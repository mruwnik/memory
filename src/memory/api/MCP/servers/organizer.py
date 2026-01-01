"""
MCP subserver for organizational tools: calendar, todos, reminders.
"""

import logging

from fastmcp import FastMCP

from memory.common.calendar import get_events_in_range, parse_date_range
from memory.common.db.connection import make_session

logger = logging.getLogger(__name__)

organizer_mcp = FastMCP("memory-organizer")


@organizer_mcp.tool()
async def get_upcoming_events(
    start_date: str | None = None,
    end_date: str | None = None,
    days: int = 7,
    limit: int = 50,
) -> list[dict]:
    """
    Get calendar events within a time span.
    Use to check the user's schedule, find meetings, or plan around events.
    Automatically expands recurring events to show all occurrences in the range.

    Args:
        start_date: ISO format start date (e.g., "2024-01-15" or "2024-01-15T09:00:00Z").
                   Defaults to now if not provided.
        end_date: ISO format end date. Defaults to start_date + days if not provided.
        days: Number of days from start_date if end_date not specified (default 7, max 365)
        limit: Maximum number of events to return (default 50, max 200)

    Returns: List of events with id, event_title, start_time, end_time, all_day,
             location, calendar_name, recurrence_rule. Sorted by start_time.
    """
    days = min(max(days, 1), 365)
    limit = min(max(limit, 1), 200)

    range_start, range_end = parse_date_range(start_date, end_date, days)

    with make_session() as session:
        return get_events_in_range(session, range_start, range_end, limit)
