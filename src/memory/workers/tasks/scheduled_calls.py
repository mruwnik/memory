"""
Celery tasks for executing scheduled LLM calls and sending messages.

TODO: The Discord sending functionality needs to be reimplemented
to use the new DiscordBot/CollectorManager system for message sending.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import cast

from memory.common.celery_app import (
    EXECUTE_SCHEDULED_CALL,
    RUN_SCHEDULED_CALLS,
    app,
)
from memory.common.db.connection import make_session
from memory.common.db.models import ScheduledLLMCall
from memory.common.content_processing import safe_task_execution

logger = logging.getLogger(__name__)

# Maximum time a task can be in "executing" state before being considered stale
# Should be longer than task_time_limit (1 hour) to allow for legitimate long-running tasks
STALE_EXECUTION_TIMEOUT_HOURS = 2


def call_llm_for_scheduled(_session, scheduled_call: ScheduledLLMCall) -> str | None:
    """Call LLM with tools support for scheduled calls.

    TODO: This needs to be reimplemented - the old LLM calling code was removed
    during the Discord simplification. For now, if no model is specified,
    we just return the message directly.
    """
    if scheduled_call.model:
        # LLM processing not yet reimplemented
        logger.warning(
            f"LLM model {scheduled_call.model} specified but LLM calling not yet reimplemented"
        )
        return None

    # If no model specified, just return the message as-is
    return cast(str, scheduled_call.message)


def send_to_discord(_bot_id: int, scheduled_call: ScheduledLLMCall, response: str):
    """Send the response to Discord user or channel.

    TODO: This needs to use the new CollectorManager to send messages
    via the Discord bot. For now, this is a no-op that logs a warning.
    """
    channel_id = scheduled_call.discord_channel.id if scheduled_call.discord_channel else None
    user_name = scheduled_call.discord_user.username if scheduled_call.discord_user else None

    logger.warning(
        f"Discord sending not yet implemented. Would send to "
        f"channel={channel_id}, user={user_name}: {response[:100]}..."
    )
    # TODO: Implement using CollectorManager.send_message() or CollectorManager.send_dm()


@app.task(bind=True, name=EXECUTE_SCHEDULED_CALL)
@safe_task_execution
def execute_scheduled_call(self, scheduled_call_id: str):
    """
    Execute a scheduled LLM call and send the response to Discord.

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

        # Update status to executing
        scheduled_call.status = "executing"
        scheduled_call.executed_at = datetime.now(timezone.utc)
        session.commit()

        logger.info(f"Calling LLM with model {scheduled_call.model}")

        # Make the LLM call with tools support
        try:
            response = call_llm_for_scheduled(session, scheduled_call)
        except Exception:
            logger.exception("Failed to generate LLM response")
            scheduled_call.status = "failed"
            scheduled_call.error_message = "LLM call failed"
            session.commit()
            return {
                "success": False,
                "error": "LLM call failed",
                "scheduled_call_id": scheduled_call_id,
            }

        if not response:
            scheduled_call.status = "failed"
            scheduled_call.error_message = "No response from LLM"
            session.commit()
            return {
                "success": False,
                "error": "No response from LLM",
                "scheduled_call_id": scheduled_call_id,
            }

        # Store the response
        scheduled_call.response = response
        scheduled_call.status = "completed"
        session.commit()

        logger.info(f"LLM call completed for {scheduled_call_id}")

        # Send to Discord
        try:
            send_to_discord(cast(int, scheduled_call.user_id), scheduled_call, response)
            logger.info(f"Response sent to Discord for {scheduled_call_id}")
        except Exception as discord_error:
            logger.error(f"Failed to send to Discord: {discord_error}")
            # Don't mark as failed since the LLM call succeeded
            data = scheduled_call.data or {}
            data["discord_error"] = str(discord_error)
            scheduled_call.data = data
            session.commit()

        return {
            "success": True,
            "scheduled_call_id": scheduled_call_id,
            "response": response[:100] + "..." if len(response) > 100 else response,
            "discord_sent": True,
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
        stale_cutoff = datetime.now(timezone.utc) - timedelta(hours=STALE_EXECUTION_TIMEOUT_HOURS)
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
