"""seperate_user__models

Revision ID: 35a2c1b610b6
Revises: 7c6169fba146
Create Date: 2025-10-20 01:48:58.537881

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "35a2c1b610b6"
down_revision: Union[str, None] = "7c6169fba146"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "discord_message", sa.Column("from_id", sa.BigInteger(), nullable=False)
    )
    op.add_column(
        "discord_message", sa.Column("recipient_id", sa.BigInteger(), nullable=False)
    )
    op.drop_index("discord_message_user_idx", table_name="discord_message")
    op.create_index(
        "discord_message_from_idx", "discord_message", ["from_id"], unique=False
    )
    op.create_index(
        "discord_message_recipient_idx",
        "discord_message",
        ["recipient_id"],
        unique=False,
    )
    op.drop_constraint(
        "discord_message_discord_user_id_fkey", "discord_message", type_="foreignkey"
    )
    op.create_foreign_key(
        "discord_message_from_id_fkey",
        "discord_message",
        "discord_users",
        ["from_id"],
        ["id"],
    )
    op.create_foreign_key(
        "discord_message_recipient_id_fkey",
        "discord_message",
        "discord_users",
        ["recipient_id"],
        ["id"],
    )
    op.drop_column("discord_message", "discord_user_id")
    op.add_column(
        "scheduled_llm_calls",
        sa.Column("discord_channel_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "scheduled_llm_calls",
        sa.Column("discord_user_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "scheduled_llm_calls_discord_user_id_fkey",
        "scheduled_llm_calls",
        "discord_users",
        ["discord_user_id"],
        ["id"],
    )
    op.create_foreign_key(
        "scheduled_llm_calls_discord_channel_id_fkey",
        "scheduled_llm_calls",
        "discord_channels",
        ["discord_channel_id"],
        ["id"],
    )
    op.drop_column("scheduled_llm_calls", "discord_user")
    op.drop_column("scheduled_llm_calls", "discord_channel")
    op.add_column(
        "users",
        sa.Column("user_type", sa.String(), nullable=False, server_default="human"),
    )
    op.add_column("users", sa.Column("api_key", sa.String(), nullable=True))
    op.alter_column("users", "password_hash", existing_type=sa.VARCHAR(), nullable=True)
    op.create_unique_constraint("users_api_key_key", "users", ["api_key"])
    op.drop_column("users", "discord_user_id")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("discord_user_id", sa.VARCHAR(), autoincrement=False, nullable=True),
    )
    op.drop_constraint("users_api_key_key", "users", type_="unique")
    op.alter_column(
        "users", "password_hash", existing_type=sa.VARCHAR(), nullable=False
    )
    op.drop_column("users", "api_key")
    op.drop_column("users", "user_type")
    op.add_column(
        "scheduled_llm_calls",
        sa.Column("discord_channel", sa.VARCHAR(), autoincrement=False, nullable=True),
    )
    op.add_column(
        "scheduled_llm_calls",
        sa.Column("discord_user", sa.VARCHAR(), autoincrement=False, nullable=True),
    )
    op.drop_constraint(
        "scheduled_llm_calls_discord_user_id_fkey",
        "scheduled_llm_calls",
        type_="foreignkey",
    )
    op.drop_constraint(
        "scheduled_llm_calls_discord_channel_id_fkey",
        "scheduled_llm_calls",
        type_="foreignkey",
    )
    op.drop_column("scheduled_llm_calls", "discord_user_id")
    op.drop_column("scheduled_llm_calls", "discord_channel_id")
    op.add_column(
        "discord_message",
        sa.Column("discord_user_id", sa.BIGINT(), autoincrement=False, nullable=False),
    )
    op.drop_constraint(
        "discord_message_from_id_fkey", "discord_message", type_="foreignkey"
    )
    op.drop_constraint(
        "discord_message_recipient_id_fkey", "discord_message", type_="foreignkey"
    )
    op.create_foreign_key(
        "discord_message_discord_user_id_fkey",
        "discord_message",
        "discord_users",
        ["discord_user_id"],
        ["id"],
    )
    op.drop_index("discord_message_recipient_idx", table_name="discord_message")
    op.drop_index("discord_message_from_idx", table_name="discord_message")
    op.create_index(
        "discord_message_user_idx", "discord_message", ["discord_user_id"], unique=False
    )
    op.drop_column("discord_message", "recipient_id")
    op.drop_column("discord_message", "from_id")
