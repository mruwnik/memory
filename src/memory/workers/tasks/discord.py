"""
Celery tasks for Discord message processing.
"""

import hashlib
import logging
from datetime import datetime
from typing import Any

from memory.common.celery_app import app
from memory.common.db.connection import make_session
from memory.common.db.models import DiscordMessage, DiscordUser
from memory.workers.tasks.content_processing import (
    safe_task_execution,
    check_content_exists,
    create_task_result,
    process_content_item,
)
from memory.common.celery_app import (
    ADD_DISCORD_MESSAGE,
    EDIT_DISCORD_MESSAGE,
    PROCESS_DISCORD_MESSAGE,
)
from memory.common import settings
from sqlalchemy.orm import Session, scoped_session

logger = logging.getLogger(__name__)


def get_prev(
    session: Session | scoped_session, channel_id: int, sent_at: datetime
) -> list[str]:
    prev = (
        session.query(DiscordUser.username, DiscordMessage.content)
        .join(DiscordUser, DiscordMessage.discord_user_id == DiscordUser.id)
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
    return (
        settings.DISCORD_PROCESS_MESSAGES
        and settings.DISCORD_NOTIFICATIONS_ENABLED
        and not (
            (message.server and message.server.ignore_messages)
            or (message.channel and message.channel.ignore_messages)
            or (message.discord_user and message.discord_user.ignore_messages)
        )
    )


@app.task(name=PROCESS_DISCORD_MESSAGE)
@safe_task_execution
def process_discord_message(message_id: int) -> dict[str, Any]:
    logger.info(f"Processing Discord message {message_id}")

    with make_session() as session:
        discord_message = session.query(DiscordMessage).get(message_id)
        if not discord_message:
            logger.info(f"Discord message not found: {message_id}")
            return {
                "status": "error",
                "error": "Message not found",
                "message_id": message_id,
            }

        print("Processing message", discord_message.id, discord_message.content)

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
    message_reference_id: int | None = None,
) -> dict[str, Any]:
    """
    Add a Discord message to the database.

    This task is queued by the Discord collector when messages are received.
    """
    logger.info(f"Adding Discord message {message_id}: {content}")
    # Include message_id in hash to ensure uniqueness across duplicate content
    content_hash = hashlib.sha256(f"{message_id}:{content}".encode()).digest()
    sent_at_dt = datetime.fromisoformat(sent_at.replace("Z", "+00:00"))

    with make_session() as session:
        discord_message = DiscordMessage(
            modality="text",
            sha256=content_hash,
            content=content,
            channel_id=channel_id,
            sent_at=sent_at_dt,
            server_id=server_id,
            discord_user_id=author_id,
            message_id=message_id,
            message_type="reply" if message_reference_id else "default",
            reply_to_message_id=message_reference_id,
        )
        existing_msg = check_content_exists(
            session, DiscordMessage, message_id=message_id, sha256=content_hash
        )
        if existing_msg:
            logger.info(f"Discord message already exists: {existing_msg.message_id}")
            return create_task_result(
                existing_msg, "already_exists", message_id=message_id
            )

        if channel_id:
            discord_message.messages_before = get_prev(session, channel_id, sent_at_dt)

        result = process_content_item(discord_message, session)
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
        existing_msg = check_content_exists(
            session, DiscordMessage, message_id=message_id
        )
        if not existing_msg:
            return {
                "status": "error",
                "error": "Message not found",
                "message_id": message_id,
            }

        existing_msg.content = content  # type: ignore
        if existing_msg.channel_id:
            existing_msg.messages_before = get_prev(
                session, existing_msg.channel_id, existing_msg.sent_at
            )
        existing_msg.edited_at = datetime.fromisoformat(
            edited_at.replace("Z", "+00:00")
        )

        return process_content_item(existing_msg, session)
