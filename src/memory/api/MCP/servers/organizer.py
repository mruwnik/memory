"""
MCP subserver for organizational tools: calendar, todos, reminders.
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Literal

from fastmcp import FastMCP

from memory.api.MCP.visibility import has_items, require_scopes, visible_when
from memory.common.calendar import EventDict, get_events_in_range, parse_date_range
from memory.common.db.connection import make_session
from memory.common.db.models import CalendarEvent, Task
from memory.common.tasks import TaskDict, get_tasks, task_to_dict

logger = logging.getLogger(__name__)

organizer_mcp = FastMCP("org")


@organizer_mcp.tool()
@visible_when(require_scopes("organizer"), has_items(CalendarEvent))
async def get_upcoming_events(
    start_date: str | None = None,
    end_date: str | None = None,
    days: int = 7,
    limit: int = 50,
) -> list[EventDict]:
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


# =============================================================================
# Task/Todo Tools
# =============================================================================


@organizer_mcp.tool()
@visible_when(require_scopes("organizer"), has_items(Task))
async def list_tasks(
    status: Literal["pending", "in_progress", "done", "cancelled"] | None = None,
    priority: Literal["low", "medium", "high", "urgent"] | None = None,
    include_completed: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[TaskDict]:
    """
    List the user's tasks/todos with optional filtering.
    Use to check what tasks are pending, find high-priority items, or review completed work.

    Args:
        status: Filter by status (pending, in_progress, done, cancelled)
        priority: Filter by priority (low, medium, high, urgent)
        include_completed: Include done/cancelled tasks (default False)
        limit: Maximum tasks to return (default 50, max 200)
        offset: Number of results to skip for pagination (default 0, max 10000)

    Returns: List of tasks with id, task_title, due_date, priority, status,
             recurrence, completed_at, tags. Sorted by due_date then priority.
    """
    limit = min(max(limit, 1), 200)
    offset = min(max(offset, 0), 10000)

    with make_session() as session:
        return get_tasks(
            session,
            status=status,
            priority=priority,
            include_completed=include_completed,
            limit=limit,
            offset=offset,
        )


@organizer_mcp.tool()
@visible_when(require_scopes("organizer"), has_items(Task))
async def get_task(task_id: int) -> TaskDict | dict:
    """
    Get a specific task by ID.
    Use to retrieve full details of a single task.

    Args:
        task_id: ID of the task to retrieve

    Returns:
        Dict with task details (id, task_title, due_date, priority, status,
        recurrence, completed_at, tags), or error dict if not found.
    """
    with make_session() as session:
        task = session.get(Task, task_id)
        if not task:
            return {"error": f"Task {task_id} not found"}
        return {"success": True, "task": task_to_dict(task)}


@organizer_mcp.tool()
@visible_when(require_scopes("organizer"))
async def create_task(
    title: str,
    due_date: str | None = None,
    priority: Literal["low", "medium", "high", "urgent"] | None = None,
    recurrence: str | None = None,
    tags: list[str] | None = None,
) -> TaskDict:
    """
    Create a new task/todo for the user.
    Use when the user asks you to remember something, add a task, or create a reminder.

    Args:
        title: The task title/description (required)
        due_date: ISO format due date (e.g., "2024-01-15" or "2024-01-15T09:00:00Z")
        priority: Priority level (low, medium, high, urgent)
        recurrence: RRULE format for recurring tasks (e.g., "FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR")
        tags: List of tags for categorization

    Returns: The created task with id, task_title, due_date, priority, status, etc.
    """
    parsed_due_date = None
    if due_date:
        try:
            parsed_due_date = datetime.fromisoformat(due_date.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"Invalid due_date format: {due_date}")

    # Hash based on title - same title means same task
    task_sha256 = hashlib.sha256(f"task:{title}".encode()).digest()

    with make_session() as session:
        # Check if task with this title already exists
        existing = session.query(Task).filter(Task.sha256 == task_sha256).first()
        if existing:
            return task_to_dict(existing)

        task = Task(
            task_title=title,
            due_date=parsed_due_date,
            priority=priority,
            status="pending",
            recurrence=recurrence,
            tags=tags or [],
            sha256=task_sha256,
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        return task_to_dict(task)


@organizer_mcp.tool()
@visible_when(require_scopes("organizer"), has_items(Task))
async def update_task(
    task_id: int,
    title: str | None = None,
    due_date: str | None = None,
    priority: Literal["low", "medium", "high", "urgent"] | None = None,
    status: Literal["pending", "in_progress", "done", "cancelled"] | None = None,
    tags: list[str] | None = None,
) -> TaskDict:
    """
    Update an existing task.
    Use to modify task details, change priority, or update status.

    Args:
        task_id: ID of the task to update (required)
        title: New task title
        due_date: New due date in ISO format
        priority: New priority (low, medium, high, urgent)
        status: New status (pending, in_progress, done, cancelled)
        tags: New tags list

    Returns: The updated task, or error if not found.
    """
    with make_session() as session:
        task = session.get(Task, task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        if title is not None:
            task.task_title = title
        if due_date is not None:
            try:
                task.due_date = datetime.fromisoformat(due_date.replace("Z", "+00:00"))
            except ValueError:
                raise ValueError(f"Invalid due_date format: {due_date}")
        if priority is not None:
            task.priority = priority
        if status is not None:
            task.status = status
            if status == "done" and not task.completed_at:
                task.completed_at = datetime.now(timezone.utc)
            elif status in ("pending", "in_progress"):
                task.completed_at = None
        if tags is not None:
            task.tags = tags

        session.commit()
        session.refresh(task)
        return task_to_dict(task)
