from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from memory.common.db.models.base import Base

if TYPE_CHECKING:
    from memory.common.db.models.users import User


class ScheduledLLMCall(Base):
    __tablename__ = "scheduled_llm_calls"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    topic: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Scheduling info
    scheduled_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now()
    )
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # LLM call configuration
    model: Mapped[str | None] = mapped_column(
        String, nullable=True, doc='e.g., "anthropic/claude-3-5-sonnet-20241022"'
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    allowed_tools: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    # Channel configuration
    # channel_type: "discord", "slack", "email"
    # channel_identifier: discord user ID, slack user ID, email address
    channel_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    channel_identifier: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Execution status and results
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending"
    )  # pending, executing, completed, failed, cancelled
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Additional metadata
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Celery task tracking
    celery_task_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # Relationships
    user: Mapped[User] = relationship("User")

    def serialize(self) -> dict[str, Any]:
        def print_datetime(dt: datetime | None) -> str | None:
            if dt:
                return dt.isoformat()
            return None

        return {
            "id": self.id,
            "user_id": self.user_id,
            "topic": self.topic,
            "scheduled_time": print_datetime(self.scheduled_time),
            "created_at": print_datetime(self.created_at),
            "executed_at": print_datetime(self.executed_at),
            "model": self.model,
            "message": self.message,
            "system_prompt": self.system_prompt,
            "allowed_tools": self.allowed_tools,
            "channel_type": self.channel_type,
            "channel_identifier": self.channel_identifier,
            "status": self.status,
            "response": self.response,
            "error_message": self.error_message,
            "metadata": self.data,
            "celery_task_id": self.celery_task_id,
        }

    def is_pending(self) -> bool:
        return self.status == "pending"

    def is_completed(self) -> bool:
        return self.status in ("completed", "failed", "cancelled")

    def can_be_cancelled(self) -> bool:
        return self.status in ("pending",)
