"""Who did what: admin-only records of mutating calls and settings changes."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from memory.common.db.models.base import Base


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )
    actor_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Identifies the actor once the user is deleted and actor_id is NULL.
    actor_email: Mapped[str | None] = mapped_column(String, nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target: Mapped[str | None] = mapped_column(String(200), nullable=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )

    __table_args__ = (
        Index("idx_audit_events_timestamp", "timestamp"),
        Index("idx_audit_events_action_ts", "action", "timestamp"),
        Index("idx_audit_events_actor_ts", "actor_id", "timestamp"),
    )
