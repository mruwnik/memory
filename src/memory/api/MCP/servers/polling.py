"""
MCP subserver for availability polling tools (LettuceMeet-style meeting scheduling).
"""

import logging
from datetime import datetime, timezone
from typing import Literal, cast

from fastmcp import FastMCP

from memory.api.MCP.access import get_mcp_current_user
from memory.api.MCP.visibility import require_scopes, visible_when
from memory.common.db.connection import make_session
from memory.common.db.models import (
    AvailabilityPoll,
    PollStatus,
)
from memory.api.polls import (
    poll_to_payload,
    aggregate_availability,
)

logger = logging.getLogger(__name__)

polling_mcp = FastMCP("memory-polling")


def get_current_user_id() -> int:
    """Get the current user ID from the MCP access token."""
    user = get_mcp_current_user()
    if not user or user.id is None:
        raise ValueError("Not authenticated")
    return user.id


def parse_datetime(s: str | None) -> datetime | None:
    """Parse ISO datetime string with UTC normalization."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        # Ensure UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        raise ValueError(f"Invalid datetime format: {s}")


@polling_mcp.tool()
@visible_when(require_scopes("polling"))
async def upsert_poll(
    poll_id: int | None = None,
    title: str | None = None,
    datetime_start: str | None = None,
    datetime_end: str | None = None,
    slot_duration: Literal[15, 30, 60] | None = None,
    description: str | None = None,
    closes_at: str | None = None,
    status: Literal["open", "closed", "finalized", "cancelled"] | None = None,
    finalized_time: str | None = None,
) -> dict:
    """
    Create or update an availability poll.

    If poll_id is None, creates a new poll (title and datetime fields required).
    If poll_id is provided, updates the existing poll (only provided fields are updated).

    Use status="closed" to close a poll, status="finalized" with finalized_time
    to finalize it, or status="cancelled" to cancel it.

    All datetimes should be in ISO format with UTC timezone (e.g., "2026-01-15T09:00:00Z").
    The client is responsible for timezone conversion when displaying to users.

    Args:
        poll_id: Poll ID to update (None to create new)
        title: Poll title (required for create)
        datetime_start: Start of poll window in UTC ISO format (required for create)
        datetime_end: End of poll window in UTC ISO format (required for create)
        slot_duration: Slot duration in minutes (default 30 for create)
        description: Optional description
        closes_at: Optional deadline in UTC ISO datetime format
        status: Poll status (open/closed/finalized/cancelled)
        finalized_time: Selected meeting time in UTC when status="finalized"

    Returns: Poll details including id, slug (for sharing), and configuration.
    """
    user_id = get_current_user_id()

    with make_session() as session:
        # Fetch existing or create new
        if poll_id:
            poll = session.get(AvailabilityPoll, poll_id)
            if not poll or poll.user_id != user_id:
                raise ValueError(f"Poll {poll_id} not found")
        else:
            # Creating new - validate required fields
            if not title or not datetime_start or not datetime_end:
                raise ValueError("title, datetime_start, and datetime_end required to create poll")
            poll = AvailabilityPoll(user_id=user_id)
            session.add(poll)

        # Update fields (use provided values, or keep existing for updates)
        if title is not None:
            poll.title = title
        if description is not None:
            poll.description = description
        if datetime_start is not None:
            poll.datetime_start = cast(datetime, parse_datetime(datetime_start))
        if datetime_end is not None:
            poll.datetime_end = cast(datetime, parse_datetime(datetime_end))
        if slot_duration is not None:
            poll.slot_duration_minutes = slot_duration
        elif poll_id is None:
            poll.slot_duration_minutes = 30  # Default for new polls
        if closes_at is not None:
            poll.closes_at = parse_datetime(closes_at)

        # Validate datetime range and slot configuration
        if poll.datetime_start and poll.datetime_end:
            if poll.datetime_start >= poll.datetime_end:
                raise ValueError("datetime_start must be before datetime_end")

            # Validate poll has at least one valid slot
            duration_minutes = (poll.datetime_end - poll.datetime_start).total_seconds() / 60
            if duration_minutes < poll.slot_duration_minutes:
                raise ValueError(
                    f"Poll duration ({int(duration_minutes)} min) must be at least "
                    f"one slot duration ({poll.slot_duration_minutes} min)"
                )

        # Handle status changes
        if status is not None:
            if status == "finalized":
                poll.status = PollStatus.FINALIZED.value
                poll.finalized_at = datetime.now(timezone.utc)
                # Use provided time or default to now
                poll.finalized_time = parse_datetime(finalized_time) if finalized_time else datetime.now(timezone.utc)
            elif status == "closed":
                poll.status = PollStatus.CLOSED.value
            elif status == "open":
                poll.status = PollStatus.OPEN.value
                # Clear finalized fields when reopening
                poll.finalized_at = None
                poll.finalized_time = None
            elif status == "cancelled":
                poll.status = PollStatus.CANCELLED.value

        session.commit()
        session.refresh(poll)

        result = poll_to_payload(poll).model_dump()
        result["share_url"] = f"/ui/polls/respond/{poll.slug}"
        result["results_url"] = f"/ui/polls/results/{poll.slug}"
        return result


@polling_mcp.tool()
@visible_when(require_scopes("polling"))
async def list_polls(
    status: Literal["open", "closed", "finalized", "cancelled"] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """
    List availability polls created by the current user.

    Args:
        status: Filter by status (open, closed, finalized, cancelled). Default: all
        limit: Maximum number of polls to return (default 50, max 200)
        offset: Number of polls to skip (default 0)

    Returns: List of polls with id, slug, title, status, response_count, etc.
    """
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    user_id = get_current_user_id()

    with make_session() as session:
        query = session.query(AvailabilityPoll).filter(
            AvailabilityPoll.user_id == user_id
        )

        if status:
            query = query.filter(AvailabilityPoll.status == status)

        query = query.order_by(AvailabilityPoll.created_at.desc())
        polls = query.offset(offset).limit(limit).all()

        return [poll_to_payload(p).model_dump() for p in polls]


@polling_mcp.tool()
@visible_when(require_scopes("polling"))
async def delete_poll(poll_id: int) -> dict:
    """
    Delete an availability poll permanently.

    This removes the poll and all associated responses from the database.
    This action cannot be undone.

    Args:
        poll_id: The poll ID to delete

    Returns: Confirmation of deletion with poll_id.
    """
    user_id = get_current_user_id()

    with make_session() as session:
        poll = session.get(AvailabilityPoll, poll_id)
        if not poll or poll.user_id != user_id:
            raise ValueError(f"Poll {poll_id} not found")

        poll_title = poll.title
        session.delete(poll)
        session.commit()

        return {
            "deleted": True,
            "poll_id": poll_id,
            "title": poll_title,
        }


@polling_mcp.tool()
@visible_when(require_scopes("polling"))
async def fetch(poll_id: int | None = None, slug: str | None = None) -> dict:
    """
    Get poll details and aggregated results by ID or slug.

    Shows which time slots have the most availability and who is available.
    Use poll_id to get your own polls, or slug to get any poll publicly.

    Args:
        poll_id: The poll ID (requires ownership)
        slug: The poll's public slug (anyone can view)

    Returns: Poll details with aggregated availability per slot and best times.
    """
    if poll_id is None and slug is None:
        raise ValueError("Must provide either poll_id or slug")
    if poll_id is not None and slug is not None:
        raise ValueError("Cannot provide both poll_id and slug")

    user_id = get_current_user_id()

    with make_session() as session:
        if poll_id is not None:
            poll = session.get(AvailabilityPoll, poll_id)
            if not poll or poll.user_id != user_id:
                raise ValueError(f"Poll {poll_id} not found")
        else:
            poll = (
                session.query(AvailabilityPoll)
                .filter(AvailabilityPoll.slug == slug)
                .first()
            )
            if not poll:
                raise ValueError(f"Poll with slug '{slug}' not found")

        aggregated = aggregate_availability(poll)

        # Find best slots
        if aggregated:
            max_available = max(s.available_count for s in aggregated)
            best_slots = [s for s in aggregated if s.available_count == max_available]
        else:
            best_slots = []

        return {
            "poll": poll_to_payload(poll).model_dump(),
            "response_count": poll.response_count,
            "aggregated": [s.model_dump() for s in aggregated],
            "best_slots": [s.model_dump() for s in best_slots[:5]],
        }


