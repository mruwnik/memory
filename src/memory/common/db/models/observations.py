"""
Agent observation models for the epistemic sparring partner system.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
)
from sqlalchemy import UUID as SQLUUID
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


class ReactionPattern(Base):
    """
    Tracks patterns in how the user reacts to certain types of observations or challenges.
    """

    __tablename__ = "reaction_pattern"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trigger_type: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # What kind of observation triggers this
    reaction_type: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # How user typically responds
    frequency: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), nullable=False
    )  # How often this pattern appears
    first_observed: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_observed: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    example_observations: Mapped[list[int] | None] = mapped_column(
        ARRAY(BigInteger)
    )  # IDs of observations showing this pattern
    reaction_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("reaction_trigger_idx", "trigger_type"),
        Index("reaction_type_idx", "reaction_type"),
        Index("reaction_frequency_idx", "frequency"),
    )


class ObservationPattern(Base):
    """
    Higher-level patterns detected across multiple observations.
    """

    __tablename__ = "observation_pattern"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    pattern_type: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # behavioral, cognitive, emotional
    description: Mapped[str] = mapped_column(Text, nullable=False)
    supporting_observations: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), nullable=False
    )  # Observation IDs
    exceptions: Mapped[list[int] | None] = mapped_column(
        ARRAY(BigInteger)
    )  # Observations that don't fit
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), nullable=False, default=0.7
    )
    validity_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validity_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    pattern_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("obs_pattern_type_idx", "pattern_type"),
        Index("obs_pattern_confidence_idx", "confidence"),
        Index("obs_pattern_validity_idx", "validity_start", "validity_end"),
    )


class BeliefCluster(Base):
    """
    Groups of related beliefs that support or depend on each other.
    """

    __tablename__ = "belief_cluster"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cluster_name: Mapped[str] = mapped_column(Text, nullable=False)
    core_beliefs: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    peripheral_beliefs: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    internal_consistency: Mapped[Decimal | None] = mapped_column(
        Numeric(3, 2)
    )  # How well beliefs align
    supporting_observations: Mapped[list[int] | None] = mapped_column(
        ARRAY(BigInteger)
    )  # Observation IDs
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_updated: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    cluster_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("belief_cluster_name_idx", "cluster_name"),
        Index("belief_cluster_consistency_idx", "internal_consistency"),
    )


class ConversationMetrics(Base):
    """
    Tracks the effectiveness and depth of conversations.
    """

    __tablename__ = "conversation_metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_id: Mapped[UUID] = mapped_column(SQLUUID(as_uuid=True), nullable=False)
    depth_score: Mapped[Decimal | None] = mapped_column(
        Numeric(3, 2)
    )  # How deep the conversation went
    breakthrough_count: Mapped[int | None] = mapped_column(Integer, default=0)
    challenge_acceptance: Mapped[Decimal | None] = mapped_column(
        Numeric(3, 2)
    )  # How well challenges were received
    new_insights: Mapped[int | None] = mapped_column(Integer, default=0)
    user_engagement: Mapped[Decimal | None] = mapped_column(
        Numeric(3, 2)
    )  # Inferred engagement level
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    observation_count: Mapped[int | None] = mapped_column(Integer, default=0)
    contradiction_count: Mapped[int | None] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    metrics_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("conv_metrics_session_idx", "session_id", unique=True),
        Index("conv_metrics_depth_idx", "depth_score"),
        Index("conv_metrics_breakthrough_idx", "breakthrough_count"),
    )
