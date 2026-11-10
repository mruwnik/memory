"""
Celery tasks for Discord message processing.

This module provides tasks for:
- Storing Discord messages in the database
- Downloading and saving image attachments
- Queueing messages for embedding
"""

import asyncio
import hashlib
import logging
import pathlib
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import BigInteger, or_
from sqlalchemy import cast as sql_cast
from sqlalchemy import exc as sqlalchemy_exc
from sqlalchemy import func as sql_func

from memory.common import paths, settings
from memory.common.downloads import stream_download_to_path
from memory.common.celery_app import (
    ADD_DISCORD_MESSAGE,
    DELETE_DISCORD_MESSAGES,
    EDIT_DISCORD_MESSAGE,
    HANDLE_DISCORD_EVENT,
    UPDATE_REACTIONS,
    VERIFY_DISCORD_MESSAGES,
    app,
)
from memory.common.db.connection import make_session
from memory.common.db.models import DiscordMessage, Person
from memory.common import discord_events
from memory.common.db.models.discord import DM_CHANNEL_TYPES, DiscordBot, DiscordChannel, DiscordUser
from memory.common.db.models.events import CAUSING_EVENT, reject_raw_event
from memory.common.people import DBSession, json_string
from memory.common.reactions import ReactionAction, Reactions, apply_reaction
from memory.common.content_processing import (
    clear_item_chunks,
    create_task_result,
    process_content_item,
)
from memory.common.jobs import tracked_task
from memory.discord.rest import BotAccess, DiscordAPIError, DiscordRest, bot_access, token_channel

logger = logging.getLogger(__name__)


# Max image size to download (50 MB)
MAX_IMAGE_SIZE = 50 * 1024 * 1024


def safe_process_discord_message(
    message: DiscordMessage,
    session,
    *,
    message_id: int,
    operation: str,
    backfill: bool = False,
) -> dict[str, Any]:
    """``process_content_item``, treating a lost insert race as the message
    already being stored. The IntegrityError text holds the row's content, so
    only ids are logged."""
    try:
        return process_content_item(message, session, backfill=backfill)
    except sqlalchemy_exc.IntegrityError:
        session.rollback()
        logger.info("Discord message %s already stored while %s it", message_id, operation)
        return {"status": "already_exists", "message_id": message_id}


def download_and_save_images(image_urls: list[str], message_id: int) -> list[str]:
    """Download images from URLs and save to disk. Returns relative file paths.

    Filenames are SHA-256 truncated to match the convention in
    parsers/html.py — pre-fix this site used MD5, an inconsistency that
    showed the three sites had drifted independently.
    """
    (settings.DISCORD_STORAGE_DIR / str(message_id)).mkdir(parents=True, exist_ok=True)
    saved_paths = []
    for url in image_urls:
        local_path = image_path(url, message_id)
        if not stream_download_to_path(url, local_path, MAX_IMAGE_SIZE):
            continue
        saved_paths.append(paths.to_db_filename(local_path))

    return saved_paths


def image_path(url: str, message_id: int) -> pathlib.Path:
    """Named by the URL without its query: Discord's CDN signs attachment URLs
    with a query that changes over time, while the path (channel, attachment
    id, filename) stays the same."""
    unsigned = url.split("?")[0]
    # SHA-256 truncated, matches parsers/html.py.
    url_hash = hashlib.sha256(unsigned.encode()).hexdigest()[:32]
    ext = (pathlib.Path(unsigned).suffix or ".jpg")[:10]
    return settings.DISCORD_STORAGE_DIR / str(message_id) / f"{url_hash}{ext}"


