"""
Agent observation models for the epistemic sparring partner system.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from memory.common.db.models.base import Base

if TYPE_CHECKING:
    from memory.common.db.models.source_items import AgentObservation


class ObservationContradiction(Base):
    """
    Tracks contradictions between observations.
    Can be detected automatically or reported by agents.
    """

    __tablename__ = "observation_contradiction"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    observation_1_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("agent_observation.id", ondelete="CASCADE"),
        nullable=False,
    )
    observation_2_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("agent_observation.id", ondelete="CASCADE"),
        nullable=False,
    )
    contradiction_type: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # direct, implied, temporal
    detected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    detection_method: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # manual, automatic, agent-reported
    resolution: Mapped[str | None] = mapped_column(
        Text
    )  # How it was resolved, if at all
    observation_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # Relationships - use string references to avoid circular imports
    observation_1: Mapped[AgentObservation] = relationship(
        "AgentObservation",
        foreign_keys=[observation_1_id],
        back_populates="contradictions_as_first",
    )
    observation_2: Mapped[AgentObservation] = relationship(
        "AgentObservation",
        foreign_keys=[observation_2_id],
        back_populates="contradictions_as_second",
    )

    __table_args__ = (
        Index("obs_contra_obs1_idx", "observation_1_id"),
        Index("obs_contra_obs2_idx", "observation_2_id"),
        Index("obs_contra_type_idx", "contradiction_type"),
        Index("obs_contra_method_idx", "detection_method"),
    )
