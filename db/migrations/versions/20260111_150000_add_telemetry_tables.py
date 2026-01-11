"""Add telemetry tables for usage tracking.

Creates a table for storing OpenTelemetry metrics and events.

Revision ID: 20260111_150000
Revises: 20260111_120000
Create Date: 2026-01-11

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260111_150000"
down_revision = "20260111_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create telemetry_events table for raw OTLP data
    op.create_table(
        "telemetry_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("session_id", sa.String(100), nullable=True),
        sa.Column("source", sa.String(100), nullable=True),
        sa.Column("tool_name", sa.String(100), nullable=True),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Indexes for telemetry_events
    op.create_index(
        "idx_telemetry_events_timestamp",
        "telemetry_events",
        ["timestamp"],
    )
    op.create_index(
        "idx_telemetry_events_user_ts",
        "telemetry_events",
        ["user_id", "timestamp"],
    )
    op.create_index(
        "idx_telemetry_events_name_ts",
        "telemetry_events",
        ["name", "timestamp"],
    )
    op.create_index(
        "idx_telemetry_events_type_name",
        "telemetry_events",
        ["event_type", "name"],
    )
    op.create_index(
        "idx_telemetry_events_session",
        "telemetry_events",
        ["session_id", "timestamp"],
    )
    op.create_index(
        "idx_telemetry_events_source",
        "telemetry_events",
        ["source", "timestamp"],
    )
    op.create_index(
        "idx_telemetry_events_attrs",
        "telemetry_events",
        ["attributes"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_table("telemetry_events")
