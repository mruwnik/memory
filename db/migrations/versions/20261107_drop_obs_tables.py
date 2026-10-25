"""Drop the observation tables nothing reads or writes.

Revision ID: 20261107_drop_obs_tables
Revises: 20261106_unique_person_user
Create Date: 2026-11-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261107_drop_obs_tables"
down_revision: Union[str, None] = "20261106_unique_person_user"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("reaction_pattern", "observation_pattern", "belief_cluster", "conversation_metrics")


def upgrade() -> None:
    for table in TABLES:
        op.drop_table(table)


def downgrade() -> None:
    op.create_table(
        "belief_cluster",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("cluster_name", sa.Text(), nullable=False),
        sa.Column("core_beliefs", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("peripheral_beliefs", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("internal_consistency", sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column("supporting_observations", sa.ARRAY(sa.BigInteger()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("last_updated", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("cluster_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("belief_cluster_consistency_idx", "belief_cluster", ["internal_consistency"])
    op.create_index("belief_cluster_name_idx", "belief_cluster", ["cluster_name"])

    op.create_table(
        "conversation_metrics",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("depth_score", sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column("breakthrough_count", sa.Integer(), nullable=True),
        sa.Column("challenge_acceptance", sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column("new_insights", sa.Integer(), nullable=True),
        sa.Column("user_engagement", sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("observation_count", sa.Integer(), nullable=True),
        sa.Column("contradiction_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("metrics_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("conv_metrics_breakthrough_idx", "conversation_metrics", ["breakthrough_count"])
    op.create_index("conv_metrics_depth_idx", "conversation_metrics", ["depth_score"])
    op.create_index("conv_metrics_session_idx", "conversation_metrics", ["session_id"], unique=True)

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
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("pattern_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("obs_pattern_confidence_idx", "observation_pattern", ["confidence"])
    op.create_index("obs_pattern_type_idx", "observation_pattern", ["pattern_type"])
    op.create_index("obs_pattern_validity_idx", "observation_pattern", ["validity_start", "validity_end"])

    op.create_table(
        "reaction_pattern",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("trigger_type", sa.Text(), nullable=False),
        sa.Column("reaction_type", sa.Text(), nullable=False),
        sa.Column("frequency", sa.Numeric(precision=3, scale=2), nullable=False),
        sa.Column("first_observed", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("last_observed", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("example_observations", sa.ARRAY(sa.BigInteger()), nullable=True),
        sa.Column("reaction_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("reaction_frequency_idx", "reaction_pattern", ["frequency"])
    op.create_index("reaction_trigger_idx", "reaction_pattern", ["trigger_type"])
    op.create_index("reaction_type_idx", "reaction_pattern", ["reaction_type"])
