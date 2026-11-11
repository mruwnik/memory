"""
MCP subserver for organizational tools: calendar, todos, reminders.
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Literal

from fastmcp import FastMCP

from memory.agents.subscription_input import new_tag, preamble, wrap_fields
from memory.api.MCP.access import (
    get_mcp_current_user,
    get_project_roles_by_user_id,
)
from memory.api.MCP.visibility import READ_ONLY, has_items, require_scopes, visible_when
from memory.common.access_control import (
    build_access_filter,
    has_admin_scope,
    user_can_access,
    user_can_edit,
)
from memory.common.dates import parse_iso_datetime
from memory.common.scopes import SCOPE_ORGANIZER, SCOPE_ORGANIZER_WRITE
from memory.common.calendar import EventDict, get_events_in_range, parse_date_range
from memory.common.db.connection import make_session
from memory.common.db.models import CalendarEvent, Task
from memory.common.db.models.events import item_event
from memory.common.db.models.journal import JournalEntry, build_journal_access_filter
from memory.common.tasks import TaskDict, get_tasks, task_to_dict

logger = logging.getLogger(__name__)

# Lets update_task tell an omitted argument from an explicit None (clear).
_UNSET: Any = "__UNSET__"

organizer_mcp = FastMCP("org")

# Fields written by whoever sent the invite, or extracted from a meeting transcript.
EVENT_TEXT_FIELDS = frozenset(
    {"event_title", "location", "calendar_name", "attendees", "meeting_link"}
)
TASK_TEXT_FIELDS = frozenset({"task_title", "tags"})
JOURNAL_TEXT_FIELDS = frozenset({"content"})


@organizer_mcp.tool(annotations=READ_ONLY)
@visible_when(require_scopes(SCOPE_ORGANIZER), has_items(CalendarEvent))
async def upcoming(
    start_date: str | None = None,
    end_date: str | None = None,
    days: int = 7,
    limit: int = 50,
    user_ids: list[int] | None = None,
) -> dict[str, Any]:
    """
    Get calendar events within a time span.
    Use to check the user's schedule, find meetings, or plan around events.
    Automatically expands recurring events to show all occurrences in the range.

    Args:
        start_date: ISO format start date (e.g., "2024-01-15" or "2024-01-15T09:00:00Z").
                   Defaults to now if not provided.
        end_date: ISO format end date. Defaults to start_date + days if not provided.
        days: Number of days from start_date if end_date not specified (default 7, max 365)
        limit: Maximum number of events to return (default 50, max 1000)
        user_ids: If provided, only return events from calendars owned by these users.
                  Only admins may name users other than themselves.

    Returns: {untrusted, events}: events with id, event_title, start_time, end_time,
             all_day, location, calendar_name, recurrence_rule, attendees and
             meeting_link, sorted by start_time. untrusted names the tag of the
             blocks holding text the invite's sender wrote.
    """
    days = min(max(days, 1), 365)
    limit = min(max(limit, 1), 1000)

    tag = new_tag()
    user = get_mcp_current_user()
    if user is None or user.id is None:
        return {"untrusted": preamble(tag), "events": []}

    is_admin = has_admin_scope(user)
    if not is_admin and any(uid != user.id for uid in user_ids or []):
        raise PermissionError("Only admins can view other users' calendars")

    range_start, range_end = parse_date_range(start_date, end_date, days)
    # Expanding recurring events is CPU-bound and can take a while, so it runs
    # in a thread rather than stalling every other request on this server.
    events = await asyncio.to_thread(
        events_in_range_for, user, is_admin, range_start, range_end, limit, user_ids
    )
    return {
        "untrusted": preamble(tag),
        "events": [wrap_fields(tag, e, EVENT_TEXT_FIELDS) for e in events],
    }


def events_in_range_for(
    user: Any,
    is_admin: bool,
    range_start: datetime,
    range_end: datetime,
    limit: int,
    user_ids: list[int] | None,
) -> list[EventDict]:
    """The calendar events in the range that ``user`` may see."""
    with make_session() as session:
        access_filter = None
        if not is_admin:
            project_roles = get_project_roles_by_user_id(user.id, session)
            access_filter = build_access_filter(user, project_roles)

        return get_events_in_range(
            session,
            range_start,
            range_end,
            limit,
            user_ids,
            access_filter=access_filter,
        )


# =============================================================================
# Task/Todo Tools
# =============================================================================


@organizer_mcp.tool(annotations=READ_ONLY)
@visible_when(require_scopes(SCOPE_ORGANIZER), has_items(Task))
async def list_tasks(
    status: Literal["pending", "in_progress", "done", "cancelled"] | None = None,
    priority: Literal["low", "medium", "high", "urgent"] | None = None,
    include_completed: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """
    List the user's tasks/todos with optional filtering.
    Use to check what tasks are pending, find high-priority items, or review completed work.

    Args:
        status: Filter by status (pending, in_progress, done, cancelled)
        priority: Filter by priority (low, medium, high, urgent)
        include_completed: Include done/cancelled tasks (default False)
        limit: Maximum tasks to return (default 50, max 200)
        offset: Number of results to skip for pagination (default 0, max 10000)

    Returns: {untrusted, tasks}: tasks with id, task_title, due_date, priority,
             status, recurrence, completed_at and tags, sorted by due_date then
             priority. untrusted names the tag of the blocks holding text people
             wrote (titles and tags, which may come from meeting transcripts).
    """
    limit = min(max(limit, 1), 200)
    offset = min(max(offset, 0), 10000)

    tag = new_tag()
    user = get_mcp_current_user()
    if user is None or user.id is None:
        return {"untrusted": preamble(tag), "tasks": []}

    with make_session() as session:
        # Build the standard access filter — admins see everything (None),
        # everyone else gets project + creator + person-override + public
        # bypass conditions threaded through. This is the same filter the
        # search pipeline uses, so list_tasks stays consistent with what
        # the user could already discover via search.
        access_filter = None
        if not has_admin_scope(user):
            project_roles = get_project_roles_by_user_id(user.id, session)
            access_filter = build_access_filter(user, project_roles)

        tasks = get_tasks(
            session,
            status=status,
            priority=priority,
            include_completed=include_completed,
            limit=limit,
            offset=offset,
            access_filter=access_filter,
        )
    return {
        "untrusted": preamble(tag),
        "tasks": [wrap_fields(tag, t, TASK_TEXT_FIELDS) for t in tasks],
    }


@organizer_mcp.tool(annotations=READ_ONLY)
@visible_when(require_scopes(SCOPE_ORGANIZER), has_items(Task))
async def fetch(task_id: int, include_journal: bool = False) -> dict:
    """
    Get a specific task by ID.
    Use to retrieve full details of a single task.

    Args:
        task_id: ID of the task to retrieve
        include_journal: Whether to include journal entries (default False)

    Returns:
        {untrusted, success, task, journal_entries?} with the task's id, task_title,
        due_date, priority, status, recurrence, completed_at and tags, or an error
        dict if not found. untrusted names the tag of the blocks holding text people
        wrote (task titles and tags, journal entry content).
    """
    user = get_mcp_current_user()
    if user is None or user.id is None:
        return {"error": "Authentication required"}

    with make_session() as session:
        task = session.get(Task, task_id)
        if not task:
            return {"error": f"Task {task_id} not found"}

        # Enforce per-task access control — tasks inherit the standard
        # SourceItem access model (project_id / sensitivity / creator_id /
        # person override). Without this any user with SCOPE_ORGANIZER
        # could read any task by ID enumeration.
        if not has_admin_scope(user):
            project_roles = get_project_roles_by_user_id(user.id, session)
            if not user_can_access(user, task, project_roles):
                # Same error string as "not found" so we don't leak the
                # existence of the row to a user who can't read it.
                return {"error": f"Task {task_id} not found"}

        tag = new_tag()
        result: dict[str, Any] = {
            "untrusted": preamble(tag),
            "success": True,
            "task": wrap_fields(tag, task_to_dict(task), TASK_TEXT_FIELDS),
        }

        if include_journal:
            user_id = getattr(user, "id", None) if user else None
            # Task is a polymorphic SourceItem (polymorphic_identity="task"),
            # so its rows live in the source_item table. The journal system's
            # CheckConstraint rejects target_type='task' at the DB level
            # ('source_item', 'project', 'team', 'poll' are the only allowed
            # values), so journal entries about a task are stored with
            # target_type='source_item' + target_id=<task_id> — the same
            # convention used by SourceItem.journal_entries elsewhere. The
            # previous filter (`target_type == "task"`) silently returned
            # an empty list.
            journal_query = (
                session.query(JournalEntry)
                .filter(
                    JournalEntry.target_type == "source_item",
                    JournalEntry.target_id == task_id,
                )
            )
            if user is not None and not has_admin_scope(user):
                journal_filter = build_journal_access_filter(user, user_id)
                if journal_filter is not True:
                    journal_query = journal_query.filter(journal_filter)
            journal_entries = journal_query.order_by(JournalEntry.created_at.asc()).all()
            result["journal_entries"] = [
                wrap_fields(tag, e.as_payload(), JOURNAL_TEXT_FIELDS) for e in journal_entries
            ]

        return result


@organizer_mcp.tool()
@visible_when(require_scopes(SCOPE_ORGANIZER_WRITE))
async def create_task(
    title: str,
    due_date: str | None = None,
    priority: Literal["low", "medium", "high", "urgent"] | None = None,
    recurrence: str | None = None,
    tags: list[str] | None = None,
) -> TaskDict:
    """
    Create a new task/todo for the user.
    Use when the user asks you to add a task or a to-do reminder.
    For information to keep, use notes_upsert; for a dated milestone with
    attached context, deadlines_upsert; for a ping at a set time,
    meta_notify_user with scheduled_time.

    Args:
        title: The task title/description (required)
        due_date: ISO format due date (e.g., "2024-01-15" or "2024-01-15T09:00:00Z")
        priority: Priority level (low, medium, high, urgent)
        recurrence: RRULE format for recurring tasks (e.g., "FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR")
        tags: List of tags for categorization

    Returns: The created task with id, task_title, due_date, priority, status, etc.
    """
    parsed_due_date = parse_iso_datetime(due_date)
    if due_date and parsed_due_date is None:
        raise ValueError(f"Invalid due_date format: {due_date}")

    user = get_mcp_current_user()
    if user is None or user.id is None:
        raise ValueError("Authentication required")

    # Hash based on title - same title means same task
    task_sha256 = hashlib.sha256(f"task:{title}".encode()).digest()

    with make_session() as session:
        # Check if task with this title already exists. Note: we return the
        # existing task to the caller regardless of who created it, but ONLY
        # if the caller can access it — otherwise we re-raise as a sha256
        # collision is a side channel for task-title enumeration.
        existing = session.query(Task).filter(Task.sha256 == task_sha256).first()
        if existing:
            if not has_admin_scope(user):
                project_roles = get_project_roles_by_user_id(user.id, session)
                if not user_can_access(user, existing, project_roles):
                    # Don't leak that a colliding title exists in someone
                    # else's project; behave as if creation failed.
                    raise ValueError(
                        "Task with this title already exists or cannot be created"
                    )
            return task_to_dict(existing)

        task = Task(
            task_title=title,
            due_date=parsed_due_date,
            priority=priority,
            status="pending",
            recurrence=recurrence,
            tags=tags or [],
            sha256=task_sha256,
            creator_id=user.id,
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        return task_to_dict(task)


@organizer_mcp.tool()
@visible_when(require_scopes(SCOPE_ORGANIZER_WRITE), has_items(Task))
async def update_task(
    task_id: int,
    title: str | None = None,
    due_date: str | None = _UNSET,
    priority: Literal["low", "medium", "high", "urgent"] | None = _UNSET,
    status: Literal["pending", "in_progress", "done", "cancelled"] | None = None,
    tags: list[str] | None = None,
) -> TaskDict:
    """
    Update an existing task.
    Use to modify task details, change priority, or update status.

    Args:
        task_id: ID of the task to update (required)
        title: New task title
        due_date: New due date in ISO format; null removes it, omit to keep it
        priority: New priority (low, medium, high, urgent); null removes it, omit to keep it
        status: New status (pending, in_progress, done, cancelled)
        tags: New tags list

    Returns: The updated task, or error if not found.
    """
    user = get_mcp_current_user()
    if user is None or user.id is None:
        raise ValueError("Authentication required")

    with make_session() as session:
        task = session.get(Task, task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        # First gate: can the caller even SEE this task? If not, behave as
        # if it doesn't exist so we don't leak the row's presence.
        project_roles: dict[int, str] | None = None
        if not has_admin_scope(user):
            project_roles = get_project_roles_by_user_id(user.id, session)
            if not user_can_access(user, task, project_roles):
                raise ValueError(f"Task {task_id} not found")

        # Second gate: edit perm = creator or admin (per user_can_edit).
        # A user who can read the task but didn't create it gets a clear
        # permission error rather than a misleading "not found".
        if not user_can_edit(user, task):
            raise PermissionError(
                "You can only update tasks you created"
            )

        if title is not None:
            task.task_title = title
        if due_date is None:
            task.due_date = None
        elif due_date is not _UNSET:
            parsed = parse_iso_datetime(due_date)
            if parsed is None:
                raise ValueError(f"Invalid due_date format: {due_date}")
            task.due_date = parsed
        if priority is not _UNSET:
            task.priority = priority
        if status is not None and status != task.status:
            session.add(item_event(session, task, ["state"]))
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
