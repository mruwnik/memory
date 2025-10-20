from datetime import datetime
import uuid
from typing import Any, Dict, cast
from sqlalchemy import (
    Column,
    String,
    DateTime,
    ForeignKey,
    BigInteger,
    JSON,
    Text,
)
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from memory.common.db.models.base import Base


class ScheduledLLMCall(Base):
    __tablename__ = "scheduled_llm_calls"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    topic = Column(Text, nullable=True)

    # Scheduling info
    scheduled_time = Column(DateTime, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    executed_at = Column(DateTime, nullable=True)

    # LLM call configuration
    model = Column(
        String, nullable=True, doc='e.g., "anthropic/claude-3-5-sonnet-20241022"'
    )
    message = Column(Text, nullable=False)
    system_prompt = Column(Text, nullable=True)
    allowed_tools = Column(JSON, nullable=True)  # List of allowed tool names

    # Discord configuration
    discord_channel_id = Column(
        BigInteger, ForeignKey("discord_channels.id"), nullable=True
    )
    discord_user_id = Column(BigInteger, ForeignKey("discord_users.id"), nullable=True)

    # Execution status and results
    status = Column(
        String, nullable=False, default="pending"
    )  # pending, executing, completed, failed, cancelled
    response = Column(Text, nullable=True)  # LLM response content
    error_message = Column(Text, nullable=True)

    # Additional metadata
    data = Column(JSON, nullable=True)  # For extensibility

    # Celery task tracking
    celery_task_id = Column(String, nullable=True)  # Track the Celery Beat task

    # Relationships
    user = relationship("User")
    discord_channel = relationship("DiscordChannel", foreign_keys=[discord_channel_id])
    discord_user = relationship("DiscordUser", foreign_keys=[discord_user_id])

    def serialize(self) -> Dict[str, Any]:
        def print_datetime(dt: datetime | None) -> str | None:
            if dt:
                return dt.isoformat()
            return None

        return {
            "id": self.id,
            "user_id": self.user_id,
            "topic": self.topic,
            "scheduled_time": print_datetime(cast(datetime, self.scheduled_time)),
            "created_at": print_datetime(cast(datetime, self.created_at)),
            "executed_at": print_datetime(cast(datetime, self.executed_at)),
            "model": self.model,
            "message": self.message,
            "system_prompt": self.system_prompt,
            "allowed_tools": self.allowed_tools,
            "discord_channel": self.discord_channel and self.discord_channel.name,
            "discord_user": self.discord_user and self.discord_user.username,
            "status": self.status,
            "response": self.response,
            "error_message": self.error_message,
            "metadata": self.data,
            "celery_task_id": self.celery_task_id,
        }

    def is_pending(self) -> bool:
        return cast(str, self.status) == "pending"

    def is_completed(self) -> bool:
        return cast(str, self.status) in ("completed", "failed", "cancelled")

    def can_be_cancelled(self) -> bool:
        return cast(str, self.status) in ("pending",)
