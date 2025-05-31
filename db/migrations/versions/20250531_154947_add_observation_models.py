"""Add observation models

Revision ID: 6554eb260176
Revises: 2524646f56f6
Create Date: 2025-05-31 15:49:47.579256

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "6554eb260176"
down_revision: Union[str, None] = "2524646f56f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "belief_cluster",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("cluster_name", sa.Text(), nullable=False),
        sa.Column("core_beliefs", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("peripheral_beliefs", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column(
            "internal_consistency", sa.Numeric(precision=3, scale=2), nullable=True
        ),
        sa.Column("supporting_observations", sa.ARRAY(sa.BigInteger()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "last_updated",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "cluster_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "belief_cluster_consistency_idx",
        "belief_cluster",
        ["internal_consistency"],
        unique=False,
    )
    op.create_index(
        "belief_cluster_name_idx", "belief_cluster", ["cluster_name"], unique=False
    )
    op.create_table(
        "conversation_metrics",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("depth_score", sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column("breakthrough_count", sa.Integer(), nullable=True),
        sa.Column(
            "challenge_acceptance", sa.Numeric(precision=3, scale=2), nullable=True
        ),
        sa.Column("new_insights", sa.Integer(), nullable=True),
        sa.Column("user_engagement", sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("observation_count", sa.Integer(), nullable=True),
        sa.Column("contradiction_count", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "metrics_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "conv_metrics_breakthrough_idx",
        "conversation_metrics",
        ["breakthrough_count"],
        unique=False,
    )
    op.create_index(
        "conv_metrics_depth_idx", "conversation_metrics", ["depth_score"], unique=False
    )
    op.create_index(
        "conv_metrics_session_idx", "conversation_metrics", ["session_id"], unique=True
    )
    op.create_table(
        "observation_pattern",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("pattern_type", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("supporting_observations", sa.ARRAY(sa.BigInteger()), nullable=False),
        sa.Column("exceptions", sa.ARRAY(sa.BigInteger()), nullable=True),
        sa.Column("confidence", sa.Numeric(precision=3, scale=2), nullable=False),
        sa.Column("validity_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validity_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "pattern_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "obs_pattern_confidence_idx",
        "observation_pattern",
        ["confidence"],
        unique=False,
    )
    op.create_index(
        "obs_pattern_type_idx", "observation_pattern", ["pattern_type"], unique=False
    )
    op.create_index(
        "obs_pattern_validity_idx",
        "observation_pattern",
        ["validity_start", "validity_end"],
        unique=False,
    )
    op.create_table(
        "reaction_pattern",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("trigger_type", sa.Text(), nullable=False),
        sa.Column("reaction_type", sa.Text(), nullable=False),
        sa.Column("frequency", sa.Numeric(precision=3, scale=2), nullable=False),
        sa.Column(
            "first_observed",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "last_observed",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("example_observations", sa.ARRAY(sa.BigInteger()), nullable=True),
        sa.Column(
            "reaction_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "reaction_frequency_idx", "reaction_pattern", ["frequency"], unique=False
    )
    op.create_index(
        "reaction_trigger_idx", "reaction_pattern", ["trigger_type"], unique=False
    )
    op.create_index(
        "reaction_type_idx", "reaction_pattern", ["reaction_type"], unique=False
    )
    op.create_table(
        "agent_observation",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=True),
        sa.Column("observation_type", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=3, scale=2), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("agent_model", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["id"], ["source_item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "agent_obs_confidence_idx", "agent_observation", ["confidence"], unique=False
    )
    op.create_index(
        "agent_obs_model_idx", "agent_observation", ["agent_model"], unique=False
    )
    op.create_index(
        "agent_obs_session_idx", "agent_observation", ["session_id"], unique=False
    )
    op.create_index(
        "agent_obs_subject_idx", "agent_observation", ["subject"], unique=False
    )
    op.create_index(
        "agent_obs_type_idx", "agent_observation", ["observation_type"], unique=False
    )
    op.create_table(
        "observation_contradiction",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("observation_1_id", sa.BigInteger(), nullable=False),
        sa.Column("observation_2_id", sa.BigInteger(), nullable=False),
        sa.Column("contradiction_type", sa.Text(), nullable=False),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("detection_method", sa.Text(), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column(
            "observation_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["observation_1_id"], ["agent_observation.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["observation_2_id"], ["agent_observation.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "obs_contra_method_idx",
        "observation_contradiction",
        ["detection_method"],
        unique=False,
    )
    op.create_index(
        "obs_contra_obs1_idx",
        "observation_contradiction",
        ["observation_1_id"],
        unique=False,
    )
    op.create_index(
        "obs_contra_obs2_idx",
        "observation_contradiction",
        ["observation_2_id"],
        unique=False,
    )
    op.create_index(
        "obs_contra_type_idx",
        "observation_contradiction",
        ["contradiction_type"],
        unique=False,
    )
    op.add_column("chunk", sa.Column("collection_name", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("chunk", "collection_name")
    op.drop_index("obs_contra_type_idx", table_name="observation_contradiction")
    op.drop_index("obs_contra_obs2_idx", table_name="observation_contradiction")
    op.drop_index("obs_contra_obs1_idx", table_name="observation_contradiction")
    op.drop_index("obs_contra_method_idx", table_name="observation_contradiction")
    op.drop_table("observation_contradiction")
    op.drop_index("agent_obs_type_idx", table_name="agent_observation")
    op.drop_index("agent_obs_subject_idx", table_name="agent_observation")
    op.drop_index("agent_obs_session_idx", table_name="agent_observation")
    op.drop_index("agent_obs_model_idx", table_name="agent_observation")
    op.drop_index("agent_obs_confidence_idx", table_name="agent_observation")
    op.drop_table("agent_observation")
    op.drop_index("reaction_type_idx", table_name="reaction_pattern")
    op.drop_index("reaction_trigger_idx", table_name="reaction_pattern")
    op.drop_index("reaction_frequency_idx", table_name="reaction_pattern")
    op.drop_table("reaction_pattern")
    op.drop_index("obs_pattern_validity_idx", table_name="observation_pattern")
    op.drop_index("obs_pattern_type_idx", table_name="observation_pattern")
    op.drop_index("obs_pattern_confidence_idx", table_name="observation_pattern")
    op.drop_table("observation_pattern")
    op.drop_index("conv_metrics_session_idx", table_name="conversation_metrics")
    op.drop_index("conv_metrics_depth_idx", table_name="conversation_metrics")
    op.drop_index("conv_metrics_breakthrough_idx", table_name="conversation_metrics")
    op.drop_table("conversation_metrics")
    op.drop_index("belief_cluster_name_idx", table_name="belief_cluster")
    op.drop_index("belief_cluster_consistency_idx", table_name="belief_cluster")
    op.drop_table("belief_cluster")
