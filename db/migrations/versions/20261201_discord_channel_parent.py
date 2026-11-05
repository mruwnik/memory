"""Discord threads record their parent channel.

Revision ID: 20261201_discord_channel_parent
Revises: 20261130_beeper
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "20261201_discord_channel_parent"
down_revision = "20261130_beeper"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("discord_channels", sa.Column("parent_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("discord_channels", "parent_id")
