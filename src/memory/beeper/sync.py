"""Reading a Beeper Server: account sync, per-chat backfill and reconcile, and live events.

The WebSocket delivers at most once with no replay, so the HTTP reads here are
what make collection complete: each collected chat is read forward from its
newest cursor and backward from its oldest until Server has no more history.
Cursors are saved after each stored page, so a run cut short resumes there.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import ValidationError

from memory.beeper import ingest
from memory.beeper.access import account_access
from memory.common import beeper, paths, settings
from memory.common.db.connection import make_session
from memory.common.db.models import BeeperAccount, BeeperChat

logger = logging.getLogger(__name__)

CHAT_PAGE_SIZE = 200
# How a crawl stopped: Server has no more, the budget ran out, or Server has
# more but gave nothing this time (it answers an empty older page with hasMore
# while it asks the bridge for history, and a later run asks again).
Stop = Literal["end", "budget", "stalled"]
# What store_message did that is news; Server rewrites unrelated fields
# (``seen``) on every read, so a page that only repeats isn't progress.
CHANGES = frozenset({"added", "edited", "updated", "deleted"})


def skipped(reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": "skipped", "reason": reason, **extra}


# --- Accounts ---


async def all_chats(client: beeper.BeeperClient) -> list[beeper.Chat]:
    chats: list[beeper.Chat] = []
    cursor: str | None = None
    while True:
        page = await client.chats(cursor, "before" if cursor else None, CHAT_PAGE_SIZE)
        chats.extend(page.items)
        if not page.has_more or not page.oldest_cursor or page.oldest_cursor == cursor:
            return chats
        cursor = page.oldest_cursor


async def sync_account(account_id: int) -> dict[str, Any]:
    """Record the account's networks and chats; returns the collected chats to read."""
    with make_session() as session:
        access = account_access(session.get(BeeperAccount, account_id))
    if access is None:
        return skipped("account_unavailable", account_id=account_id)

    async with access.client() as client:
        networks = await client.accounts()
        chats = await all_chats(client)

    with make_session() as session:
        account = session.get_one(BeeperAccount, account_id)
        ingest.record_version(account, client.server_version)
        if beeper.self_user_id(networks) != account.beeper_user_id:
            session.commit()
            logger.warning("Beeper account %s: Server serves a different Beeper user", account_id)
            return skipped("different_user", account_id=account_id)
        ingest.refresh_networks(session, account, networks)
        rows = [row for chat in chats if (row := ingest.upsert_chat(session, account, chat)) is not None]
        account.last_reconcile_at = datetime.now(timezone.utc)
        session.commit()
        collected = [row.id for row in rows if row.should_collect]
    return {"status": "ok", "account_id": account_id, "chats": len(rows), "collected": collected}


# --- Messages ---


async def fetch_images(client: beeper.BeeperClient, chat_pk: int, message: beeper.Message) -> list[str]:
    saved = []
    for attachment in ingest.images_to_fetch(message):
        path = ingest.image_path(chat_pk, attachment)
        url = ingest.asset_url(attachment)
        if url and (path.exists() or await client.download_asset(url, path, settings.BEEPER_ATTACHMENT_MAX_BYTES)):
            saved.append(paths.to_db_filename(path))
        else:
            logger.info("Beeper chat %s: could not fetch an image of message %s", chat_pk, message.id)
    return saved


async def store_messages(
    client: beeper.BeeperClient,
    chat_pk: int,
    messages: list[beeper.Message],
    event_ids: dict[str, str] | None = None,
) -> list[str]:
    """Store each message as listed, with the Matrix event ids known for them;
    returns what was done to each."""
    results = []
    for message in messages:
        images = [] if message.is_deleted else await fetch_images(client, chat_pk, message)
        with make_session() as session:
            chat = session.get_one(BeeperChat, chat_pk)
            event_id = (event_ids or {}).get(message.id)
            results.append(ingest.store_message(session, chat, message, images, event_id))
    return results


def save_cursor(chat_pk: int, direction: Literal["before", "after"], cursor: str | None, reached_end: bool) -> None:
    with make_session() as session:
        chat = session.get_one(BeeperChat, chat_pk)
        if direction == "after":
            chat.newest_cursor = cursor
        else:
            chat.oldest_cursor = cursor
            chat.backfill_done = reached_end
        session.commit()


