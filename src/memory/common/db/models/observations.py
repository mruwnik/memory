"""
Agent observation models for the epistemic sparring partner system.
"""

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UUID,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from memory.common.db.models.base import Base


class ObservationContradiction(Base):
    """
    Tracks contradictions between observations.
    Can be detected automatically or reported by agents.
    """

    __tablename__ = "observation_contradiction"

    id = Column(BigInteger, primary_key=True)
    observation_1_id = Column(
        BigInteger,
        ForeignKey("agent_observation.id", ondelete="CASCADE"),
        nullable=False,
    )
    observation_2_id = Column(
        BigInteger,
        ForeignKey("agent_observation.id", ondelete="CASCADE"),
        nullable=False,
    )
    contradiction_type = Column(Text, nullable=False)  # direct, implied, temporal
    detected_at = Column(DateTime(timezone=True), server_default=func.now())
    detection_method = Column(Text, nullable=False)  # manual, automatic, agent-reported
    resolution = Column(Text)  # How it was resolved, if at all
    observation_metadata = Column(JSONB)

    # Relationships - use string references to avoid circular imports
    observation_1 = relationship(
        "AgentObservation",
        foreign_keys=[observation_1_id],
        back_populates="contradictions_as_first",
    )
    observation_2 = relationship(
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

    id = Column(BigInteger, primary_key=True)
    trigger_type = Column(
        Text, nullable=False
    )  # What kind of observation triggers this
    reaction_type = Column(Text, nullable=False)  # How user typically responds
    frequency = Column(Numeric(3, 2), nullable=False)  # How often this pattern appears
    first_observed = Column(DateTime(timezone=True), server_default=func.now())
    last_observed = Column(DateTime(timezone=True), server_default=func.now())
    example_observations = Column(
        ARRAY(BigInteger)
    )  # IDs of observations showing this pattern
    reaction_metadata = Column(JSONB)

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

    id = Column(BigInteger, primary_key=True)
    pattern_type = Column(Text, nullable=False)  # behavioral, cognitive, emotional
    description = Column(Text, nullable=False)
    supporting_observations = Column(
        ARRAY(BigInteger), nullable=False
    )  # Observation IDs
    exceptions = Column(ARRAY(BigInteger))  # Observations that don't fit
    confidence = Column(Numeric(3, 2), nullable=False, default=0.7)
    validity_start = Column(DateTime(timezone=True))
    validity_end = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
    pattern_metadata = Column(JSONB)

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

    id = Column(BigInteger, primary_key=True)
    cluster_name = Column(Text, nullable=False)
    core_beliefs = Column(ARRAY(Text), nullable=False)
    peripheral_beliefs = Column(ARRAY(Text))
    internal_consistency = Column(Numeric(3, 2))  # How well beliefs align
    supporting_observations = Column(ARRAY(BigInteger))  # Observation IDs
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_updated = Column(DateTime(timezone=True), server_default=func.now())
    cluster_metadata = Column(JSONB)

    __table_args__ = (
        Index("belief_cluster_name_idx", "cluster_name"),
        Index("belief_cluster_consistency_idx", "internal_consistency"),
    )


class ConversationMetrics(Base):
    """
    Tracks the effectiveness and depth of conversations.
    """

    __tablename__ = "conversation_metrics"

    id = Column(BigInteger, primary_key=True)
    session_id = Column(UUID(as_uuid=True), nullable=False)
    depth_score = Column(Numeric(3, 2))  # How deep the conversation went
    breakthrough_count = Column(Integer, default=0)
    challenge_acceptance = Column(Numeric(3, 2))  # How well challenges were received
    new_insights = Column(Integer, default=0)
    user_engagement = Column(Numeric(3, 2))  # Inferred engagement level
    duration_minutes = Column(Integer)
    observation_count = Column(Integer, default=0)
    contradiction_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    metrics_metadata = Column(JSONB)

    __table_args__ = (
        Index("conv_metrics_session_idx", "session_id", unique=True),
        Index("conv_metrics_depth_idx", "depth_score"),
        Index("conv_metrics_breakthrough_idx", "breakthrough_count"),
    )
