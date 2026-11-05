"""Discord bots record when they last listed their servers.

Revision ID: 20261129_discord_bot_last_sync
Revises: 20261128_orphaned_source_items
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "20261129_discord_bot_last_sync"
down_revision = "20261128_orphaned_source_items"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("discord_bots", sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("discord_bots", "last_sync_at")
