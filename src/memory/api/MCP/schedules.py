"""
MCP tools for the epistemic sparring partner system.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from memory.api.MCP.base import get_current_user
from memory.common.db.connection import make_session
from memory.common.db.models import ScheduledLLMCall
from memory.api.MCP.base import mcp

logger = logging.getLogger(__name__)


@mcp.tool()
async def schedule_llm_call(
    scheduled_time: str,
    model: str,
    prompt: str,
    topic: str | None = None,
    discord_channel: str | None = None,
    system_prompt: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Schedule an LLM call to be executed at a specific time with response sent to Discord.

    Args:
        scheduled_time: ISO format datetime string (e.g., "2024-12-20T15:30:00Z")
        model: Model to use (e.g., "anthropic/claude-3-5-sonnet-20241022"). If not provided, the message will be sent to the user directly.
        prompt: The prompt to send to the LLM
        topic: The topic of the scheduled call. If not provided, the topic will be inferred from the prompt.
        discord_channel: Discord channel name where the response should be sent. If not provided, the message will be sent to the user directly.
        system_prompt: Optional system prompt
        metadata: Optional metadata dict for tracking

    Returns:
        Dict with scheduled call ID and status
    """
    logger.info("schedule_llm_call tool called")

    current_user = get_current_user()
    if not current_user["authenticated"]:
        raise ValueError("Not authenticated")
    user_id = current_user.get("user", {}).get("user_id")
    if not user_id:
        raise ValueError("User not found")

    discord_user = current_user.get("user", {}).get("discord_user_id")
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

    # Validate that the scheduled time is in the future
    # Compare with naive datetime since we store naive in the database
    current_time_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    if scheduled_dt <= current_time_naive:
        raise ValueError("Scheduled time must be in the future")

    with make_session() as session:
        # Create the scheduled call
        scheduled_call = ScheduledLLMCall(
            user_id=user_id,
            scheduled_time=scheduled_dt,
            topic=topic,
            model=model,
            prompt=prompt,
            system_prompt=system_prompt,
            discord_channel=discord_channel,
            discord_user=discord_user,
            data=metadata or {},
        )

        session.add(scheduled_call)
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
