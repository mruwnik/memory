"""
Database models for metrics and profiling data.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import BigInteger, DateTime, Float, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from memory.common.db.models.base import Base


class MetricEvent(Base):
    """
    Stores individual metric events from profiling.

    Used for:
    - Task execution timing (metric_type='task')
    - MCP tool call timing (metric_type='mcp_call')
    - System/process metrics (metric_type='system')
    - Any other profiled function (metric_type='function')
    """

    __tablename__ = "metric_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    metric_type: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    labels: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        Index("idx_metric_events_timestamp", "timestamp"),
        Index("idx_metric_events_type_name", "metric_type", "name"),
        Index("idx_metric_events_timestamp_type", "timestamp", "metric_type"),
    )

    def __repr__(self) -> str:
        return (
            f"<MetricEvent(id={self.id}, type={self.metric_type}, "
            f"name={self.name}, duration={self.duration_ms}ms)>"
        )
