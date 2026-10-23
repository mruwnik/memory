"""Agent triggers (event-driven starts of agent definitions) and the items each has matched.

Revision ID: 20261025_agent_triggers
Revises: 20261024_agent_runs
Create Date: 2026-10-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261025_agent_triggers"
down_revision: Union[str, None] = "20261024_agent_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_triggers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "definition_id",
            sa.Integer(),
            sa.ForeignKey("agent_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("state", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "api_key_id",
            sa.Integer(),
            sa.ForeignKey("api_keys.id", ondelete="SET NULL"),
        ),
        sa.Column("via_api_key", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "active_since",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
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
        sa.CheckConstraint("kind IN ('ingest')", name="agent_triggers_kind_check"),
    )
    op.create_index("agent_triggers_definition_idx", "agent_triggers", ["definition_id"])
    op.create_table(
        "agent_trigger_matches",
        sa.Column(
            "trigger_id",
            sa.Integer(),
            sa.ForeignKey("agent_triggers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "item_id",
            sa.BigInteger(),
            sa.ForeignKey("source_item.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "agent_trigger_matches_item_idx", "agent_trigger_matches", ["item_id"]
    )
    op.create_index(
        "agent_trigger_matches_created_idx", "agent_trigger_matches", ["created_at"]
    )
    op.create_index(
        "agent_triggers_enabled_kind_idx",
        "agent_triggers",
        ["kind"],
        postgresql_where=sa.text("enabled"),
    )


def downgrade() -> None:
    op.drop_table("agent_trigger_matches")
    op.drop_index("agent_triggers_enabled_kind_idx", table_name="agent_triggers")
    op.drop_index("agent_triggers_definition_idx", table_name="agent_triggers")
    op.drop_table("agent_triggers")
