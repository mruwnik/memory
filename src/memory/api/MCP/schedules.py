"""
MCP tools for the epistemic sparring partner system.
"""

import logging
from datetime import datetime, timezone
from typing import Any, cast

from memory.api.MCP.base import get_current_user, mcp
from memory.common.db.connection import make_session
from memory.common.db.models import ScheduledLLMCall, DiscordBotUser
from memory.discord.messages import schedule_discord_message

logger = logging.getLogger(__name__)


@mcp.tool()
async def schedule_message(
    scheduled_time: str,
    message: str,
    model: str | None = None,
    topic: str | None = None,
    discord_channel: str | None = None,
    system_prompt: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Schedule an message to be sent to the user's Discord at specific time.

    This can either be a string to be sent, or a prompt that should be
    be first sent to an LLM to generate the final message to be sent.

    If `model` is empty, the message will be sent as is. If a model is provided, the message will first be sent to that AI system, and the user
    will be sent whatever the AI system generates.

    Args:
        scheduled_time: ISO format datetime string (e.g., "2024-12-20T15:30:00Z")
        message: A raw message to be sent to the user, or prompt to the LLM if `model` is set
        model: Model to use (e.g., "anthropic/claude-3-5-sonnet-20241022"). If not provided, the message will be sent to the user directly. Currently only OpenAI and Anthropic models are supported
        topic: The topic of the scheduled call. If not provided, the topic will be inferred from the prompt (if provided).
        discord_channel: Discord channel name where the response should be sent. If not provided, the message will be sent to the user directly.
        system_prompt: Optional system prompt
        metadata: Optional metadata dict for tracking

    Returns:
        Dict with scheduled call ID and status
    """
    logger.info("schedule_message tool called")
    if not message:
        raise ValueError("You must provide `message`")

    current_user = get_current_user()
    if not current_user["authenticated"]:
        raise ValueError("Not authenticated")
    user_id = current_user.get("user", {}).get("user_id")
    if not user_id:
        raise ValueError("User not found")

    discord_users = current_user.get("user", {}).get("discord_users")
    discord_user = discord_users and next(iter(discord_users.keys()), None)
    if not discord_user and not discord_channel:
        raise ValueError("Either discord_user or discord_channel must be provided")

    # Parse scheduled time
    try:
        scheduled_dt = datetime.fromisoformat(scheduled_time.replace("Z", "+00:00"))
        # Ensure we store as naive datetime (remove timezone info for database storage)
        if scheduled_dt.tzinfo is not None:
            scheduled_dt = scheduled_dt.astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        raise ValueError("Invalid datetime format")

    with make_session() as session:
        bot = session.query(DiscordBotUser).first()
        if not bot:
            return {"error": "No bot found"}

        scheduled_call = schedule_discord_message(
            session=session,
            scheduled_time=scheduled_dt,
            message=message,
            user_id=cast(int, bot.id),
            model=model,
            topic=topic,
            discord_channel=discord_channel,
            discord_user=discord_user,
            system_prompt=system_prompt,
            metadata=metadata,
        )

        session.commit()

        return {
            "success": True,
            "scheduled_call_id": scheduled_call.id,
            "scheduled_time": scheduled_dt.isoformat(),
            "message": f"LLM call scheduled for {scheduled_dt.isoformat()}",
        }


@mcp.tool()
async def list_scheduled_llm_calls(
    status: str | None = None, limit: int | None = 50
) -> dict[str, Any]:
    """
    List scheduled LLM calls for the authenticated user.

    Args:
        status: Optional status filter ("pending", "executing", "completed", "failed", "cancelled")
        limit: Maximum number of calls to return (default: 50)

    Returns:
        Dict with list of scheduled calls
    """
    logger.info("list_scheduled_llm_calls tool called")

    current_user = get_current_user()
    if not current_user["authenticated"]:
        return {"error": "Not authenticated", "user": current_user}
    user_id = current_user.get("user", {}).get("user_id")
    if not user_id:
        return {"error": "User not found", "user": current_user}

    with make_session() as session:
        query = (
            session.query(ScheduledLLMCall)
            .filter(ScheduledLLMCall.user_id == user_id)
            .order_by(ScheduledLLMCall.scheduled_time.desc())
        )

        if status:
            query = query.filter(ScheduledLLMCall.status == status)

        if limit:
            query = query.limit(limit)

        calls = query.all()

        return {
            "success": True,
            "scheduled_calls": [call.serialize() for call in calls],
            "count": len(calls),
        }


@mcp.tool()
async def cancel_scheduled_llm_call(scheduled_call_id: str) -> dict[str, Any]:
    """
    Cancel a scheduled LLM call.

    Args:
        scheduled_call_id: ID of the scheduled call to cancel

    Returns:
        Dict with cancellation status
    """
    logger.info(f"cancel_scheduled_llm_call tool called for ID: {scheduled_call_id}")

    current_user = get_current_user()
    if not current_user["authenticated"]:
        return {"error": "Not authenticated", "user": current_user}
    user_id = current_user.get("user", {}).get("user_id")
    if not user_id:
        return {"error": "User not found", "user": current_user}

    with make_session() as session:
        # Find the scheduled call
        scheduled_call = (
            session.query(ScheduledLLMCall)
            .filter(
                ScheduledLLMCall.id == scheduled_call_id,
                ScheduledLLMCall.user_id == user_id,
            )
            .first()
        )

        if not scheduled_call:
            return {"error": "Scheduled call not found"}

        if not scheduled_call.can_be_cancelled():
            return {"error": f"Cannot cancel call with status: {scheduled_call.status}"}

        # Update the status
        scheduled_call.status = "cancelled"
        session.commit()

        logger.info(f"Scheduled LLM call {scheduled_call_id} cancelled")

        return {
            "success": True,
            "message": f"Scheduled call {scheduled_call_id} has been cancelled",
        }
