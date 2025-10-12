import logging
from datetime import datetime, timezone
from typing import cast

from memory.common.db.connection import make_session
from memory.common.db.models import ScheduledLLMCall
from memory.common.celery_app import (
    app,
    EXECUTE_SCHEDULED_CALL,
    RUN_SCHEDULED_CALLS,
)
from memory.common import llms, discord
from memory.workers.tasks.content_processing import safe_task_execution

logger = logging.getLogger(__name__)


def _send_to_discord(scheduled_call: ScheduledLLMCall, response: str):
    """
    Send the LLM response to the specified Discord user.

    Args:
        scheduled_call: The scheduled call object
        response: The LLM response to send
    """
    # Format the message with topic, model, and response
    message_parts = []
    if cast(str, scheduled_call.topic):
        message_parts.append(f"**Topic:** {scheduled_call.topic}")
    if cast(str, scheduled_call.model):
        message_parts.append(f"**Model:** {scheduled_call.model}")
    message_parts.append(f"**Response:** {response}")

    message = "\n".join(message_parts)

    # Discord has a 2000 character limit, so we may need to split the message
    if len(message) > 1900:  # Leave some buffer
        message = message[:1900] + "\n\n... (response truncated)"

    if discord_user := cast(str, scheduled_call.discord_user):
        logger.info(f"Sending DM to {discord_user}: {message}")
        discord.send_dm(discord_user, message)
    elif discord_channel := cast(str, scheduled_call.discord_channel):
        logger.info(f"Broadcasting message to {discord_channel}: {message}")
        discord.broadcast_message(discord_channel, message)
    else:
        logger.warning(
            f"No Discord user or channel found for scheduled call {scheduled_call.id}"
        )


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
        scheduled_call = (
            session.query(ScheduledLLMCall)
            .filter(ScheduledLLMCall.id == scheduled_call_id)
            .first()
        )

        if not scheduled_call:
            logger.error(f"Scheduled call {scheduled_call_id} not found")
            return {"error": "Scheduled call not found"}

        # Check if the call is still pending
        if not scheduled_call.is_pending():
            logger.warning(
                f"Scheduled call {scheduled_call_id} is not pending (status: {scheduled_call.status})"
            )
            return {"error": f"Call is not pending (status: {scheduled_call.status})"}

        # Update status to executing
        scheduled_call.status = "executing"
        scheduled_call.executed_at = datetime.now(timezone.utc)
        session.commit()

        logger.info(f"Calling LLM with model {scheduled_call.model}")

        # Make the LLM call
        if scheduled_call.model:
            response = llms.call(
                prompt=cast(str, scheduled_call.message),
                model=cast(str, scheduled_call.model),
                system_prompt=cast(str, scheduled_call.system_prompt)
                or llms.SYSTEM_PROMPT,
            )
        else:
            response = cast(str, scheduled_call.message)

        # Store the response
        scheduled_call.response = response
        scheduled_call.status = "completed"
        session.commit()

        logger.info(f"LLM call completed for {scheduled_call_id}")

        # Send to Discord
        try:
            _send_to_discord(scheduled_call, response)
            logger.info(f"Response sent to Discord for {scheduled_call_id}")
        except Exception as discord_error:
            logger.error(f"Failed to send to Discord: {discord_error}")
            # Don't mark as failed since the LLM call succeeded
            scheduled_call.data = scheduled_call.data or {}
            scheduled_call.data["discord_error"] = str(discord_error)
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
    """Run scheduled calls that are due."""
    with make_session() as session:
        calls = (
            session.query(ScheduledLLMCall)
            .filter(
                ScheduledLLMCall.status.in_(["pending"]),
                ScheduledLLMCall.scheduled_time
                < datetime.now(timezone.utc).replace(tzinfo=None),
            )
            .all()
        )
        for call in calls:
            execute_scheduled_call.delay(call.id)

        return {
            "calls": [call.id for call in calls],
            "count": len(calls),
        }
