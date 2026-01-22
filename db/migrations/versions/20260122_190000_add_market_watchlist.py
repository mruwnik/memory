"""Add market watchlist table for prediction market tracking.

Revision ID: 20260122_190000
Revises: 20260122_180000
Create Date: 2026-01-22 19:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260122_190000"
down_revision: Union[str, None] = "20260122_180000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "watched_markets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("market_id", sa.String(255), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),  # manifold, polymarket, kalshi
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("url", sa.String(500), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("price_when_added", sa.Float(), nullable=True),
        sa.Column("alert_threshold", sa.Float(), nullable=True),  # Alert if price crosses this
        sa.Column("last_price", sa.Float(), nullable=True),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "market_id", "source", name="uq_watched_market"),
    )

    # Index for efficient user lookups
    op.create_index("ix_watched_markets_user_id", "watched_markets", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_watched_markets_user_id", table_name="watched_markets")
    op.drop_table("watched_markets")
