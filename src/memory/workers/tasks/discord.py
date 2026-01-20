"""
Celery tasks for Discord message processing.
"""

import hashlib
import logging
import pathlib
import re
import textwrap
from datetime import datetime
from typing import Any, cast

import requests
from sqlalchemy import exc as sqlalchemy_exc

from memory.common import discord, settings
from memory.common.celery_app import (
    ADD_DISCORD_MESSAGE,
    EDIT_DISCORD_MESSAGE,
    PROCESS_DISCORD_MESSAGE,
    app,
)
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import DiscordMessage, DiscordUser
from memory.discord.messages import call_llm, comm_channel_prompt, send_discord_response
from memory.common.content_processing import (
    check_content_exists,
    create_task_result,
    process_content_item,
    safe_task_execution,
)

logger = logging.getLogger(__name__)


def download_and_save_images(image_urls: list[str], message_id: int) -> list[str]:
    """Download images from URLs and save to disk. Returns relative file paths."""
    image_dir = settings.DISCORD_STORAGE_DIR / str(message_id)
    image_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = []
    for url in image_urls:
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            # Generate filename
            url_hash = hashlib.md5(url.encode()).hexdigest()
            ext = pathlib.Path(url).suffix or ".jpg"
            ext = ext.split("?")[0]
            filename = f"{url_hash}{ext}"
            local_path = image_dir / filename

            # Save image
            local_path.write_bytes(response.content)

            # Store relative path from FILE_STORAGE_DIR
            relative_path = local_path.relative_to(settings.FILE_STORAGE_DIR)
            saved_paths.append(str(relative_path))

        except Exception as e:
            logger.error(f"Failed to download/save image from {url}: {e}")

    return saved_paths


def get_prev(
    session: DBSession, channel_id: int, sent_at: datetime
) -> list[str]:
    prev = (
        session.query(DiscordUser.username, DiscordMessage.content)
        .join(DiscordUser, DiscordMessage.from_id == DiscordUser.id)
        .filter(
            DiscordMessage.channel_id == channel_id,
            DiscordMessage.sent_at < sent_at,
        )
        .order_by(DiscordMessage.sent_at.desc())
        .limit(settings.DISCORD_CONTEXT_WINDOW)
        .all()
    )
    return [f"{msg.username}: {msg.content}" for msg in prev[::-1]]


def should_process(message: DiscordMessage) -> bool:
    if not (
        settings.DISCORD_PROCESS_MESSAGES
        and settings.DISCORD_NOTIFICATIONS_ENABLED
        and not message.ignore_messages
    ):
        return False

    if (
        message.recipient_user
        and message.content
        and f"<@{message.recipient_user.id}>" in message.content
    ):
        logger.info("Direct mention of the bot, processing message")
        return True

    if message.from_user == message.recipient_user:
        logger.info("Skipping message because from_user == recipient_user")
        return False

    if not message.recipient_user or not message.from_user:
        return False

    with make_session() as session:
        msg = textwrap.dedent("""
            Should you continue the conversation with the user?
            Please return a number between 0 and 100 indicating how much you want to continue the conversation (0 is no, 100 is yes).
            Please return the number in the following format:

            <response>
                <number>50</number>
                <reason>I want to continue the conversation because I think it's important.</reason>
            </response>
        """)

        system_prompt = message.system_prompt or ""
        system_prompt += comm_channel_prompt(
            session, message.recipient_user, message.from_user, message.channel
        )
        allowed_tools = [
            "update_channel_summary",
            "update_user_summary",
            "update_server_summary",
        ]

        response = call_llm(
            session,
            bot_user=message.recipient_user,
            from_user=message.from_user,
            channel=message.channel,
            model=settings.SUMMARIZER_MODEL,
            system_prompt=system_prompt,
            messages=[msg],
            allowed_tools=message.filter_tools(allowed_tools),
        )
        if not response:
            return False
        if not (res := re.search(r"<number>(.*)</number>", response)):
            return False
        try:
            logger.info(f"chattiness_threshold: {message.chattiness_threshold}")
            logger.info(f"haiku desire: {res.group(1)}")
            if int(res.group(1)) < 100 - message.chattiness_threshold:
                return False
        except ValueError:
            return False

        if not (bot_id := _resolve_bot_id(message)):
            return False

        if message.channel and message.channel.server:
            discord.trigger_typing_channel(bot_id, cast(int, message.channel_id))
        else:
            discord.trigger_typing_dm(bot_id, cast(int | str, message.from_id))
        return True


def _resolve_bot_id(discord_message: DiscordMessage) -> int | None:
    recipient = discord_message.recipient_user
    if not recipient:
        return None

    system_user = recipient.system_user
    if not system_user:
        return None

    return getattr(system_user, "id", None)