def person_for_discord_user(session: DBSession, discord_user: DiscordUser) -> Person:
    """The Person whose contact_info names this account by id, else a new
    Person. Never matched by username or name: a username can be taken by
    another account after a rename."""
    stored = Person.contact_info["discord"]
    named = (
        session.query(Person)
        .filter(or_(
            stored.contains(json_string(str(discord_user.id))),
            stored.contains(sql_func.to_jsonb(sql_cast(discord_user.id, BigInteger))),
        ))
        .order_by(Person.id)
        .first()
    )
    if named:
        return named
    identifier = f"discord_{discord_user.id}"
    if session.query(Person.id).filter(Person.identifier == identifier).first() is not None:
        identifier = f"{identifier}_{uuid4().hex}"
    person = Person(
        identifier=identifier,
        display_name=discord_user.name,
        contact_info={"discord": str(discord_user.id)},
    )
    session.add(person)
    return person


def parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def is_stale_edit(message: DiscordMessage, edited_at: datetime | None) -> bool:
    """Edits can arrive out of order; one older than the stored edit is ignored."""
    return bool(message.edited_at and edited_at and edited_at < message.edited_at)


def changes(
    message: DiscordMessage, channel_id: int, is_edit: bool, content: str, edited_at: datetime | None
) -> bool:
    """Whether storing this over the stored message changes it: only an edit
    in its channel does (a message never moves; one naming another channel is
    someone's forgery), and not one that is unchanged or older than the stored edit."""
    unchanged = message.content == content and edited_at in (None, message.edited_at)
    return is_edit and message.channel_id == channel_id and not unchanged and not is_stale_edit(message, edited_at)


@app.task(name=ADD_DISCORD_MESSAGE)
@tracked_task
def add_discord_message(
    bot_id: int,
    message_id: int,
    channel_id: int,
    author_id: int,
    content: str,
    sent_at: str,
    edited_at: str | None = None,
    reply_to_message_id: int | None = None,
    thread_id: int | None = None,
    message_type: str = "default",
    is_pinned: bool = False,
    images: list[str] | None = None,
    embeds: list[dict] | None = None,
    attachments: list[dict] | None = None,
    reactions: Reactions | None = None,
    is_edit: bool = False,
    backfill: bool = False,
) -> dict[str, Any]:
    """
    Add a Discord message to the database and queue for embedding.

    ``backfill`` marks history read below what was stored, which subscriptions
    don't react to. A new message is filed under its channel's server as stored
    now: the channel may have moved since the message was queued.
    """
    logger.debug(f"Adding Discord message {message_id}")

    # Include message_id in hash to ensure uniqueness
    content_hash = hashlib.sha256(f"{message_id}:{content}".encode()).digest()
    sent_at_dt = datetime.fromisoformat(sent_at.replace("Z", "+00:00"))
    edited_at_dt = parse_time(edited_at)

    # Images are fetched only for a message this call stores or edits: they
    # live under the message id, which a push may name for someone else's message.
    with make_session() as session:
        existing = find_message(session, message_id, None)
        if existing and not changes(existing, channel_id, is_edit, content, edited_at_dt):
            return create_task_result(existing, "already_exists", message_id=message_id)
    saved_image_paths = download_and_save_images(images, message_id) if images else []

    # Checked again, on the locked row: the message may have been stored while the images downloaded.
    with make_session() as session:
        existing = find_message(session, message_id, None, lock=True)
        if existing and not changes(existing, channel_id, is_edit, content, edited_at_dt):
            return create_task_result(existing, "already_exists", message_id=message_id)

        if existing:
            existing_msg = existing
            existing_msg.content = content  # type: ignore
            if edited_at_dt:
                existing_msg.edited_at = edited_at_dt
            if saved_image_paths:
                existing_msg.images = saved_image_paths  # type: ignore
            if embeds is not None:
                existing_msg.embeds = embeds  # type: ignore
            if attachments is not None:
                existing_msg.attachments = attachments  # type: ignore
            if reactions is not None:
                existing_msg.reactions = reactions  # type: ignore

            return safe_process_discord_message(
                existing_msg,
                session,
                message_id=message_id,
                operation="editing",
                backfill=backfill,
            )

        # Create new message
        discord_message = DiscordMessage(
            modality="message",
            sha256=content_hash,
            content=content,
            bot_id=bot_id,
            message_id=message_id,
            channel_id=channel_id,
            server_id=session.get_one(DiscordChannel, channel_id).server_id,
            author_id=author_id,
            sent_at=sent_at_dt,
            edited_at=edited_at_dt,
            reply_to_message_id=reply_to_message_id,
            thread_id=thread_id,
            message_type=message_type,
            is_pinned=is_pinned,
            images=saved_image_paths or None,
            embeds=embeds,
            attachments=attachments,
            reactions=reactions,
        )

        if discord_user := session.get(DiscordUser, author_id):
            if not discord_user.person:
                discord_user.person = person_for_discord_user(session, discord_user)
                session.flush()
            if discord_user.person not in discord_message.people:
                discord_message.people.append(discord_user.person)

        return safe_process_discord_message(
            discord_message,
            session,
            message_id=message_id,
            operation="adding",
            backfill=backfill,
        )


