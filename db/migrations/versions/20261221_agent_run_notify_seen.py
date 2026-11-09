"""Agent runs can notify their owner when they end, and remember when their owner
first saw them ended, so agent_list_runs can list the unseen ones.

Runs started by their owner that ended before this migration count as seen.

Revision ID: 20261221_agent_run_notify_seen
Revises: 20261220_github_incomplete_since
Create Date: 2026-11-09
"""

import sqlalchemy as sa
from alembic import op

revision = "20261221_agent_run_notify_seen"
down_revision = "20261220_github_incomplete_since"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("notify_on_finish", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("agent_runs", sa.Column("notified_at", sa.DateTime(timezone=True)))
    op.add_column("agent_runs", sa.Column("seen_at", sa.DateTime(timezone=True)))
    # Only the rows agent_list_runs(unseen=true) reads.
    op.execute(
        "UPDATE agent_runs SET seen_at = finished_at WHERE finished_at IS NOT NULL "
        "AND parent_run_id IS NULL AND trigger_kind IN ('mcp', 'manual')"
    )
    op.create_index(
        "agent_runs_notify_pending_idx",
        "agent_runs",
        ["finished_at"],
        postgresql_where=sa.text("notify_on_finish AND notified_at IS NULL"),
    )
    op.create_index(
        "agent_runs_unseen_idx",
        "agent_runs",
        ["user_id", "finished_at"],
        postgresql_where=sa.text(
            "seen_at IS NULL AND parent_run_id IS NULL AND trigger_kind IN ('mcp', 'manual')"
        ),
    )


def downgrade() -> None:
    op.drop_index("agent_runs_unseen_idx", table_name="agent_runs")
    op.drop_index("agent_runs_notify_pending_idx", table_name="agent_runs")
    op.drop_column("agent_runs", "seen_at")
    op.drop_column("agent_runs", "notified_at")
    op.drop_column("agent_runs", "notify_on_finish")
