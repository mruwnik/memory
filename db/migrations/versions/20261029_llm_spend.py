"""Hourly per-key spend of LLM calls that keep no run row (search, ingest).

Revision ID: 20261029_llm_spend
Revises: 20261029_key_names_per_user
Create Date: 2026-10-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261029_llm_spend"
down_revision: Union[str, None] = "20261029_key_names_per_user"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_spend",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "provider_key_id",
            sa.Integer(),
            sa.ForeignKey("llm_provider_keys.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent", sa.Text(), nullable=False),
        sa.Column("hour", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("calls", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("provider_key_id", "agent", "hour", name="llm_spend_bucket"),
    )


def downgrade() -> None:
    op.drop_table("llm_spend")
