from __future__ import annotations
from datetime import datetime
import uuid
from typing import Any, Dict, List, TYPE_CHECKING
from sqlalchemy import (
    String,
    DateTime,
    ForeignKey,
    BigInteger,
    Integer,
    JSON,
    Text,
)
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship, Mapped, mapped_column

from memory.common.db.models.base import Base

if TYPE_CHECKING:
    from memory.common.db.models.discord import DiscordChannel, DiscordUser
    from memory.common.db.models.users import User


class ScheduledLLMCall(Base):
    __tablename__ = "scheduled_llm_calls"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Scheduling info
    scheduled_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # LLM call configuration
    model: Mapped[str | None] = mapped_column(
        String, nullable=True, doc='e.g., "anthropic/claude-3-5-sonnet-20241022"'
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    allowed_tools: Mapped[List[str] | None] = mapped_column(JSON, nullable=True)  # List of allowed tool names

    # Discord configuration
    discord_channel_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("discord_channels.id"), nullable=True
    )
    discord_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("discord_users.id"), nullable=True)

    # Execution status and results
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending"
    )  # pending, executing, completed, failed, cancelled
    response: Mapped[str | None] = mapped_column(Text, nullable=True)  # LLM response content
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Additional metadata
    data: Mapped[Dict[str, Any] | None] = mapped_column(JSON, nullable=True)  # For extensibility

    # Celery task tracking
    celery_task_id: Mapped[str | None] = mapped_column(String, nullable=True)  # Track the Celery Beat task

    # Relationships
    user: Mapped[User] = relationship("User")
    discord_channel: Mapped[DiscordChannel | None] = relationship("DiscordChannel", foreign_keys=[discord_channel_id])
    discord_user: Mapped[DiscordUser | None] = relationship("DiscordUser", foreign_keys=[discord_user_id])

    def serialize(self) -> Dict[str, Any]:
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
            "discord_channel": self.discord_channel.name if self.discord_channel else None,
            "discord_user": self.discord_user.username if self.discord_user else None,
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