def save_stalls(chat_pk: int, stop: Stop, changed: int) -> tuple[int, bool]:
    """Count the backfill runs in a row that stalled; one that changed anything
    starts over. Returns the count and whether the backfill gave up."""
    with make_session() as session:
        chat = session.get_one(BeeperChat, chat_pk)
        stalls = 0 if changed else chat.backfill_stalls
        chat.backfill_stalls = stalls + 1 if stop == "stalled" else stalls
        session.commit()
        return chat.backfill_stalls, chat.backfill_stopped


async def crawl(
    client: beeper.BeeperClient,
    chat_pk: int,
    chat_id: str,
    cursor: str | None,
    direction: Literal["before", "after"],
    budget: int,
) -> tuple[int, int, Stop]:
    """Store whole pages from ``cursor`` until Server says there are no more,
    the budget is spent, or Server stalls. Returns (messages stored, how many
    of them were new or changed, why it stopped)."""
    processed = changed = 0
    while processed < budget:
        page = await client.messages(chat_id, cursor, direction)
        # Server answers some chats' newer pages empty with hasMore, every time.
        if not page.items and direction == "after":
            return processed, changed, "end"
        if not page.items and page.has_more:
            return processed, changed, "stalled"
        results = await store_messages(client, chat_pk, page.items)
        processed += len(page.items)
        changed += sum(result in CHANGES for result in results)
        moved = page.newest_cursor if direction == "after" else page.oldest_cursor
        if page.has_more and moved in (None, cursor):
            logger.warning("Beeper chat %s: Server's %s cursor didn't move", chat_pk, direction)
            return processed, changed, "end" if direction == "after" else "stalled"
        cursor = moved or cursor
        save_cursor(chat_pk, direction, cursor, reached_end=not page.has_more)
        if not page.has_more:
            return processed, changed, "end"
    return processed, changed, "budget"


async def sync_chat(chat_pk: int, budget: int, retry_stopped: bool = False) -> dict[str, Any]:
    """Read a collected chat forward from its newest cursor, then backward from
    its oldest until Server has no more history, within ``budget`` messages.
    ``done`` means both directions reached their end, or the backfill gave up
    after too many stalled runs; ``stalls`` counts those runs. A backfill that
    gave up is tried again only with ``retry_stopped``."""
    with make_session() as session:
        chat = session.get(BeeperChat, chat_pk)
        if chat is None:
            return skipped("unknown_chat", chat_id=chat_pk)
        if not chat.should_collect:
            return skipped("not_collected", chat_id=chat_pk)
        access = account_access(chat.network.account)
        chat_id, newest, oldest = chat.chat_id, chat.newest_cursor, chat.oldest_cursor
        backfilled, stopped, stalls = chat.backfill_done, chat.backfill_stopped, chat.backfill_stalls
    if access is None:
        return skipped("account_unavailable", chat_id=chat_pk)

    stop: Stop = "end"
    async with access.client() as client:
        if newest is None:
            # First read: the latest page sets both cursors.
            page = await client.messages(chat_id)
            await store_messages(client, chat_pk, page.items)
            processed, caught_up = len(page.items), True
            save_cursor(chat_pk, "after", page.newest_cursor, reached_end=True)
            save_cursor(chat_pk, "before", page.oldest_cursor, reached_end=not page.has_more)
            oldest, backfilled = page.oldest_cursor, not page.has_more
        else:
            processed, _, forward = await crawl(client, chat_pk, chat_id, newest, "after", budget)
            caught_up = forward == "end"
        if not backfilled and (retry_stopped or not stopped):
            count, changed, stop = await crawl(client, chat_pk, chat_id, oldest, "before", budget - processed)
            processed += count
            stalls, stopped = save_stalls(chat_pk, stop, changed)
    backfill_over = backfilled or stopped or stop == "end"
    return {
        "status": "ok",
        "chat_id": chat_pk,
        "processed": processed,
        "done": caught_up and backfill_over,
        "stalls": stalls if stop == "stalled" else 0,
    }


# --- Live events ---


def listed_messages(event: dict[str, Any], ids: list[str]) -> dict[str, beeper.Message]:
    """The event's entries by the id the event names each with. An id may be
    the entry's Matrix event id, which only position ties to an entry, and
    nothing confirms the order of a longer batch. So a lone entry takes its id;
    otherwise entries are keyed by their own ids and the caller fetches the
    ids left over."""
    entries = event.get("entries") or []
    in_order = len(ids) == len(entries) and (
        len(entries) == 1 or all(i == e.get("id") for i, e in zip(ids, entries))
    )
    named = zip(ids, entries) if in_order else ((e.get("id"), e) for e in entries)
    messages = {}
    for name, entry in named:
        try:
            message = beeper.Message.model_validate(entry)
        except ValidationError:
            continue
        messages[str(name or message.id)] = message
    return messages


