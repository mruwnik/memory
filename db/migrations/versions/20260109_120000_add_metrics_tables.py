"""Add metrics infrastructure tables.

Creates metric_events table for storing profiling data from tasks,
MCP calls, and system metrics. Also creates a materialized view
for aggregated metrics with percentiles.

Revision ID: 20260109_120000
Revises: 20260108_180000
Create Date: 2026-01-09

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260109_120000"
down_revision = "20260108_180000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create metric_events table
    op.create_table(
        "metric_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("metric_type", sa.String(50), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("status", sa.String(50), nullable=True),
        sa.Column(
            "labels",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("value", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create indexes for common query patterns
    op.create_index(
        "idx_metric_events_timestamp",
        "metric_events",
        ["timestamp"],
    )
    # Include status in this index since summary queries group by (metric_type, name, status)
    op.create_index(
        "idx_metric_events_type_name_status",
        "metric_events",
        ["metric_type", "name", "status"],
    )
    op.create_index(
        "idx_metric_events_timestamp_type",
        "metric_events",
        ["timestamp", "metric_type"],
    )

    # Create materialized view for aggregated metrics
    # Note: Using raw SQL because Alembic doesn't have built-in MATERIALIZED VIEW support
    op.execute("""
        CREATE MATERIALIZED VIEW metric_summaries AS
        SELECT
            date_trunc('hour', timestamp) as hour,
            metric_type,
            name,
            status,
            COUNT(*) as count,
            AVG(duration_ms) as avg_duration_ms,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_ms) as p50_ms,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms) as p95_ms,
            PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY duration_ms) as p99_ms,
            MIN(duration_ms) as min_ms,
            MAX(duration_ms) as max_ms,
            AVG(value) as avg_value,
            MIN(value) as min_value,
            MAX(value) as max_value
        FROM metric_events
        WHERE timestamp > NOW() - INTERVAL '30 days'
        GROUP BY 1, 2, 3, 4
    """)

    # Create index on materialized view for faster queries
    op.execute("""
        CREATE INDEX idx_metric_summaries_hour_type
        ON metric_summaries (hour, metric_type)
    """)
    op.execute("""
        CREATE INDEX idx_metric_summaries_name
        ON metric_summaries (name)
    """)
    # UNIQUE INDEX required for REFRESH MATERIALIZED VIEW CONCURRENTLY
    # COALESCE handles NULL status values
    op.execute("""
        CREATE UNIQUE INDEX idx_metric_summaries_unique
        ON metric_summaries (hour, metric_type, name, COALESCE(status, ''))
    """)


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS metric_summaries")
    op.drop_table("metric_events")