def find_message(session, message_id: int, bot_ids: set[int] | None, lock: bool = False) -> DiscordMessage | None:
    """The stored message, if these bots may change it (any bots when None).
    Locks only the discord_message row, so concurrent updates queue on it alone."""
    query = session.query(DiscordMessage).filter(DiscordMessage.message_id == message_id)
    if lock:
        query = query.with_for_update(of=DiscordMessage.__table__)
    message = query.first()
    if message is None or bot_ids is None or discord_events.may_change(session, message, bot_ids):
        return message
    logger.info("Discord message %s not changed: it belongs to another owner", message_id)
    return None


@app.task(name=EDIT_DISCORD_MESSAGE)
@tracked_task
def edit_discord_message(
    message_id: int,
    content: str,
    edited_at: str | None = None,
    bot_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Edit a stored message's content. An edit older than the stored one is
    ignored; without an edit time the stored one stays."""
    edited_at_dt = parse_time(edited_at)
    with make_session() as session:
        message = find_message(session, message_id, set(bot_ids) if bot_ids is not None else None)
        if message is None:
            return {"status": "error", "error": "Message not found", "message_id": message_id}
        if is_stale_edit(message, edited_at_dt):
            return create_task_result(message, "already_exists", message_id=message_id)
        message.content = content  # type: ignore
        if edited_at_dt:
            message.edited_at = edited_at_dt
        return safe_process_discord_message(message, session, message_id=message_id, operation="editing")


# A reaction can be processed before its message is stored; retry a few times
# before dropping it.
RETRIES = 3
RETRY_SECONDS = 30


@app.task(name=UPDATE_REACTIONS)
@tracked_task
def update_reactions(
    message_id: int,
    action: ReactionAction,
    emoji: str | None = None,
    user_id: str | None = None,
    attempt: int = 0,
    bot_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Apply one reaction event to a Discord message, as a per-user set
    operation on the locked row. ``bot_ids`` limits it to messages those bots may change."""
    retry_kwargs = {
        "message_id": message_id,
        "action": action,
        "emoji": emoji,
        "user_id": user_id,
        "attempt": attempt + 1,
        "bot_ids": bot_ids,
    }
    try:
        with make_session() as session:
            exists = session.query(DiscordMessage.id).filter(DiscordMessage.message_id == message_id).first()
            message = find_message(session, message_id, set(bot_ids) if bot_ids is not None else None, lock=True)
            if message:
                message.reactions = apply_reaction(message.reactions, action, emoji, user_id)
                session.commit()
                return {"status": "updated", "message_id": message_id}
            if exists:
                return {"status": "ignored", "message_id": message_id}
    except sqlalchemy_exc.OperationalError:
        logger.info("Reaction update for Discord message %s hit a lock conflict; retrying", message_id)
        update_reactions.apply_async(kwargs=retry_kwargs, countdown=1)
        return {"status": "retrying", "message_id": message_id}

    if attempt >= RETRIES:
        return {"status": "not_found", "message_id": message_id}
    update_reactions.apply_async(
        kwargs=retry_kwargs, countdown=RETRY_SECONDS * (attempt + 1)
    )
    return {"status": "retrying", "message_id": message_id}


@app.task(name=DELETE_DISCORD_MESSAGES)
@tracked_task
def delete_discord_messages(message_ids: list[int], bot_ids: list[int] | None = None) -> dict[str, Any]:
    """Delete stored Discord messages with their chunks; unknown ids, and with
    ``bot_ids`` messages those bots may not change, are skipped.

    A delete processed before its create finds nothing, and the later create
    stores the message.
    """
    with make_session() as session:
        messages = [
            m
            for m in session.query(DiscordMessage).filter(DiscordMessage.message_id.in_(message_ids)).all()
            if bot_ids is None or discord_events.may_change(session, m, set(bot_ids))
        ]
        for message in messages:
            clear_item_chunks(message, session)
            session.delete(message)
        session.commit()
        return {"status": "deleted", "deleted": len(messages)}


# Discord's error code for a message that doesn't exist; 404s for an unknown channel keep the messages.
UNKNOWN_MESSAGE = 10008


async def read_messages(
    access: BotAccess, targets: list[tuple[int, int, int]]
) -> tuple[dict[int, dict | None], DiscordAPIError | None]:
    """Each (item id, channel id, message id) as the token reads it, in
    order: None for one Discord says doesn't exist, left out for one it
    can't reach, and with it every other one in that channel. Reading stops
    at the first other error (a rejected token, rate limit, server error or
    dropped connection), which is returned with what was read before it."""
    read: dict[int, dict | None] = {}
    unreachable: set[int] = set()
    async with DiscordRest(access) as rest:
        for item_id, channel_id, message_id in targets:
            if channel_id in unreachable:
                continue
            try:
                read[item_id] = await rest.get(f"/channels/{channel_id}/messages/{message_id}")
            except DiscordAPIError as e:
                if e.code == UNKNOWN_MESSAGE:
                    read[item_id] = None
                elif e.unreachable:
                    unreachable.add(channel_id)
                else:
                    return read, e
    return read, None


def rewrite_message(session: DBSession, message: DiscordMessage, data: dict) -> bool:
    """Make a stored message what Discord says it is; whether it differed.
    Reactions are cleared either way, as a message read doesn't say who
    reacted. Images are downloaded again only when their attachments differ."""
    kwargs = discord_events.message_task_kwargs(data, bot_id=0)
    images = [paths.to_db_filename(image_path(url, message.message_id)) for url in kwargs["images"] or []]
    fields = {
        "content": kwargs["content"],
        "author_id": kwargs["author_id"],
        "sent_at": parse_time(kwargs["sent_at"]),
        "edited_at": parse_time(kwargs["edited_at"]),
        "reply_to_message_id": kwargs["reply_to_message_id"],
        "thread_id": kwargs["thread_id"],
        "message_type": kwargs["message_type"],
        "is_pinned": kwargs["is_pinned"],
        "images": images or None,
        "embeds": kwargs["embeds"],
        "attachments": kwargs["attachments"],
    }
    message.reactions = None
    if all(getattr(message, field) == value for field, value in fields.items()):
        return False
    if fields["images"] != message.images:
        fields["images"] = kwargs["images"] and download_and_save_images(kwargs["images"], message.message_id) or None
    author = discord_events.ensure_user(session, data["author"])
    if not author.person:
        author.person = person_for_discord_user(session, author)
    for field, value in fields.items():
        setattr(message, field, value)
    message.people = [author.person]
    return True


@app.task(name=VERIFY_DISCORD_MESSAGES, bind=True, max_retries=6)
@tracked_task
def verify_discord_messages(self, bot_id: int, item_ids: list[int]) -> dict[str, Any]:
    """Check stored messages with this bot's token: one Discord says doesn't
    exist is deleted, one it reads otherwise is rewritten as it reads it, and
    one the token can't reach stays as it is. Without API access (paused, no
    token, or a token Discord rejects, which isn't retried: rejections count
    towards Discord's per-IP limit) the check is skipped, and logged. A rate
    limit, server error or dropped connection keeps what was read and
    retries the rest with backoff; only the last attempt alerts."""
    access = access_of(bot_id)
    if access is None:
        logger.warning("Discord bot %s has no API access: %s disowned messages left unchecked", bot_id, len(item_ids))
        return {"status": "skipped", "reason": "no_access"}
    with make_session() as session:
        rows = (
            session.query(DiscordMessage.id, DiscordMessage.channel_id, DiscordMessage.message_id)
            .filter(DiscordMessage.id.in_(item_ids))
            .order_by(DiscordMessage.id)
        )
        targets = [(row.id, row.channel_id, row.message_id) for row in rows]
    read, stopped = asyncio.run(read_messages(access, targets))
    deleted, rewritten = apply_reads(read)
    if stopped is not None and stopped.status == 401:
        logger.warning("Discord bot %s: token rejected, %s disowned messages left unchecked", bot_id, len(targets) - len(read))
        return {"status": "skipped", "reason": "unauthorized"}
    if stopped is not None:
        remaining = [item_id for item_id, *_ in targets if item_id not in read]
        countdown = min(60 * 2**self.request.retries, 3600)
        raise self.retry(exc=stopped, args=[bot_id, remaining], countdown=countdown)
    return {"status": "verified", "deleted": deleted, "rewritten": rewritten}


def apply_reads(read: dict[int, dict | None]) -> tuple[int, int]:
    """Delete or rewrite each stored message as it was read; the counts of both."""
    deleted = rewritten = 0
    for item_id, data in read.items():
        with make_session() as session:
            message = session.get(DiscordMessage, item_id)
            if message is None:
                continue
            if data is None:
                clear_item_chunks(message, session)
                session.delete(message)
                session.commit()
                deleted += 1
            elif rewrite_message(session, message, data):
                safe_process_discord_message(message, session, message_id=message.message_id, operation="verifying")
                rewritten += 1
            else:
                session.commit()
    return deleted, rewritten


REACTION_EVENTS: dict[str, ReactionAction] = {
    "MESSAGE_REACTION_ADD": "add",
    "MESSAGE_REACTION_REMOVE": "remove",
    "MESSAGE_REACTION_REMOVE_EMOJI": "clear_emoji",
    "MESSAGE_REACTION_REMOVE_ALL": "clear",
}


# Discord's channel types for a DM and a group DM.
DM_TYPES = frozenset({1, 3})


def access_of(bot_id: int) -> BotAccess | None:
    with make_session() as session:
        return bot_access(session.get(DiscordBot, bot_id))


def read_channel(bot_id: int, channel_id: int) -> dict | None:
    """The channel, as the bot's own token reads it."""
    access = access_of(bot_id)
    return asyncio.run(token_channel(access, channel_id)) if access else None


def read_dm(bot_id: int, channel_id: int) -> dict | None:
    channel = read_channel(bot_id, channel_id)
    return channel if channel is not None and channel.get("type") in DM_TYPES else None


def read_server_channel(bot_id: int, channel_id: int, guild_id: int) -> dict | None:
    """The channel, if the bot's token reads it as one in this server."""
    channel = read_channel(bot_id, channel_id)
    if channel is None or discord_events.snowflake(channel.get("guild_id")) != guild_id:
        return None
    return channel


def dm_name(dm: dict) -> str | None:
    recipients = dm.get("recipients") or []
    if dm.get("name") or not recipients:
        return dm.get("name")
    return f"DM with {recipients[0].get('username') or recipients[0]['id']}"


def store_message(bot: DiscordBot, bot_ids: set[int], data: dict, is_edit: bool, pushed: bool) -> dict[str, Any]:
    """Store a pushed or gateway message. A push is held to
    ``discord_events.push_refusal``; a DM it names is stored only once the
    bot's token reads the channel as its DM, and a new one only from someone
    who shares a server with the bot; a new server channel only once the
    token reads it in the pushed server, and as the token lists it. A push
    naming no server is about its stored channel's server, if that has one:
    only unknown channel ids cost a REST call. The gateway starts DMs
    itself, so an edit it relays never does. The message is filed under its
    stored channel's server, never the one a payload names."""
    channel_id = int(data["channel_id"])
    guild_id = discord_events.snowflake(data.get("guild_id"))
    with make_session() as session:
        existing = session.get(DiscordChannel, channel_id)
        new = existing is None
        if pushed and guild_id is None and existing is not None and existing.server_id is not None:
            guild_id = existing.server_id
            data = data | {"guild_id": str(guild_id)}
        # Checked before the token is asked as well as after: a refused push mustn't cost a REST call.
        refusal = pushed and guild_id is not None and discord_events.push_refusal(session, channel_id, guild_id, bot_ids)
    if refusal:
        return {"status": "skipped", "reason": refusal}
    if guild_id is None and new and not pushed:
        return {"status": "skipped", "reason": "not_collected"}
    dm = read_dm(bot.id, channel_id) if pushed and guild_id is None and new else None
    if new and guild_id is None and dm is None:
        return {"status": "skipped", "reason": "unconfirmed_dm"}
    listed = read_server_channel(bot.id, channel_id, guild_id) if pushed and guild_id is not None and new else None
    if pushed and guild_id is not None and new and listed is None:
        return {"status": "skipped", "reason": "unconfirmed_channel"}
    with make_session() as session:
        if dm is not None:
            recipients = [int(r["id"]) for r in dm.get("recipients") or []]
            if new and not discord_events.knows_user(session, bot.id, recipients):
                return {"status": "skipped", "reason": "unknown_sender"}
            discord_events.claim_dm(
                session, channel_id, bot, name=dm_name(dm), channel_type=discord_events.CHANNEL_TYPE_NAMES[dm["type"]],
            )
        if pushed and (refusal := discord_events.push_refusal(session, channel_id, guild_id, bot_ids)):
            return {"status": "skipped", "reason": refusal}
        channel = discord_events.ensure_message_entities(
            session, data, bot.id, push_only=not bot.runs_gateway, trusted=not pushed, listed=listed
        )
        if channel.is_direct and channel.bot_id not in bot_ids:
            return {"status": "skipped", "reason": "not_owner"}
        collect = channel.should_collect
        session.commit()
    if not collect:
        return {"status": "skipped", "reason": "not_collected"}
    return add_discord_message(**discord_events.message_task_kwargs(data, bot.id, is_edit))


def update_message(bot: DiscordBot, bot_ids: set[int], data: dict, pushed: bool) -> dict[str, Any]:
    """A MESSAGE_UPDATE: an edit when it carries content. An update for a
    message not stored yet stores it if the payload is complete, else is dropped."""
    if "content" not in data:
        return {"status": "ignored"}
    message_id = int(data["id"])
    with make_session() as session:
        stored = session.query(DiscordMessage.id).filter(DiscordMessage.message_id == message_id).first()
        if stored and find_message(session, message_id, bot_ids) is None:
            return {"status": "ignored"}
    if discord_events.is_full_message(data):
        return store_message(bot, bot_ids, data, is_edit=True, pushed=pushed)
    if not stored:
        return {"status": "ignored"}
    return edit_discord_message(message_id, data["content"], data.get("edited_timestamp"), sorted(bot_ids))


# What a push is refused for; other skips (not collected, too short to embed) store or drop it as Memory would anyway.
PUSH_REFUSALS = frozenset(
    {
        "guild_mismatch", "no_server", "not_owner", "not_member",
        "held_by_other", "unconfirmed_dm", "unconfirmed_channel", "malformed",
    }
)


@app.task(name=HANDLE_DISCORD_EVENT)
@tracked_task
def handle_discord_event(bot_id: int, event_type: str, data: dict, pushed: bool = True) -> dict[str, Any]:
    """Apply one gateway event, ``pushed`` to a bot's events endpoint unless
    the gateway collector says it relayed it.

    Every branch is idempotent: Discord retries deliveries and doesn't order
    them. The bot's key only proves its owner sent a pushed event, so edits,
    deletes and reactions only touch messages that owner's bots may change.
    A push it refuses (``PUSH_REFUSALS``) marks its delivery rejected.
    """
    result = apply_discord_event(bot_id, event_type, data, pushed)
    if pushed and result.get("status") == "skipped" and result.get("reason") in PUSH_REFUSALS:
        with make_session() as session:
            reject_raw_event(session, CAUSING_EVENT.get())
            session.commit()
    return result


def message_ids(event_type: str, data: dict) -> list[int]:
    """The messages an event is about."""
    if event_type == "MESSAGE_DELETE_BULK":
        return [int(i) for i in data.get("ids", [])]
    if event_type in REACTION_EVENTS:
        return [int(data["message_id"])]
    if event_type in ("MESSAGE_CREATE", "MESSAGE_UPDATE", "MESSAGE_DELETE"):
        return [int(data["id"])]
    return []


def apply_discord_event(bot_id: int, event_type: str, data: dict, pushed: bool) -> dict[str, Any]:
    if not discord_events.well_formed(data):
        return {"status": "skipped", "reason": "malformed"}
    with make_session() as session:
        bot = session.get(DiscordBot, bot_id)
        if bot is None:
            return {"status": "skipped", "reason": "unknown_bot"}
        bot_ids = discord_events.owner_bot_ids(session, bot)
        session.expunge(bot)
        if pushed and discord_events.held_by_other(session, message_ids(event_type, data), bot_ids):
            return {"status": "skipped", "reason": "held_by_other"}

    if event_type == "MESSAGE_CREATE":
        return store_message(bot, bot_ids, data, is_edit=False, pushed=pushed)
    if event_type == "MESSAGE_UPDATE":
        return update_message(bot, bot_ids, data, pushed)
    if event_type == "MESSAGE_DELETE":
        return delete_discord_messages([int(data["id"])], sorted(bot_ids))
    if event_type == "MESSAGE_DELETE_BULK":
        return delete_discord_messages([int(i) for i in data.get("ids", [])], sorted(bot_ids))

    if action := REACTION_EVENTS.get(event_type):
        user_id = data.get("user_id")
        return update_reactions(
            int(data["message_id"]),
            action,
            emoji=discord_events.emoji_key(data.get("emoji")),
            user_id=str(user_id) if user_id is not None else None,
            bot_ids=sorted(bot_ids),
        )

    if event_type in ("THREAD_CREATE", "THREAD_UPDATE"):
        if discord_events.CHANNEL_TYPE_NAMES.get(data.get("type", 11)) in DM_CHANNEL_TYPES:
            return {"status": "skipped", "reason": "no_server"}
        return store_thread(bot, bot_ids, data, pushed)

    return {"status": "ignored"}


def store_thread(bot: DiscordBot, bot_ids: set[int], data: dict, pushed: bool) -> dict[str, Any]:
    """A THREAD_CREATE or THREAD_UPDATE. A push is held to
    ``discord_events.push_refusal``, and creates a thread only once the bot's
    token reads it in the pushed server, and then as the token lists it."""
    thread_id, guild_id = int(data["id"]), discord_events.snowflake(data.get("guild_id"))
    listed = None
    if pushed and guild_id is not None:
        with make_session() as session:
            new = session.get(DiscordChannel, thread_id) is None
            refusal = discord_events.push_refusal(
                session, thread_id, guild_id, bot_ids, parent_id=discord_events.snowflake(data.get("parent_id")),
            )
        if refusal:
            return {"status": "skipped", "reason": refusal}
        listed = read_server_channel(bot.id, thread_id, guild_id) if new else None
        if new and listed is None:
            return {"status": "skipped", "reason": "unconfirmed_channel"}
    data = listed or data
    with make_session() as session:
        refusal = pushed and discord_events.push_refusal(
            session, thread_id, guild_id, bot_ids, parent_id=discord_events.snowflake(data.get("parent_id")),
        )
        if refusal:
            return {"status": "skipped", "reason": refusal}
        thread = discord_events.ensure_thread(
            session, data, bot.id, push_only=not bot.runs_gateway, trusted=not pushed or listed is not None
        )
        session.commit()
        return {"status": "updated", "channel_id": thread.id}
