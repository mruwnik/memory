"""
Celery tasks for Discord message processing.
"""

import hashlib
import logging
import re
import textwrap
from datetime import datetime
from typing import Any

from sqlalchemy import exc as sqlalchemy_exc
from sqlalchemy.orm import Session, scoped_session

from memory.common import discord, settings
from memory.common.celery_app import (
    ADD_DISCORD_MESSAGE,
    EDIT_DISCORD_MESSAGE,
    PROCESS_DISCORD_MESSAGE,
    app,
)
from memory.common.db.connection import make_session
from memory.common.db.models import DiscordMessage, DiscordUser
from memory.common.llms.base import create_provider
from memory.common.llms.tools.discord import make_discord_tools
from memory.discord.messages import comm_channel_prompt, previous_messages
from memory.workers.tasks.content_processing import (
    check_content_exists,
    create_task_result,
    process_content_item,
    safe_task_execution,
)

logger = logging.getLogger(__name__)


def get_prev(
    session: Session | scoped_session, channel_id: int, sent_at: datetime
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


def call_llm(
    session,
    message: DiscordMessage,
    model: str,
    msgs: list[str] = [],
    allowed_tools: list[str] = [],
) -> str | None:
    provider = create_provider(model=model)
    if provider.usage_tracker.is_rate_limited(model):
        logger.error(
            f"Rate limited for model {model}: {provider.usage_tracker.get_usage_breakdown(model=model)}"
        )
        return None

    tools = make_discord_tools(
        message.recipient_user.system_user,
        message.from_user,
        message.channel,
        model=model,
    )
    tools = {
        name: tool
        for name, tool in tools.items()
        if message.tool_allowed(name) and name in allowed_tools
    }
    system_prompt = message.system_prompt or ""
    system_prompt += comm_channel_prompt(
        session, message.recipient_user, message.channel
    )
    messages = previous_messages(
        session,
        message.recipient_user and message.recipient_user.id,
        message.channel and message.channel.id,
        max_messages=10,
    )
    return provider.run_with_tools(
        messages=provider.as_messages([m.title for m in messages] + msgs),
        tools=tools,
        system_prompt=system_prompt,
        max_iterations=settings.DISCORD_MAX_TOOL_CALLS,
    ).response


def should_process(message: DiscordMessage) -> bool:
    if not (
        settings.DISCORD_PROCESS_MESSAGES
        and settings.DISCORD_NOTIFICATIONS_ENABLED
        and not message.ignore_messages
    ):
        return False

    if message.from_user == message.recipient_user:
        logger.info("Skipping message because from_user == recipient_user")
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
        response = call_llm(
            session,
            message,
            settings.SUMMARIZER_MODEL,
            [msg],
            allowed_tools=[
                "update_channel_summary",
                "update_user_summary",
                "update_server_summary",
            ],
        )
        if not response:
            return False
        if not (res := re.search(r"<number>(.*)</number>", response)):
            return False
        try:
            return int(res.group(1)) > message.chattiness_threshold
        except ValueError:
            return False


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
        discord_message = session.query(DiscordMessage).get(message_id)
        if not discord_message:
            logger.info(f"Discord message not found: {message_id}")
            return {
                "status": "error",
                "error": "Message not found",
                "message_id": message_id,
            }

        response = call_llm(session, discord_message, settings.DISCORD_MODEL)

        if not response:
            return {
                "status": "processed",
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

        if discord_message.channel.server:
            discord.send_to_channel(bot_id, discord_message.channel.name, response)
        else:
            discord.send_dm(bot_id, discord_message.from_user.username, response)

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
            from_id=author_id,
            recipient_id=recipient_id,
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
