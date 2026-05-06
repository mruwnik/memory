"""Add bot_id FK to discord_servers for per-user isolation.

Revision ID: 20260501_discord_server_bot_id
Revises: 20260216_report_connect_urls
Create Date: 2026-05-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260501_discord_server_bot_id"
down_revision: Union[str, None] = "20260216_report_connect_urls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "discord_servers",
        sa.Column(
            "bot_id",
            sa.BigInteger(),
            sa.ForeignKey("discord_bots.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("discord_servers_bot_idx", "discord_servers", ["bot_id"])


def downgrade() -> None:
    op.drop_index("discord_servers_bot_idx", table_name="discord_servers")
    op.drop_column("discord_servers", "bot_id")
