"""API endpoints for Task management."""

from datetime import datetime, timezone
from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from memory.api.auth import get_current_user
from memory.common.db.connection import get_session
from memory.common.db.models import User, Task
from memory.common.tasks import get_tasks, complete_task, task_to_dict

router = APIRouter(prefix="/tasks", tags=["tasks"])


class TaskCreate(BaseModel):
    task_title: str
    due_date: datetime | None = None
    priority: Literal["low", "medium", "high", "urgent"] | None = None
    recurrence: str | None = None
    tags: list[str] = []


class TaskUpdate(BaseModel):
    task_title: str | None = None
    due_date: datetime | None = None
    priority: Literal["low", "medium", "high", "urgent"] | None = None
    status: Literal["pending", "in_progress", "done", "cancelled"] | None = None
    recurrence: str | None = None
    tags: list[str] | None = None


class TaskResponse(BaseModel):
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


@router.get("")
def list_tasks(
    status: str | None = Query(default=None, description="Filter by status"),
    priority: str | None = Query(default=None, description="Filter by priority"),
    include_completed: bool = Query(default=False, description="Include done/cancelled tasks"),
    due_before: datetime | None = Query(default=None, description="Tasks due before this date"),
    due_after: datetime | None = Query(default=None, description="Tasks due after this date"),
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[TaskResponse]:
    """List tasks with optional filters."""
    # Parse comma-separated status/priority if provided
    status_list = status.split(",") if status else None
    priority_list = priority.split(",") if priority else None

    tasks = get_tasks(
        db,
        status=status_list,
        priority=priority_list,
        include_completed=include_completed,
        due_before=due_before,
        due_after=due_after,
        limit=limit,
    )

    return [TaskResponse(**t) for t in tasks]


@router.post("")
def create_task(
    data: TaskCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> TaskResponse:
    """Create a new task."""
    task = Task(
        task_title=data.task_title,
        due_date=data.due_date,
        priority=data.priority,
        status="pending",
        recurrence=data.recurrence,
        tags=data.tags,
        sha256=b"task:" + data.task_title.encode()[:24],  # Simple hash for tasks
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    return TaskResponse(**task_to_dict(task))


@router.get("/{task_id}")
def get_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> TaskResponse:
    """Get a single task by ID."""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return TaskResponse(**task_to_dict(task))


@router.patch("/{task_id}")
def update_task(
    task_id: int,
    updates: TaskUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> TaskResponse:
    """Update a task."""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if updates.task_title is not None:
        task.task_title = updates.task_title
    if updates.due_date is not None:
        task.due_date = updates.due_date
    if updates.priority is not None:
        task.priority = updates.priority
    if updates.status is not None:
        task.status = updates.status
        # Set completed_at when marking as done
        if updates.status == "done" and not task.completed_at:
            task.completed_at = datetime.now(timezone.utc)
        # Clear completed_at if reopening
        elif updates.status in ("pending", "in_progress"):
            task.completed_at = None
    if updates.recurrence is not None:
        task.recurrence = updates.recurrence
    if updates.tags is not None:
        task.tags = updates.tags

    db.commit()
    db.refresh(task)

    return TaskResponse(**task_to_dict(task))


@router.delete("/{task_id}")
def delete_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Delete a task."""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    db.delete(task)
    db.commit()

    return {"status": "deleted"}


@router.post("/{task_id}/complete")
def mark_task_complete(
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> TaskResponse:
    """Mark a task as complete."""
    task = complete_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return TaskResponse(**task_to_dict(task))
