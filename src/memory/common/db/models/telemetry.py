"""
Database models for telemetry collection.

Stores OpenTelemetry metrics and events for usage analysis,
cost tracking, and pattern identification.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from memory.common.db.models.base import Base


class TelemetryEvent(Base):
    """
    Raw OTLP events.

    Stores both metrics (counters/gauges) and log events in a unified table.

    Event types:
    - 'metric': Counter/gauge values (token.usage, cost.usage, session.count, etc.)
    - 'log': Structured events (user_prompt, tool_result, api_request, api_error)
    """

    __tablename__ = "telemetry_events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    timestamp = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # User who reported this event
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    user = relationship("User", lazy="select")

    # Event classification
    event_type = Column(String(50), nullable=False)  # 'metric' or 'log'
    name = Column(String(100), nullable=False)  # e.g., 'token.usage', 'user_prompt'

    # Numeric value (for counters/gauges)
    value = Column(Float, nullable=True)

    # Session tracking
    session_id = Column(String(100), nullable=True, index=True)

    # Common dimensions extracted for efficient querying
    source = Column(String(100), nullable=True)  # e.g., model name, tool name
    tool_name = Column(String(100), nullable=True)

    # Full OTLP attributes (token_type, cost_type, etc.)
    attributes = Column(JSONB, default=dict, nullable=False)

    # For log events: body content (prompt hash, error message, etc.)
    body = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_telemetry_events_timestamp", "timestamp"),
        Index("idx_telemetry_events_user_ts", "user_id", "timestamp"),
        Index("idx_telemetry_events_name_ts", "name", "timestamp"),
        Index("idx_telemetry_events_type_name", "event_type", "name"),
        Index("idx_telemetry_events_session", "session_id", "timestamp"),
        Index("idx_telemetry_events_source", "source", "timestamp"),
        # GIN index for JSONB attribute queries
        Index("idx_telemetry_events_attrs", "attributes", postgresql_using="gin"),
    )

    def __repr__(self) -> str:
        return (
            f"<TelemetryEvent(id={self.id}, type={self.event_type}, "
            f"name={self.name}, value={self.value})>"
        )
