"""
Celery tasks for Discord message processing.

This module provides tasks for:
- Storing Discord messages in the database
- Downloading and saving image attachments
- Queueing messages for embedding
"""

import hashlib
import logging
import pathlib
from datetime import datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import exc as sqlalchemy_exc
from sqlalchemy import or_

from memory.common import paths, settings
from memory.common.downloads import stream_download_to_path
from memory.common.celery_app import (
    ADD_DISCORD_MESSAGE,
    DELETE_DISCORD_MESSAGES,
    EDIT_DISCORD_MESSAGE,
    HANDLE_DISCORD_EVENT,
    UPDATE_REACTIONS,
    app,
)
from memory.common.db.connection import make_session
from memory.common.db.models import DiscordMessage, Person
from memory.common import discord_events
from memory.common.db.models.discord import DiscordBot, DiscordUser
from memory.common.people import DBSession, json_string
from memory.common.reactions import ReactionAction, Reactions, apply_reaction
from memory.common.content_processing import (
    check_content_exists,
    clear_item_chunks,
    create_task_result,
    process_content_item,
)
from memory.common.jobs import tracked_task

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
    image_dir = settings.DISCORD_STORAGE_DIR / str(message_id)
    image_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = []
    for url in image_urls:
        # SHA-256 truncated, matches parsers/html.py (was MD5 before).
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:32]
        ext = pathlib.Path(url).suffix or ".jpg"
        ext = ext.split("?")[0][:10]
        filename = f"{url_hash}{ext}"
        local_path = image_dir / filename

        if not stream_download_to_path(url, local_path, MAX_IMAGE_SIZE):
            continue
        saved_paths.append(paths.to_db_filename(local_path))

    return saved_paths


def person_for_discord_user(
    session: DBSession, discord_user: DiscordUser, by_username: bool = True
) -> Person:
    """The Person whose contact_info names this account by id (or username,
    when the username came from Discord itself), else a new Person. Never
    matched by name: the account's owner picks those."""
    names = [json_string(str(discord_user.id))]
    if by_username:
        names.append(json_string(discord_user.username))
    named = (
        session.query(Person)
        .filter(or_(*(Person.contact_info["discord"].contains(n) for n in names)))
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


def is_stale_edit(message: DiscordMessage, edited_at: datetime | None) -> bool:
    """Edits can arrive out of order; one older than the stored edit is ignored."""
    return bool(message.edited_at and edited_at and edited_at < message.edited_at)


@app.task(name=ADD_DISCORD_MESSAGE)
@tracked_task
def add_discord_message(
    bot_id: int,
    message_id: int,
    channel_id: int,
    author_id: int,
    content: str,
    sent_at: str,
    server_id: int | None = None,
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
    link_by_username: bool = True,
    backfill: bool = False,
) -> dict[str, Any]:
    """
    Add a Discord message to the database and queue for embedding.

    ``link_by_username`` is off for pushed messages, whose author names are
    only as trustworthy as the pushing bot's owner. ``backfill`` marks history
    read below what was stored, which triggers don't react to.
    """
    logger.debug(f"Adding Discord message {message_id}")

    # Include message_id in hash to ensure uniqueness
    content_hash = hashlib.sha256(f"{message_id}:{content}".encode()).digest()
    sent_at_dt = datetime.fromisoformat(sent_at.replace("Z", "+00:00"))
    edited_at_dt = (
        datetime.fromisoformat(edited_at.replace("Z", "+00:00"))
        if edited_at
        else None
    )

    # Download and save images to disk
    saved_image_paths = []
    if images:
        saved_image_paths = download_and_save_images(images, message_id)

    with make_session() as session:
        # Check if message already exists
        existing = check_content_exists(
            session, DiscordMessage, message_id=message_id
        )

        if existing and not is_edit:
            existing_msg = cast(DiscordMessage, existing)
            logger.info(f"Discord message already exists: {existing_msg.message_id}")
            return create_task_result(
                existing_msg, "already_exists", message_id=message_id
            )

        if existing and is_edit:
            # Update existing message
            existing_msg = cast(DiscordMessage, existing)
            unchanged = existing_msg.content == content and edited_at_dt in (None, existing_msg.edited_at)
            if unchanged or is_stale_edit(existing_msg, edited_at_dt):
                return create_task_result(existing_msg, "already_exists", message_id=message_id)
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
            server_id=server_id,
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
                discord_user.person = person_for_discord_user(
                    session, discord_user, by_username=link_by_username
                )
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
    if message is None or bot_ids is None or discord_events.may_change(message, bot_ids):
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
    edited_at_dt = datetime.fromisoformat(edited_at.replace("Z", "+00:00")) if edited_at else None
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
        update_reactions.apply_async(kwargs=retry_kwargs, countdown=1)  # type: ignore[attr-defined]
        return {"status": "retrying", "message_id": message_id}

    if attempt >= RETRIES:
        return {"status": "not_found", "message_id": message_id}
    update_reactions.apply_async(  # type: ignore[attr-defined]
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
            if bot_ids is None or discord_events.may_change(m, set(bot_ids))
        ]
        for message in messages:
            clear_item_chunks(message, session)
            session.delete(message)
        session.commit()
        return {"status": "deleted", "deleted": len(messages)}


REACTION_EVENTS: dict[str, ReactionAction] = {
    "MESSAGE_REACTION_ADD": "add",
    "MESSAGE_REACTION_REMOVE": "remove",
    "MESSAGE_REACTION_REMOVE_EMOJI": "clear_emoji",
    "MESSAGE_REACTION_REMOVE_ALL": "clear",
}


def store_message(bot: DiscordBot, data: dict, is_edit: bool) -> dict[str, Any]:
    with make_session() as session:
        channel = discord_events.ensure_message_entities(
            session, data, bot.id, push_only=not bot.runs_gateway
        )
        collect = channel.should_collect
        session.commit()
    if not collect:
        return {"status": "skipped", "reason": "not_collected"}
    return add_discord_message(
        **discord_events.message_task_kwargs(data, bot.id, is_edit), link_by_username=False
    )


def update_message(bot: DiscordBot, bot_ids: set[int], data: dict) -> dict[str, Any]:
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
        return store_message(bot, data, is_edit=True)
    if not stored:
        return {"status": "ignored"}
    return edit_discord_message(message_id, data["content"], data.get("edited_timestamp"), sorted(bot_ids))


@app.task(name=HANDLE_DISCORD_EVENT)
@tracked_task
def handle_discord_event(bot_id: int, event_type: str, data: dict) -> dict[str, Any]:
    """Apply one gateway event pushed to a bot's events endpoint.

    Every branch is idempotent: Discord retries deliveries and doesn't order
    them. The bot's key only proves its owner sent the event, so edits,
    deletes and reactions only touch messages that owner's bots may change.
    """
    with make_session() as session:
        bot = session.get(DiscordBot, bot_id)
        if bot is None:
            return {"status": "skipped", "reason": "unknown_bot"}
        bot_ids = discord_events.owner_bot_ids(session, bot)
        session.expunge(bot)

    if event_type == "MESSAGE_CREATE":
        return store_message(bot, data, is_edit=False)
    if event_type == "MESSAGE_UPDATE":
        return update_message(bot, bot_ids, data)
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
        with make_session() as session:
            thread = discord_events.ensure_thread(session, data, bot.id, push_only=not bot.runs_gateway)
            session.commit()
            return {"status": "updated", "channel_id": thread.id}

    return {"status": "ignored"}
