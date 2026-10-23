"""Agent definitions, runs and transcript steps; agent cost caps on API keys.

Revision ID: 20261024_agent_runs
Revises: 20261023_summary_requests
Create Date: 2026-10-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261024_agent_runs"
down_revision: Union[str, None] = "20261023_summary_requests"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_definitions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("user_id", "name", name="agent_definitions_user_name_key"),
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "definition_id",
            sa.Integer(),
            sa.ForeignKey("agent_definitions.id", ondelete="SET NULL"),
        ),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column("input", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("error", sa.Text()),
        sa.Column("usage", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("max_cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("num_turns", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trigger_kind", sa.String(20), nullable=False),
        sa.Column(
            "trigger_ref", postgresql.JSONB(), nullable=False, server_default="{}"
        ),
        sa.Column("dedupe_key", sa.Text()),
        sa.Column(
            "api_key_id",
            sa.Integer(),
            sa.ForeignKey("api_keys.id", ondelete="SET NULL"),
        ),
        sa.Column("scopes", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "parent_run_id",
            sa.String(),
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
        ),
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("starred", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("steps_purged_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('pending','running','done','failed','budget_exceeded','cancelled')",
            name="agent_runs_status_check",
        ),
    )
    op.create_index(
        "agent_runs_user_created_idx",
        "agent_runs",
        ["user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "agent_runs_definition_created_idx",
        "agent_runs",
        ["definition_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "agent_runs_active_idx",
        "agent_runs",
        ["status"],
        postgresql_where=sa.text("status IN ('pending','running')"),
    )
    op.create_index(
        "agent_runs_api_key_created_idx", "agent_runs", ["api_key_id", "created_at"]
    )
    op.create_index(
        "agent_runs_definition_dedupe_key",
        "agent_runs",
        ["definition_id", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text("dedupe_key IS NOT NULL"),
    )
    op.create_table(
        "agent_run_steps",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("replay", postgresql.JSONB()),
        sa.Column("usage", postgresql.JSONB()),
        sa.Column("cost_usd", sa.Numeric(12, 6)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("run_id", "seq", name="agent_run_steps_run_seq_key"),
        sa.CheckConstraint(
            "kind IN ('user','assistant','tool_results','notice')",
            name="agent_run_steps_kind_check",
        ),
    )
    op.add_column(
        "api_keys",
        sa.Column("agent_max_run_cost_usd", sa.Numeric(12, 6), nullable=True),
    )
    op.add_column(
        "api_keys",
        sa.Column("agent_max_daily_cost_usd", sa.Numeric(12, 6), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("api_keys", "agent_max_daily_cost_usd")
    op.drop_column("api_keys", "agent_max_run_cost_usd")
    op.drop_table("agent_run_steps")
    op.drop_table("agent_runs")
    op.drop_table("agent_definitions")
