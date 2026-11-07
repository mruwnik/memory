"""Send a message to a Discord, Slack or email target. Worker-safe: no API imports."""

import asyncio
import concurrent.futures
import logging
from dataclasses import dataclass
from typing import Any

from memory.common import discord as discord_utils
from memory.common.db.connection import make_session
from memory.common.db.models import EmailAccount
from memory.common.db.models.slack import SlackUserCredentials
from memory.common.discord_data import discord_target_is_channel, get_user_bots
from memory.common.email_sender import (
    PASSWORD_RESET_HEADER,
    get_account_by_address,
    prepare_send_config,
    send_email,
)
from memory.common.slack import async_slack_call, slack_api_base_url, slack_target_is_dm

logger = logging.getLogger(__name__)

DISCORD_MESSAGE_LIMIT = 2000
SLACK_MESSAGE_LIMIT = 4000  # Slack chat.postMessage text limit
EMAIL_BODY_LIMIT = 100000  # Reasonable limit for email body size


@dataclass
class NotificationParams:
    """Parameters for sending notifications."""

    notification_channel: str
    notification_target: str
    message: str
    user_id: int
    topic: str | None
    data: dict[str, Any]


def resolve_discord_bot_id(session, params: NotificationParams) -> int | None:
    """The stored bot when it is the user's own (older rows may name anyone's), else
    the user's default bot."""
    bots = get_user_bots(session, params.user_id)
    stored = params.data.get("discord_bot_id")
    if bot := next((b for b in bots if b.id == stored), None) or next(iter(bots), None):
        return bot.id
    logger.error(f"No Discord bot found for user {params.user_id}")
    return None


def send_via_discord(params: NotificationParams) -> bool:
    """Send via Discord — channel post or user DM, derived from the target id."""
    target = params.notification_target
    if not target:
        logger.error("No Discord target for notification")
        return False
    # Snowflake ids can arrive as int across the celery boundary; the collector
    # API and classification both want a str.
    target = str(target)

    with make_session() as session:
        bot_id = resolve_discord_bot_id(session, params)
        if bot_id is None:
            return False
        is_channel = discord_target_is_channel(session, target)

    message = params.message
    if len(message) > DISCORD_MESSAGE_LIMIT:
        message = message[: DISCORD_MESSAGE_LIMIT - 3] + "..."
        logger.warning(
            f"Discord message truncated from {len(params.message)} to {DISCORD_MESSAGE_LIMIT} chars"
        )

    if is_channel:
        success = discord_utils.send_to_channel(bot_id, target, message)
        label = f"channel {target}"
    else:
        success = discord_utils.send_dm(bot_id, target, message)
        label = f"user {target}"

    if success:
        logger.info(f"Discord message sent to {label}")
    else:
        logger.error(f"Failed to send Discord message to {label}")
    return success


async def send_via_slack_async(params: NotificationParams) -> bool:
    """Async Slack send — opens a DM for a user id, posts directly to a channel."""
    target = params.notification_target
    message = params.message

    # Truncate message if it exceeds Slack's limit
    if len(message) > SLACK_MESSAGE_LIMIT:
        message = message[: SLACK_MESSAGE_LIMIT - 3] + "..."
        logger.warning(
            f"Slack message truncated from {len(params.message)} to {SLACK_MESSAGE_LIMIT} chars"
        )

    if not target:
        logger.error("No Slack target for notification")
        return False

    with make_session() as session:
        credentials = (
            session.query(SlackUserCredentials)
            .filter(SlackUserCredentials.user_id == params.user_id)
            .first()
        )
        if not credentials or not credentials.access_token:
            logger.error(f"No Slack credentials for user {params.user_id}")
            return False
        slack_token = credentials.access_token
        base_url = slack_api_base_url(credentials.slack_app)

    try:
        if slack_target_is_dm(target):
            data = await async_slack_call(
                slack_token, "conversations.open", base_url=base_url, users=target
            )
            channel_id = data.get("channel", {}).get("id")
            if not channel_id:
                raise ValueError(f"Failed to open DM with Slack user {target}")
            label = f"user {target}"
        else:
            channel_id = target
            label = f"channel {target}"

        await async_slack_call(
            slack_token,
            "chat.postMessage",
            base_url=base_url,
            channel=channel_id,
            text=message,
        )

        logger.info(f"Slack message sent to {label}")
        return True
    except Exception as e:
        logger.error(f"Failed to send Slack message: {e}")
        return False


def send_via_slack(params: NotificationParams) -> bool:
    """
    Send message via Slack DM.

    This is a sync wrapper around the async implementation. It handles the
    event loop boundary by always creating a new event loop via asyncio.run().
    If called from within an existing event loop (e.g., in tests), it runs
    the async code in a separate thread.
    """
    try:
        # Try to run directly - this works when no event loop is running
        return asyncio.run(send_via_slack_async(params))
    except RuntimeError as e:
        if "cannot be called from a running event loop" in str(e):
            # We're inside an existing event loop - run in a thread
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, send_via_slack_async(params))
                return future.result(timeout=30)
        raise


def send_via_email(params: NotificationParams) -> bool:
    """Send message via email."""
    to_address = params.notification_target
    if not to_address:
        logger.error("No email address for notification")
        return False

    message = params.message
    # Truncate message if it exceeds reasonable email body size
    if len(message) > EMAIL_BODY_LIMIT:
        message = (
            message[: EMAIL_BODY_LIMIT - 100]
            + "\n\n[Message truncated due to size limit]"
        )
        logger.warning(
            f"Email body truncated from {len(params.message)} to {EMAIL_BODY_LIMIT} chars"
        )

    from_address = params.data.get("from_address")

    with make_session() as session:
        if from_address:
            account = get_account_by_address(session, params.user_id, from_address)
        else:
            account = (
                session.query(EmailAccount)
                .filter(
                    EmailAccount.user_id == params.user_id,
                    EmailAccount.active.is_(True),
                )
                .first()
            )

        if not account:
            logger.error(f"No email account for user {params.user_id}")
            return False

        config = prepare_send_config(session, account)

    subject = params.data.get("subject", params.topic or "Notification")

    result = send_email(
        config=config,
        to=[to_address],
        subject=subject,
        body=message,
        headers={PASSWORD_RESET_HEADER: "1"} if params.data.get("password_reset") else None,
    )

    if result.success:
        logger.info(f"Email sent to {to_address}")
        return True
    logger.error(f"Failed to send email: {result.error}")
    return False


def deliver_notification(params: NotificationParams) -> bool:
    """Route a notification to the right channel and deliver it."""
    channel = params.notification_channel

    if channel == "discord":
        return send_via_discord(params)
    elif channel == "slack":
        return send_via_slack(params)
    elif channel == "email":
        return send_via_email(params)

    logger.error(f"Unknown notification_channel: {channel}")
    return False
