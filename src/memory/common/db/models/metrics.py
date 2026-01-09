"""
Database models for metrics and profiling data.
"""

from datetime import datetime, timezone
from sqlalchemy import Column, BigInteger, DateTime, String, Float, Index
from sqlalchemy.dialects.postgresql import JSONB

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

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    timestamp = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    metric_type = Column(String(50), nullable=False)
    name = Column(String(200), nullable=False)
    duration_ms = Column(Float, nullable=True)
    status = Column(String(50), nullable=True)
    labels = Column(JSONB, default=dict, nullable=False)
    value = Column(Float, nullable=True)

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
