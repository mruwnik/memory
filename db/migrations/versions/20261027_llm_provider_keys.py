"""Per-user LLM provider keys for agent runs, and which key each run used.

Users and API keys that already hold the `agents` scope are granted
`llm_server_key`, so their agents keep running on the server's provider keys.

Revision ID: 20261027_llm_provider_keys
Revises: 20261027_trigger_match_versions
Create Date: 2026-10-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261027_llm_provider_keys"
down_revision: Union[str, None] = "20261027_trigger_match_versions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_provider_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("key_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("key_hint", sa.Text(), nullable=False),
        sa.Column(
            "is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="ok"),
        sa.Column("status_detail", sa.Text(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_cost_usd_per_day", sa.Numeric(12, 6), nullable=True),
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
        sa.UniqueConstraint(
            "user_id", "provider", "label", name="llm_provider_keys_user_provider_label"
        ),
        sa.CheckConstraint(
            "provider IN ('anthropic', 'openai')", name="llm_provider_keys_provider"
        ),
        sa.CheckConstraint("status IN ('ok', 'invalid')", name="llm_provider_keys_status"),
    )
    op.create_index(
        "llm_provider_keys_one_default",
        "llm_provider_keys",
        ["user_id", "provider"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "key_source", sa.String(10), nullable=False, server_default="server"
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "provider_key_id",
            sa.Integer(),
            sa.ForeignKey("llm_provider_keys.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "agent_runs_key_source_check", "agent_runs", "key_source IN ('server','user')"
    )
    op.create_index(
        "agent_runs_provider_key_created_idx",
        "agent_runs",
        ["provider_key_id", "created_at"],
    )
    for table in ("users", "api_keys"):
        op.execute(
            f"UPDATE {table} SET scopes = array_append(scopes, 'llm_server_key') "
            "WHERE 'agents' = ANY(scopes) AND NOT 'llm_server_key' = ANY(scopes)"
        )


def downgrade() -> None:
    for table in ("users", "api_keys"):
        op.execute(
            f"UPDATE {table} SET scopes = array_remove(scopes, 'llm_server_key') "
            "WHERE 'llm_server_key' = ANY(scopes)"
        )
    op.drop_index("agent_runs_provider_key_created_idx", table_name="agent_runs")
    op.drop_constraint("agent_runs_key_source_check", "agent_runs", type_="check")
    op.drop_column("agent_runs", "provider_key_id")
    op.drop_column("agent_runs", "key_source")
    op.drop_index("llm_provider_keys_one_default", table_name="llm_provider_keys")
    op.drop_table("llm_provider_keys")