async def fetch_messages(
    client: beeper.BeeperClient, chat_id: str, ids: list[str]
) -> tuple[dict[str, beeper.Message], list[str]]:
    """The messages as Server lists them now, by the id asked for, and the ids it no longer has."""
    found, gone = {}, []
    for message_id in ids:
        try:
            found[message_id] = await client.message(chat_id, message_id)
        except beeper.BeeperError as e:
            if e.status != 404:
                raise
            gone.append(message_id)
    return found, gone


def forget_messages(chat_pk: int, ids: list[str]) -> set[str]:
    """Delete what Server no longer has; returns the parents of reactions among them."""
    with make_session() as session:
        return {parent for i in ids if (parent := ingest.forget_message(session, chat_pk, i))}


async def event_chat(
    client: beeper.BeeperClient, account_id: int, event: dict[str, Any]
) -> tuple[int, bool, bool] | None:
    """Record the event's chat if it's new or changed; returns (chat pk,
    collected, never read yet), or None when it isn't one of this account's."""
    chat_id = event["chatID"]
    with make_session() as session:
        chat = ingest.find_chat(session, account_id, chat_id)
        if chat is not None and event["type"] != "chat.upserted":
            return chat.id, chat.should_collect, chat.newest_cursor is None
    listed = [c for e in event.get("entries") or [] if (c := as_chat(e)) and c.id == chat_id]
    data = listed[0] if listed else await client.chat(chat_id)
    with make_session() as session:
        account = session.get_one(BeeperAccount, account_id)
        chat = ingest.upsert_chat(session, account, data)
        session.commit()
        if chat is None:
            return None
        return chat.id, chat.should_collect, chat.newest_cursor is None


def as_chat(entry: dict[str, Any]) -> beeper.Chat | None:
    try:
        return beeper.Chat.model_validate(entry)
    except ValidationError:
        return None


async def handle_event(account_id: int, event: dict[str, Any]) -> dict[str, Any]:
    """Apply one WebSocket event. ``message.deleted`` also fires, naming the
    parent's event id, when a reaction is removed, so its messages are fetched
    again and deleted only when Server marks them deleted or no longer has them.
    ``start_sync`` names a chat to read in full."""
    with make_session() as session:
        access = account_access(session.get(BeeperAccount, account_id))
        if access is not None:
            session.get_one(BeeperAccount, account_id).last_event_at = datetime.now(timezone.utc)
            session.commit()
    if access is None:
        return skipped("account_unavailable", account_id=account_id)
    kind = event.get("type")
    if kind not in ("chat.upserted", "message.upserted", "message.deleted") or not event.get("chatID"):
        return {"status": "ignored"}

    async with access.client() as client:
        found = await event_chat(client, account_id, event)
        if found is None:
            return skipped("unknown_network")
        chat_pk, collected, unread = found
        start_sync = chat_pk if collected and unread else None
        if not collected:
            return skipped("not_collected", chat_id=chat_pk)
        if kind == "chat.upserted":
            return {"status": "ok", "chat_id": chat_pk, "start_sync": start_sync}

        ids = [str(i) for i in event.get("ids") or []]
        listed = listed_messages(event, ids) if kind == "message.upserted" else {}
        fetched, gone = await fetch_messages(client, event["chatID"], [i for i in ids if i not in listed])
        named = {**listed, **fetched}
        messages = [named[i] for i in ids if i in named]
        # Server takes a Matrix event id wherever it takes a message id, but
        # only the WebSocket ever says which event a message is.
        event_ids = {m.id: i for i, m in named.items() if i != m.id and i.startswith("$")}
        # A delete for a message Server has forgotten is a delete.
        refresh = forget_messages(chat_pk, gone) if kind == "message.deleted" else set()
        # A reaction row is never stored; its parent lists the reactions.
        refresh |= {m.linked_message_id for m in messages if m.type == "REACTION" and m.linked_message_id}
        parents, _ = await fetch_messages(client, event["chatID"], sorted(refresh))
        results = await store_messages(client, chat_pk, messages + list(parents.values()), event_ids)
    return {"status": "ok", "chat_id": chat_pk, "results": results, "start_sync": start_sync}