@app.task(name=PROCESS_DISCORD_MESSAGE)
@safe_task_execution
def process_discord_message(message_id: int) -> dict[str, Any]:
    """
    Process a Discord message.

    This task is queued by the Discord collector when messages are received.
    """
    logger.info(f"Processing Discord message {message_id}")

    with make_session() as session:
        discord_message = session.get(DiscordMessage, message_id)
        if not discord_message:
            logger.info(f"Discord message not found: {message_id}")
            return {
                "status": "error",
                "error": "Message not found",
                "message_id": message_id,
            }

        bot_id = _resolve_bot_id(discord_message)
        if not bot_id:
            logger.warning(
                "No associated Discord bot user for message %s; skipping send",
                message_id,
            )
            return {
                "status": "processed",
                "message_id": message_id,
            }

        # Validate required relationships exist before processing
        if not discord_message.recipient_user:
            logger.warning(
                "No recipient_user for message %s; skipping processing",
                message_id,
            )
            return {
                "status": "error",
                "error": "No recipient user",
                "message_id": message_id,
            }

        if not discord_message.from_user:
            logger.warning(
                "No from_user for message %s; skipping processing",
                message_id,
            )
            return {
                "status": "error",
                "error": "No sender user",
                "message_id": message_id,
            }

        mcp_servers = discord_message.get_mcp_servers(session)
        system_prompt = discord_message.system_prompt or ""
        system_prompt += comm_channel_prompt(
            session,
            discord_message.recipient_user,
            discord_message.from_user,
            discord_message.channel,
        )

        try:
            response = call_llm(
                session,
                bot_user=discord_message.recipient_user,
                from_user=discord_message.from_user,
                channel=discord_message.channel,
                model=settings.DISCORD_MODEL,
                mcp_servers=mcp_servers,
                system_prompt=discord_message.system_prompt,
            )
        except Exception:
            logger.exception("Failed to generate Discord response")
            return {
                "status": "error",
                "error": "Failed to generate Discord response",
                "message_id": message_id,
            }
        if not response:
            return {
                "status": "no-response",
                "message_id": message_id,
            }

        res = send_discord_response(
            bot_id=bot_id,
            response=response,
            channel_id=discord_message.channel_id,
            user_identifier=discord_message.from_user
            and discord_message.from_user.username,
        )
        if not res:
            return {
                "status": "error",
                "error": "Failed to send Discord response",
                "message_id": message_id,
            }

    return {
        "status": "processed",
        "message_id": message_id,
    }


@app.task(name=ADD_DISCORD_MESSAGE)
@safe_task_execution
def add_discord_message(
    message_id: int,
    channel_id: int,
    author_id: int,
    content: str,
    sent_at: str,
    server_id: int | None = None,
    recipient_id: int | None = None,
    message_reference_id: int | None = None,
    message_type: str = "default",
    thread_id: int | None = None,
    image_urls: list[str] | None = None,
) -> dict[str, Any]:
    """
    Add a Discord message to the database.

    This task is queued by the Discord collector when messages are received.
    """
    logger.info(f"Adding Discord message {message_id}: {content}")
    # Include message_id in hash to ensure uniqueness across duplicate content
    content_hash = hashlib.sha256(f"{message_id}:{content}".encode()).digest()
    sent_at_dt = datetime.fromisoformat(sent_at.replace("Z", "+00:00"))

    # Download and save images to disk
    saved_image_paths = []
    if image_urls:
        saved_image_paths = download_and_save_images(image_urls, message_id)

    with make_session() as session:
        discord_message = DiscordMessage(
            modality="text",
            sha256=content_hash,
            content=content,
            channel_id=channel_id,
            sent_at=sent_at_dt,
            server_id=server_id,
            from_id=author_id,
            recipient_id=recipient_id,
            message_id=message_id,
            message_type=message_type,
            reply_to_message_id=message_reference_id,
            thread_id=thread_id,
            images=saved_image_paths or None,
        )
        existing = check_content_exists(
            session, DiscordMessage, message_id=message_id, sha256=content_hash
        )
        if existing:
            existing_msg = cast(DiscordMessage, existing)
            logger.info(f"Discord message already exists: {existing_msg.message_id}")
            return create_task_result(
                existing_msg, "already_exists", message_id=message_id
            )

        if channel_id:
            discord_message.messages_before = get_prev(session, channel_id, sent_at_dt)

        try:
            result = process_content_item(discord_message, session)
        except sqlalchemy_exc.IntegrityError as e:
            logger.error(f"Integrity error adding Discord message {message_id}: {e}")
            return {
                "status": "error",
                "error": "Integrity error",
                "message_id": message_id,
            }
        if should_process(discord_message):
            process_discord_message.delay(discord_message.id)

        return result


@app.task(name=EDIT_DISCORD_MESSAGE)
@safe_task_execution
def edit_discord_message(
    message_id: int, content: str, edited_at: str
) -> dict[str, Any]:
    """
    Edit a Discord message in the database.

    This task is queued by the Discord collector when messages are edited.
    """
    logger.info(f"Editing Discord message {message_id}: {content}")
    with make_session() as session:
        existing = check_content_exists(
            session, DiscordMessage, message_id=message_id
        )
        if not existing:
            return {
                "status": "error",
                "error": "Message not found",
                "message_id": message_id,
            }

        existing_msg = cast(DiscordMessage, existing)
        existing_msg.content = content  # type: ignore
        if existing_msg.channel_id:
            existing_msg.messages_before = get_prev(
                session, existing_msg.channel_id, existing_msg.sent_at
            )
        existing_msg.edited_at = datetime.fromisoformat(
            edited_at.replace("Z", "+00:00")
        )

        return process_content_item(existing_msg, session)
