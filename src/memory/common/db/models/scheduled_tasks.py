# src/memory/common/db/models/scheduled_tasks.py
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from croniter import croniter
from sqlalchemy import ColumnElement, DateTime, ForeignKey, Index, Integer, JSON, String, Text, text
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from memory.common.db.models.base import Base

if TYPE_CHECKING:
    from memory.common.db.models.users import User


class TaskType(str, enum.Enum):
    """Valid task types for scheduled tasks."""
    NOTIFICATION = "notification"
    CLAUDE_SESSION = "claude_session"


class ExecutionStatus(str, enum.Enum):
    """Valid execution statuses for task executions."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


def compute_next_cron(cron_expression: str, base_time: datetime | None = None) -> datetime:
    """
    Compute the next occurrence of a cron expression.

    Args:
        cron_expression: A cron expression (e.g., "0 9 * * *" for daily at 9am)
        base_time: The time to compute the next occurrence from.
                   If None, uses current UTC time.

    Returns:
        The next scheduled datetime after base_time.

    Note on catchup behavior:
        This function does NOT catch up on missed runs. If a task was scheduled
        for 9am daily and is processed at 10am after being down, the next run
        will be 9am tomorrow - missed runs are silently skipped.

        This is intentional: for most use cases (notifications, reminders),
        catching up on missed runs would be confusing or harmful. If catchup
        behavior is needed, callers should implement it explicitly by iterating
        from the last successful execution time.
    """
    if base_time is None:
        base_time = datetime.now(timezone.utc).replace(tzinfo=None)

    cron = croniter(cron_expression, base_time)
    return cron.get_next(datetime)


def iso_utc(dt: datetime | None) -> str | None:
    """Serialize a stored datetime as an explicit-UTC ISO string.

    Stored datetimes are naive but semantically UTC. ``isoformat()`` alone
    emits no offset, so JS ``new Date(...)`` parses them as browser-local time.
    Appending ``Z`` for naive values (and preserving any existing offset for
    aware ones) makes the instant unambiguous on the wire.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.isoformat() + "Z"
    return dt.isoformat()


class ScheduledTask(Base):
    """A scheduled task that can run once or on a recurring schedule."""
    __tablename__ = "scheduled_tasks"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    task_type: Mapped[str] = mapped_column(String(20), nullable=False)
    topic: Mapped[str | None] = mapped_column(Text, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    notification_channel: Mapped[str | None] = mapped_column(String(20), nullable=True)
    notification_target: Mapped[str | None] = mapped_column(String(255), nullable=True)

    data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    cron_expression: Mapped[str | None] = mapped_column(String(100), nullable=True)
    next_scheduled_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, onupdate=func.now())

    user: Mapped[User] = relationship("User")
    executions: Mapped[list[TaskExecution]] = relationship(
        "TaskExecution", back_populates="task", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index(
            "ix_scheduled_tasks_next_time_enabled",
            "next_scheduled_time",
            postgresql_where=text("next_scheduled_time IS NOT NULL"),
        ),
        Index("ix_scheduled_tasks_user_id", "user_id"),
    )

    @hybrid_property
    def enabled(self) -> bool:
        """A task is enabled iff it has a pending run.

        State is fully determined by ``(cron_expression, next_scheduled_time)``;
        ``enabled`` is the derived view the API/UI consume. A fired one-off
        (``next_scheduled_time is None``) reads as disabled, which is what we
        want — it will never run again.
        """
        return self.next_scheduled_time is not None

    @enabled.inplace.expression
    @classmethod
    def _enabled_expression(cls) -> ColumnElement[bool]:
        return cls.next_scheduled_time.isnot(None)

    @enabled.inplace.setter
    def _enabled_setter(self, value: bool) -> None:
        if not value:
            self.next_scheduled_time = None
        elif self.next_scheduled_time is None and self.cron_expression:
            # Resuming a paused recurring task: schedule the next run from now.
            self.next_scheduled_time = compute_next_cron(self.cron_expression)
        # value=True with a next already set, or a one-off with no cron: no-op.

    def serialize(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "task_type": self.task_type,
            "topic": self.topic,
            "message": self.message,
            "notification_channel": self.notification_channel,
            "notification_target": self.notification_target,
            "data": self.data,
            "cron_expression": self.cron_expression,
            "next_scheduled_time": iso_utc(self.next_scheduled_time),
            "enabled": self.enabled,
            "created_at": iso_utc(self.created_at),
            "updated_at": iso_utc(self.updated_at),
        }


class TaskExecution(Base):
    """Record of a single execution attempt of a ScheduledTask."""
    __tablename__ = "task_executions"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    task_id: Mapped[str] = mapped_column(
        String, ForeignKey("scheduled_tasks.id", ondelete="CASCADE"), nullable=False
    )

    scheduled_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    celery_task_id: Mapped[str | None] = mapped_column(String, nullable=True)

    data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    task: Mapped[ScheduledTask] = relationship("ScheduledTask", back_populates="executions")

    __table_args__ = (
        Index("ix_task_executions_task_id_scheduled_time", "task_id", "scheduled_time"),
        Index(
            "ix_task_executions_status_active",
            "status",
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
        Index(
            "ix_task_executions_celery_task_id",
            "celery_task_id",
            postgresql_where=text("celery_task_id IS NOT NULL"),
        ),
    )

    def serialize(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "scheduled_time": iso_utc(self.scheduled_time),
            "started_at": iso_utc(self.started_at),
            "finished_at": iso_utc(self.finished_at),
            "status": self.status,
            "response": self.response,
            "error_message": self.error_message,
            "celery_task_id": self.celery_task_id,
            "data": self.data,
        }
