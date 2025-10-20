"""add_discord_models

Revision ID: 7c6169fba146
Revises: c86079073c1d
Create Date: 2025-10-13 14:21:01.080948

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7c6169fba146"
down_revision: Union[str, None] = "c86079073c1d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "discord_servers",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("member_count", sa.Integer(), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "track_messages", sa.Boolean(), server_default="true", nullable=False
        ),
        sa.Column("ignore_messages", sa.Boolean(), nullable=True),
        sa.Column(
            "allowed_tools", sa.ARRAY(sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "disallowed_tools", sa.ARRAY(sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "discord_servers_active_idx",
        "discord_servers",
        ["track_messages", "last_sync_at"],
        unique=False,
    )
    op.create_table(
        "discord_channels",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("server_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("channel_type", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "track_messages", sa.Boolean(), server_default="true", nullable=False
        ),
        sa.Column("ignore_messages", sa.Boolean(), nullable=True),
        sa.Column(
            "allowed_tools", sa.ARRAY(sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "disallowed_tools", sa.ARRAY(sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["discord_servers.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "discord_channels_server_idx", "discord_channels", ["server_id"], unique=False
    )
    op.create_table(
        "discord_users",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("system_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "track_messages", sa.Boolean(), server_default="true", nullable=False
        ),
        sa.Column("ignore_messages", sa.Boolean(), nullable=True),
        sa.Column(
            "allowed_tools", sa.ARRAY(sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "disallowed_tools", sa.ARRAY(sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["system_user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "discord_users_system_user_idx",
        "discord_users",
        ["system_user_id"],
        unique=False,
    )
    op.create_table(
        "discord_message",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("server_id", sa.BigInteger(), nullable=True),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("discord_user_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("message_type", sa.Text(), server_default="default", nullable=True),
        sa.Column("reply_to_message_id", sa.BigInteger(), nullable=True),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["discord_channels.id"],
        ),
        sa.ForeignKeyConstraint(
            ["discord_user_id"],
            ["discord_users.id"],
        ),
        sa.ForeignKeyConstraint(["id"], ["source_item.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["discord_servers.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "discord_message_discord_id_idx", "discord_message", ["message_id"], unique=True
    )
    op.create_index(
        "discord_message_server_channel_idx",
        "discord_message",
        ["server_id", "channel_id"],
        unique=False,
    )
    op.create_index(
        "discord_message_user_idx", "discord_message", ["discord_user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("discord_message_user_idx", table_name="discord_message")
    op.drop_index("discord_message_server_channel_idx", table_name="discord_message")
    op.drop_index("discord_message_discord_id_idx", table_name="discord_message")
    op.drop_table("discord_message")
    op.drop_index("discord_users_system_user_idx", table_name="discord_users")
    op.drop_table("discord_users")
    op.drop_index("discord_channels_server_idx", table_name="discord_channels")
    op.drop_table("discord_channels")
    op.drop_index("discord_servers_active_idx", table_name="discord_servers")
    op.drop_table("discord_servers")
