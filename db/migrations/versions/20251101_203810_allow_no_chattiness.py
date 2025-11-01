"""allow no chattiness

Revision ID: 2024235e37e7
Revises: 7dc03dbf184c
Create Date: 2025-11-01 20:38:10.849651

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2024235e37e7"
down_revision: Union[str, None] = "7dc03dbf184c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "discord_channels",
        "chattiness_threshold",
        existing_type=sa.INTEGER(),
        nullable=True,
        existing_server_default=sa.text("50"),
    )
    op.drop_column("discord_channels", "track_messages")
    op.alter_column(
        "discord_servers",
        "chattiness_threshold",
        existing_type=sa.INTEGER(),
        nullable=True,
        existing_server_default=sa.text("50"),
    )
    op.drop_index("discord_servers_active_idx", table_name="discord_servers")
    op.create_index(
        "discord_servers_active_idx",
        "discord_servers",
        ["ignore_messages", "last_sync_at"],
        unique=False,
    )
    op.drop_column("discord_servers", "track_messages")
    op.alter_column(
        "discord_users",
        "chattiness_threshold",
        existing_type=sa.INTEGER(),
        nullable=True,
        existing_server_default=sa.text("50"),
    )
    op.drop_column("discord_users", "track_messages")


def downgrade() -> None:
    op.add_column(
        "discord_users",
        sa.Column(
            "track_messages",
            sa.BOOLEAN(),
            server_default=sa.text("true"),
            autoincrement=False,
            nullable=False,
        ),
    )
    op.alter_column(
        "discord_users",
        "chattiness_threshold",
        existing_type=sa.INTEGER(),
        nullable=False,
        existing_server_default=sa.text("50"),
    )
    op.add_column(
        "discord_servers",
        sa.Column(
            "track_messages",
            sa.BOOLEAN(),
            server_default=sa.text("true"),
            autoincrement=False,
            nullable=False,
        ),
    )
    op.drop_index("discord_servers_active_idx", table_name="discord_servers")
    op.create_index(
        "discord_servers_active_idx",
        "discord_servers",
        ["track_messages", "last_sync_at"],
        unique=False,
    )
    op.alter_column(
        "discord_servers",
        "chattiness_threshold",
        existing_type=sa.INTEGER(),
        nullable=False,
        existing_server_default=sa.text("50"),
    )
    op.add_column(
        "discord_channels",
        sa.Column(
            "track_messages",
            sa.BOOLEAN(),
            server_default=sa.text("true"),
            autoincrement=False,
            nullable=False,
        ),
    )
    op.alter_column(
        "discord_channels",
        "chattiness_threshold",
        existing_type=sa.INTEGER(),
        nullable=False,
        existing_server_default=sa.text("50"),
    )
