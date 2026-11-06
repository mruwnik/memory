"""Agent runs placed on workers: who holds a run's lease, until when, what they report
spending, and when a run was put on its current executor.

Revision ID: 20261203_agent_run_workers
Revises: 20261202_discord_msg_bot_null
Create Date: 2026-11-06
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261203_agent_run_workers"
down_revision = "20261202_discord_msg_bot_null"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column(
            "worker_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="agent_runs_worker_user_id_fkey"),
        ),
    )
    op.add_column("agent_runs", sa.Column("worker_label", sa.Text()))
    op.add_column("agent_runs", sa.Column("lease_token", sa.Text()))
    op.add_column("agent_runs", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    op.add_column(
        "agent_runs",
        sa.Column("lease_lapses", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("agent_runs", sa.Column("worker_usage", JSONB()))
    op.add_column("agent_runs", sa.Column("placed_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE agent_runs SET placed_at = coalesce(created_at, now())")
    op.alter_column(
        "agent_runs", "placed_at", nullable=False, server_default=sa.text("now()")
    )
    op.create_index(
        "agent_runs_workers_queue_idx",
        "agent_runs",
        ["created_at", "id"],
        postgresql_where=sa.text("status = 'pending' AND executor = 'workers'"),
    )
    op.create_index("agent_runs_worker_user_idx", "agent_runs", ["worker_user_id"])


def downgrade() -> None:
    op.drop_index("agent_runs_worker_user_idx", table_name="agent_runs")
    op.drop_index("agent_runs_workers_queue_idx", table_name="agent_runs")
    for column in (
        "placed_at",
        "worker_usage",
        "lease_lapses",
        "lease_expires_at",
        "lease_token",
        "worker_label",
        "worker_user_id",
    ):
        op.drop_column("agent_runs", column)
