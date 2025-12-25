"""Add proactive check-in fields to Discord entities

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2025-12-24 16:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "d0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add proactive fields to all MessageProcessor tables
    for table in ["discord_servers", "discord_channels", "discord_users"]:
        op.add_column(
            table,
            sa.Column("proactive_cron", sa.Text(), nullable=True),
        )
        op.add_column(
            table,
            sa.Column("proactive_prompt", sa.Text(), nullable=True),
        )
        op.add_column(
            table,
            sa.Column(
                "last_proactive_at", sa.DateTime(timezone=True), nullable=True
            ),
        )


def downgrade() -> None:
    for table in ["discord_servers", "discord_channels", "discord_users"]:
        op.drop_column(table, "last_proactive_at")
        op.drop_column(table, "proactive_prompt")
        op.drop_column(table, "proactive_cron")
