"""
Celery tasks for executing scheduled LLM calls and sending messages.

Supports multiple notification channels: Discord, Slack, Email.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from memory.common import discord as discord_utils
from memory.common.celery_app import (
    EXECUTE_SCHEDULED_CALL,
    RUN_SCHEDULED_CALLS,
    app,
)
from memory.common.content_processing import safe_task_execution
from memory.common.db.connection import make_session
from memory.common.db.models import DiscordBot, EmailAccount, ScheduledLLMCall
from memory.common.db.models.slack import SlackUserCredentials
from memory.common.email_sender import get_account_by_address, prepare_send_config, send_email
from memory.common.slack import async_slack_call

logger = logging.getLogger(__name__)

# Maximum time a task can be in "executing" state before being considered stale
# Should be longer than task_time_limit (1 hour) to allow for legitimate long-running tasks
STALE_EXECUTION_TIMEOUT_HOURS = 2

# Discord message size limit
DISCORD_MESSAGE_LIMIT = 2000


@dataclass
class MessageParams:
    """Parameters extracted from ScheduledLLMCall for sending messages.

    Using a dataclass avoids DetachedInstanceError by extracting primitives
    from the SQLAlchemy object before passing to send functions.
    """
    channel_type: str
    channel_identifier: str
    message: str
    user_id: int
    topic: str | None
    data: dict[str, Any]


def extract_message_params(scheduled_call: ScheduledLLMCall) -> MessageParams | None:
    """Extract message parameters from a ScheduledLLMCall.

    Returns None if required fields are missing.
    """
    if not scheduled_call.channel_type or not scheduled_call.channel_identifier:
        return None

    return MessageParams(
        channel_type=scheduled_call.channel_type,
        channel_identifier=scheduled_call.channel_identifier,
        message=scheduled_call.message,
        user_id=scheduled_call.user_id,
        topic=scheduled_call.topic,
        data=scheduled_call.data or {},
    )


def send_via_discord(params: MessageParams) -> bool:
    """Send message via Discord DM."""
    user_id = params.channel_identifier
    if not user_id:
        logger.error("No Discord user ID for scheduled call")
        return False

    # Get bot ID from the scheduled call's data or user's first bot
    bot_id = params.data.get("discord_bot_id")

    if not bot_id:
        with make_session() as session:
            bot = (
                session.query(DiscordBot)
                .filter(DiscordBot.user_id == params.user_id)
                .first()
            )
            if bot:
                bot_id = bot.id
            else:
                logger.error(f"No Discord bot found for user {params.user_id}")
                return False

    # Truncate message if it exceeds Discord's limit
    message = params.message
    if len(message) > DISCORD_MESSAGE_LIMIT:
        message = message[:DISCORD_MESSAGE_LIMIT - 3] + "..."
        logger.warning(f"Discord message truncated from {len(params.message)} to {DISCORD_MESSAGE_LIMIT} chars")

    success = discord_utils.send_dm(bot_id, user_id, message)
    if success:
        logger.info(f"Discord DM sent to user {user_id}")
    else:
        logger.error(f"Failed to send Discord DM to user {user_id}")
    return success


def send_via_slack(params: MessageParams) -> bool:
    """Send message via Slack DM."""
    user_id = params.channel_identifier
    message = params.message

    if not user_id:
        logger.error("No Slack user ID for scheduled call")
        return False

    # Get Slack token for the user
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

    async def send():
        # Open DM channel
        data = await async_slack_call(slack_token, "conversations.open", users=user_id)
        channel = data.get("channel", {})
        channel_id = channel.get("id")
        if not channel_id:
            raise ValueError(f"Failed to open DM with Slack user {user_id}")

        # Send message
        await async_slack_call(
            slack_token,
            "chat.postMessage",
            channel=channel_id,
            text=message,
        )

    try:
        # Use get_event_loop to handle cases where a loop may already exist
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None:
            # Already in an async context - create a new thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, send())
                future.result(timeout=30)
        else:
            asyncio.run(send())

        logger.info(f"Slack DM sent to user {user_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to send Slack DM: {e}")
        return False


def send_via_email(params: MessageParams) -> bool:
    """Send message via email."""
    to_address = params.channel_identifier
    if not to_address:
        logger.error("No email address for scheduled call")
        return False

    # Get sender's email account
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
        body=params.message,
    )

    if result.success:
        logger.info(f"Email sent to {to_address}")
        return True
    logger.error(f"Failed to send email: {result.error}")
    return False


def send_message(params: MessageParams) -> bool:
    """Send message via the appropriate channel."""
    channel_type = params.channel_type

    if channel_type == "discord":
        return send_via_discord(params)
    elif channel_type == "slack":
        return send_via_slack(params)
    elif channel_type == "email":
        return send_via_email(params)

    logger.error(f"Unknown channel_type: {channel_type}")
    return False


@app.task(bind=True, name=EXECUTE_SCHEDULED_CALL)
@safe_task_execution
def execute_scheduled_call(self, scheduled_call_id: str):
    """
    Execute a scheduled LLM call and send the response.

    Args:
        scheduled_call_id: The ID of the scheduled call to execute
    """
    logger.info(f"Executing scheduled LLM call: {scheduled_call_id}")

    with make_session() as session:
        # Fetch the scheduled call
        scheduled_call = session.get(ScheduledLLMCall, scheduled_call_id)

        if not scheduled_call:
            logger.error(f"Scheduled call {scheduled_call_id} not found")
            return {"error": "Scheduled call not found"}

        # Check if the call is ready to execute (pending or queued)
        if scheduled_call.status not in ("pending", "queued"):
            logger.warning(
                f"Scheduled call {scheduled_call_id} is not ready (status: {scheduled_call.status})"
            )
            return {"error": f"Call is not ready (status: {scheduled_call.status})"}

        # Extract params to avoid DetachedInstanceError when send functions open their own sessions
        params = extract_message_params(scheduled_call)
        if not params:
            logger.error(f"Missing channel_type or channel_identifier for {scheduled_call_id}")
            scheduled_call.status = "failed"
            scheduled_call.error_message = "Missing channel configuration"
            session.commit()
            return {"error": "Missing channel configuration"}

        # Send via the appropriate channel
        try:
            sent = send_message(params)
            if sent:
                scheduled_call.status = "completed"
                scheduled_call.executed_at = datetime.now(timezone.utc)
                logger.info(f"Message sent for {scheduled_call_id}")
            else:
                scheduled_call.status = "failed"
                scheduled_call.error_message = "Failed to send message"
                logger.error(f"Failed to send message for {scheduled_call_id}")
        except Exception as send_error:
            logger.error(f"Failed to send message: {send_error}")
            scheduled_call.status = "failed"
            scheduled_call.error_message = str(send_error)

        session.commit()

        return {
            "success": scheduled_call.status == "completed",
            "scheduled_call_id": scheduled_call_id,
            "channel_type": params.channel_type,
        }


@app.task(name=RUN_SCHEDULED_CALLS)
@safe_task_execution
def run_scheduled_calls():
    """Run scheduled calls that are due.

    Uses SELECT FOR UPDATE SKIP LOCKED to prevent race conditions when
    multiple workers query for due calls simultaneously.

    Also recovers stale "executing" tasks that were abandoned due to worker crashes.
    """
    with make_session() as session:
        # First, recover stale "executing" tasks that have been stuck too long
        # This handles cases where workers crashed mid-execution
        stale_cutoff = datetime.now(timezone.utc) - timedelta(
            hours=STALE_EXECUTION_TIMEOUT_HOURS
        )
        stale_calls = (
            session.query(ScheduledLLMCall)
            .filter(
                ScheduledLLMCall.status == "executing",
                ScheduledLLMCall.executed_at < stale_cutoff,
            )
            .with_for_update(skip_locked=True)
            .all()
        )

        for stale_call in stale_calls:
            logger.warning(
                f"Recovering stale scheduled call {stale_call.id} "
                f"(stuck in executing since {stale_call.executed_at})"
            )
            stale_call.status = "pending"
            stale_call.executed_at = None
            stale_call.error_message = "Recovered from stale execution state"

        if stale_calls:
            session.commit()
            logger.info(f"Recovered {len(stale_calls)} stale scheduled calls")

        # Use FOR UPDATE SKIP LOCKED to atomically claim pending calls
        # This prevents multiple workers from processing the same call
        #
        # Note: scheduled_time is stored as naive datetime (assumed UTC).
        # We compare against current UTC time, also as naive datetime.
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        calls = (
            session.query(ScheduledLLMCall)
            .filter(
                ScheduledLLMCall.status.in_(["pending"]),
                ScheduledLLMCall.scheduled_time < now_utc,
            )
            .with_for_update(skip_locked=True)
            .all()
        )

        # Mark calls as queued before dispatching to prevent re-processing
        call_ids = []
        for call in calls:
            call.status = "queued"
            call_ids.append(call.id)
        session.commit()

        # Now dispatch tasks for queued calls
        for call_id in call_ids:
            execute_scheduled_call.delay(call_id)  # type: ignore[attr-defined]

        return {
            "calls": call_ids,
            "count": len(call_ids),
        }
