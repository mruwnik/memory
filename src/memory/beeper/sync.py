"""Reading a Beeper Server: account sync, per-chat backfill and reconcile, and live events.

The WebSocket delivers at most once with no replay, so the HTTP reads here are
what make collection complete: each collected chat is read forward from its
newest cursor and backward from its oldest until Server has no more history.
Cursors are saved after each stored page, so a run cut short resumes there.
"""

import asyncio
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

# Server answers an empty older page with hasMore while it asks the bridge for
# more history; it is asked again after a wait, and a run gives up after a few.
EMPTY_PAGE_WAIT_SECONDS = 5
EMPTY_PAGE_RETRIES = 6
CHAT_PAGE_SIZE = 200


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


async def store_messages(client: beeper.BeeperClient, chat_pk: int, messages: list[beeper.Message]) -> list[str]:
    """Store each message as listed; returns what was done to each."""
    results = []
    for message in messages:
        images = [] if message.is_deleted else await fetch_images(client, chat_pk, message)
        with make_session() as session:
            chat = session.get_one(BeeperChat, chat_pk)
            results.append(ingest.store_message(session, chat, message, images))
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


async def crawl(
    client: beeper.BeeperClient,
    chat_pk: int,
    chat_id: str,
    cursor: str | None,
    direction: Literal["before", "after"],
    budget: int,
) -> tuple[int, bool]:
    """Store whole pages from ``cursor`` until Server says there are no more or
    the budget is spent. Returns (messages stored, reached the end)."""
    processed = empty = 0
    while processed < budget:
        page = await client.messages(chat_id, cursor, direction)
        # Server answers some chats' newer pages empty with hasMore, every time.
        if not page.items and direction == "after":
            return processed, True
        if not page.items and page.has_more:
            empty += 1
            if empty > EMPTY_PAGE_RETRIES:
                logger.info("Beeper chat %s: no history yet after %s empty pages", chat_pk, empty)
                return processed, False
            await asyncio.sleep(EMPTY_PAGE_WAIT_SECONDS)
            continue
        empty = 0
        await store_messages(client, chat_pk, page.items)
        processed += len(page.items)
        cursor = (page.newest_cursor if direction == "after" else page.oldest_cursor) or cursor
        save_cursor(chat_pk, direction, cursor, reached_end=not page.has_more)
        if not page.has_more:
            return processed, True
    return processed, False


async def sync_chat(chat_pk: int, budget: int) -> dict[str, Any]:
    """Read a collected chat forward from its newest cursor, then backward from
    its oldest until Server has no more history, within ``budget`` messages.
    ``done`` means both directions reached their end."""
    with make_session() as session:
        chat = session.get(BeeperChat, chat_pk)
        if chat is None:
            return skipped("unknown_chat", chat_id=chat_pk)
        if not chat.should_collect:
            return skipped("not_collected", chat_id=chat_pk)
        access = account_access(chat.network.account)
        chat_id, newest, oldest, backfilled = chat.chat_id, chat.newest_cursor, chat.oldest_cursor, chat.backfill_done
    if access is None:
        return skipped("account_unavailable", chat_id=chat_pk)

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
            processed, caught_up = await crawl(client, chat_pk, chat_id, newest, "after", budget)
        if not backfilled:
            count, backfilled = await crawl(client, chat_pk, chat_id, oldest, "before", budget - processed)
            processed += count
    return {"status": "ok", "chat_id": chat_pk, "processed": processed, "done": caught_up and backfilled}


# --- Live events ---


def listed_messages(event: dict[str, Any]) -> dict[str, beeper.Message]:
    messages = {}
    for entry in event.get("entries") or []:
        try:
            message = beeper.Message.model_validate(entry)
        except ValidationError:
            continue
        messages[message.id] = message
    return messages


async def fetch_messages(client: beeper.BeeperClient, chat_id: str, ids: list[str]) -> list[beeper.Message]:
    """The messages as Server lists them now; ones it no longer has are left out."""
    found = []
    for message_id in ids:
        try:
            found.append(await client.message(chat_id, message_id))
        except beeper.BeeperError as e:
            if e.status != 404:
                raise
    return found


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
    """Apply one WebSocket event. ``message.deleted`` also fires when a reaction
    is removed, so its messages are fetched again and deleted only when Server
    marks them deleted. ``start_sync`` names a chat to read in full."""
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
        listed = listed_messages(event) if kind == "message.upserted" else {}
        missing = [i for i in ids if i not in listed]
        messages = [listed[i] for i in ids if i in listed] + await fetch_messages(client, event["chatID"], missing)
        # A reaction row is never stored; its parent lists the reactions.
        parents = sorted({m.linked_message_id for m in messages if m.type == "REACTION" and m.linked_message_id})
        messages += await fetch_messages(client, event["chatID"], parents)
        results = await store_messages(client, chat_pk, messages)
    return {"status": "ok", "chat_id": chat_pk, "results": results, "start_sync": start_sync}
