"""
Common task utilities for task querying and management.
"""

from datetime import datetime, timezone
from typing import TypedDict

from sqlalchemy import or_
from sqlalchemy.orm import Session

from memory.common.db.models import Task


class TaskDict(TypedDict):
    id: int
    task_title: str
    due_date: str | None
    priority: str | None
    status: str
    recurrence: str | None
    completed_at: str | None
    source_item_id: int | None
    tags: list[str]
    inserted_at: str | None


def task_to_dict(task: Task) -> TaskDict:
    """Convert a Task model to a dictionary."""
    return TaskDict(
        id=task.id,  # type: ignore
        task_title=task.task_title or "",  # type: ignore
        due_date=task.due_date.isoformat() if task.due_date else None,
        priority=task.priority,  # type: ignore
        status=task.status or "pending",  # type: ignore
        recurrence=task.recurrence,  # type: ignore
        completed_at=task.completed_at.isoformat() if task.completed_at else None,
        source_item_id=task.source_item_id,  # type: ignore
        tags=list(task.tags or []),  # type: ignore
        inserted_at=task.inserted_at.isoformat() if task.inserted_at else None,
    )


def get_tasks(
    session: Session,
    status: str | list[str] | None = None,
    priority: str | list[str] | None = None,
    include_completed: bool = False,
    due_before: datetime | None = None,
    due_after: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[TaskDict]:
    """Get tasks with optional filters.

    Args:
        session: Database session
        status: Filter by status(es) - single value or list
        priority: Filter by priority(ies) - single value or list
        include_completed: Whether to include done/cancelled tasks
        due_before: Only tasks due before this date
        due_after: Only tasks due after this date
        limit: Maximum number of tasks to return
        offset: Number of tasks to skip for pagination

    Returns:
        List of task dictionaries, sorted by due_date (nulls last), then priority
    """
    query = session.query(Task)

    # Status filter
    if status:
        if isinstance(status, str):
            query = query.filter(Task.status == status)
        else:
            query = query.filter(Task.status.in_(status))
    elif not include_completed:
        # By default, exclude done/cancelled
        query = query.filter(Task.status.in_(["pending", "in_progress"]))

    # Priority filter
    if priority:
        if isinstance(priority, str):
            query = query.filter(Task.priority == priority)
        else:
            query = query.filter(Task.priority.in_(priority))

    # Due date filters
    if due_before:
        query = query.filter(
            or_(Task.due_date.is_(None), Task.due_date <= due_before)
        )
    if due_after:
        query = query.filter(Task.due_date >= due_after)

    # Order by due_date (nulls last), then by priority
    # Priority order: urgent > high > medium > low > null
    priority_order = """
        CASE priority
            WHEN 'urgent' THEN 1
            WHEN 'high' THEN 2
            WHEN 'medium' THEN 3
            WHEN 'low' THEN 4
            ELSE 5
        END
    """
    query = query.order_by(
        Task.due_date.asc().nullslast(),
        Task.priority.desc().nullslast(),
        Task.inserted_at.desc(),
    )

    tasks = query.offset(offset).limit(limit).all()
    return [task_to_dict(t) for t in tasks]


def complete_task(session: Session, task_id: int) -> Task | None:
    """Mark a task as complete.

    Args:
        session: Database session
        task_id: ID of the task to complete

    Returns:
        The updated task, or None if not found
    """
    task = session.get(Task, task_id)
    if task:
        task.status = "done"
        task.completed_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(task)
    return task
