"""Simplify Discord schema for collection-only use.

This migration:
1. Creates discord_bots table for bot management
2. Creates discord_bot_users association table for many-to-many User <-> DiscordBot
3. Removes MessageProcessor fields (chattiness, tools, proactive, summary) from
   discord_servers, discord_channels, discord_users
4. Adds simple collect_messages flags
5. Restructures discord_message to reference bot and author
6. Converts any 'discord_bot' user types to 'bot' (DiscordBotUser class was removed)

Revision ID: 20260121_120000
Revises: 20260118_120000
Create Date: 2026-01-21

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision = "20260121_120000"
down_revision = "20260118_120000"
branch_labels = None
depends_on = None


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in the database."""
    conn = op.get_bind()
    result = conn.execute(
        sa.text("""
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = :table AND column_name = :column
            )
        """),
        {"table": table_name, "column": column_name},
    )
    return result.scalar()


def index_exists(index_name: str) -> bool:
    """Check if an index exists in the database."""
    conn = op.get_bind()
    result = conn.execute(
        sa.text("""
            SELECT EXISTS (
                SELECT 1
                FROM pg_indexes
                WHERE indexname = :index_name
            )
        """),
        {"index_name": index_name},
    )
    return result.scalar()


def constraint_exists(table_name: str, constraint_name: str) -> bool:
    """Check if a constraint exists in the database."""
    conn = op.get_bind()
    result = conn.execute(
        sa.text("""
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.table_constraints
                WHERE table_name = :table AND constraint_name = :constraint
            )
        """),
        {"table": table_name, "constraint": constraint_name},
    )
    return result.scalar()


def table_exists(table_name: str) -> bool:
    """Check if a table exists in the database."""
    conn = op.get_bind()
    result = conn.execute(
        sa.text("""
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_name = :table
            )
        """),
        {"table": table_name},
    )
    return result.scalar()


def drop_column_if_exists(table_name: str, column_name: str) -> None:
    """Drop a column only if it exists."""
    if column_exists(table_name, column_name):
        op.drop_column(table_name, column_name)


def drop_index_if_exists(index_name: str, table_name: str) -> None:
    """Drop an index only if it exists."""
    if index_exists(index_name):
        op.drop_index(index_name, table_name=table_name)


def drop_constraint_if_exists(
    constraint_name: str, table_name: str, type_: str
) -> None:
    """Drop a constraint only if it exists."""
    if constraint_exists(table_name, constraint_name):
        op.drop_constraint(constraint_name, table_name, type_=type_)


def upgrade() -> None:
    # 0. Migrate discord_bot user types to bot (the DiscordBotUser class was removed)
    op.execute("UPDATE users SET user_type = 'bot' WHERE user_type = 'discord_bot'")

    # 1. Create discord_bots table (if not exists)
    if not table_exists("discord_bots"):
        op.create_table(
            "discord_bots",
            sa.Column("id", sa.BigInteger(), nullable=False),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("token_encrypted", sa.LargeBinary(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
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
            sa.PrimaryKeyConstraint("id"),
        )

    # 2. Create discord_bot_users association table (if not exists)
    if not table_exists("discord_bot_users"):
        op.create_table(
            "discord_bot_users",
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("bot_id", sa.BigInteger(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["bot_id"], ["discord_bots.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("user_id", "bot_id"),
        )

    # 3. Modify discord_servers - add collect_messages, drop old fields
    if not column_exists("discord_servers", "collect_messages"):
        op.add_column(
            "discord_servers",
            sa.Column(
                "collect_messages", sa.Boolean(), nullable=False, server_default="false"
            ),
        )

    # Drop old MessageProcessor columns from discord_servers
    drop_column_if_exists("discord_servers", "ignore_messages")
    drop_column_if_exists("discord_servers", "allowed_tools")
    drop_column_if_exists("discord_servers", "disallowed_tools")
    drop_column_if_exists("discord_servers", "summary")
    drop_column_if_exists("discord_servers", "system_prompt")
    drop_column_if_exists("discord_servers", "chattiness_threshold")
    drop_column_if_exists("discord_servers", "proactive_cron")
    drop_column_if_exists("discord_servers", "proactive_prompt")
    drop_column_if_exists("discord_servers", "last_proactive_at")

    # Drop and recreate index
    drop_index_if_exists("discord_servers_active_idx", table_name="discord_servers")
    if not index_exists("discord_servers_collect_idx"):
        op.create_index(
            "discord_servers_collect_idx", "discord_servers", ["collect_messages"]
        )

    # 4. Modify discord_channels - add collect_messages (nullable for inheritance), drop old fields
    if not column_exists("discord_channels", "collect_messages"):
        op.add_column(
            "discord_channels",
            sa.Column("collect_messages", sa.Boolean(), nullable=True),
        )

    # Drop old MessageProcessor columns from discord_channels
    drop_column_if_exists("discord_channels", "ignore_messages")
    drop_column_if_exists("discord_channels", "allowed_tools")
    drop_column_if_exists("discord_channels", "disallowed_tools")
    drop_column_if_exists("discord_channels", "summary")
    drop_column_if_exists("discord_channels", "system_prompt")
    drop_column_if_exists("discord_channels", "chattiness_threshold")
    drop_column_if_exists("discord_channels", "proactive_cron")
    drop_column_if_exists("discord_channels", "proactive_prompt")
    drop_column_if_exists("discord_channels", "last_proactive_at")

    # 5. Modify discord_users - drop old fields
    drop_column_if_exists("discord_users", "ignore_messages")
    drop_column_if_exists("discord_users", "allowed_tools")
    drop_column_if_exists("discord_users", "disallowed_tools")
    drop_column_if_exists("discord_users", "summary")
    drop_column_if_exists("discord_users", "system_prompt")
    drop_column_if_exists("discord_users", "chattiness_threshold")
    drop_column_if_exists("discord_users", "proactive_cron")
    drop_column_if_exists("discord_users", "proactive_prompt")
    drop_column_if_exists("discord_users", "last_proactive_at")

    # 6. Modify discord_message - add new fields, rename from_id to author_id, drop recipient_id
    # Add bot_id column (will set up FK after creating a default bot if needed)
    if not column_exists("discord_message", "bot_id"):
        op.add_column(
            "discord_message",
            sa.Column("bot_id", sa.BigInteger(), nullable=True),
        )

    # Add author_id and copy data from from_id
    if not column_exists("discord_message", "author_id"):
        op.add_column(
            "discord_message",
            sa.Column("author_id", sa.BigInteger(), nullable=True),
        )

        # Copy from_id values to author_id (only if from_id exists)
        if column_exists("discord_message", "from_id"):
            op.execute("UPDATE discord_message SET author_id = from_id")

    # Add other new columns
    if not column_exists("discord_message", "is_pinned"):
        op.add_column(
            "discord_message",
            sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default="false"),
        )
    if not column_exists("discord_message", "reactions"):
        op.add_column(
            "discord_message",
            sa.Column("reactions", JSONB(), nullable=True),
        )
    if not column_exists("discord_message", "embeds"):
        op.add_column(
            "discord_message",
            sa.Column("embeds", JSONB(), nullable=True),
        )
    if not column_exists("discord_message", "attachments"):
        op.add_column(
            "discord_message",
            sa.Column("attachments", JSONB(), nullable=True),
        )

    # Drop old indexes and constraints
    drop_index_if_exists("discord_message_from_idx", table_name="discord_message")
    drop_index_if_exists("discord_message_recipient_idx", table_name="discord_message")
    drop_constraint_if_exists(
        "discord_message_from_id_fkey", "discord_message", type_="foreignkey"
    )
    drop_constraint_if_exists(
        "discord_message_recipient_id_fkey", "discord_message", type_="foreignkey"
    )

    # Drop the old columns
    drop_column_if_exists("discord_message", "from_id")
    drop_column_if_exists("discord_message", "recipient_id")

    # Now make author_id NOT NULL (after data migration)
    # Only do this if there's data and author_id is still nullable
    if column_exists("discord_message", "author_id"):
        op.alter_column("discord_message", "author_id", nullable=False)

    # Create new foreign keys and indexes (if they don't exist)
    if not constraint_exists("discord_message", "discord_message_author_id_fkey"):
        op.create_foreign_key(
            "discord_message_author_id_fkey",
            "discord_message",
            "discord_users",
            ["author_id"],
            ["id"],
        )
    if not constraint_exists("discord_message", "discord_message_bot_id_fkey"):
        op.create_foreign_key(
            "discord_message_bot_id_fkey",
            "discord_message",
            "discord_bots",
            ["bot_id"],
            ["id"],
        )
    if not index_exists("discord_message_author_idx"):
        op.create_index(
            "discord_message_author_idx", "discord_message", ["author_id"]
        )
    if not index_exists("discord_message_bot_idx"):
        op.create_index("discord_message_bot_idx", "discord_message", ["bot_id"])

    # Update channel index to include sent_at for efficient time-based queries
    drop_index_if_exists("discord_message_server_channel_idx", table_name="discord_message")
    if not index_exists("discord_message_channel_idx"):
        op.create_index(
            "discord_message_channel_idx", "discord_message", ["channel_id", "sent_at"]
        )


def downgrade() -> None:
    # Reverse discord_message changes
    drop_index_if_exists("discord_message_channel_idx", table_name="discord_message")
    drop_index_if_exists("discord_message_bot_idx", table_name="discord_message")
    drop_index_if_exists("discord_message_author_idx", table_name="discord_message")
    drop_constraint_if_exists(
        "discord_message_bot_id_fkey", "discord_message", type_="foreignkey"
    )
    drop_constraint_if_exists(
        "discord_message_author_id_fkey", "discord_message", type_="foreignkey"
    )

    # Add back old columns
    if not column_exists("discord_message", "from_id"):
        op.add_column(
            "discord_message",
            sa.Column("from_id", sa.BigInteger(), nullable=True),
        )
    if not column_exists("discord_message", "recipient_id"):
        op.add_column(
            "discord_message",
            sa.Column("recipient_id", sa.BigInteger(), nullable=True),
        )

    # Copy author_id back to from_id
    if column_exists("discord_message", "author_id"):
        op.execute("UPDATE discord_message SET from_id = author_id")
        op.execute("UPDATE discord_message SET recipient_id = author_id")  # Default

    # Make from_id and recipient_id NOT NULL
    if column_exists("discord_message", "from_id"):
        op.alter_column("discord_message", "from_id", nullable=False)
    if column_exists("discord_message", "recipient_id"):
        op.alter_column("discord_message", "recipient_id", nullable=False)

    # Recreate old constraints and indexes
    if not constraint_exists("discord_message", "discord_message_from_id_fkey"):
        op.create_foreign_key(
            "discord_message_from_id_fkey",
            "discord_message",
            "discord_users",
            ["from_id"],
            ["id"],
        )
    if not constraint_exists("discord_message", "discord_message_recipient_id_fkey"):
        op.create_foreign_key(
            "discord_message_recipient_id_fkey",
            "discord_message",
            "discord_users",
            ["recipient_id"],
            ["id"],
        )
    if not index_exists("discord_message_from_idx"):
        op.create_index("discord_message_from_idx", "discord_message", ["from_id"])
    if not index_exists("discord_message_recipient_idx"):
        op.create_index(
            "discord_message_recipient_idx", "discord_message", ["recipient_id"]
        )
    if not index_exists("discord_message_server_channel_idx"):
        op.create_index(
            "discord_message_server_channel_idx",
            "discord_message",
            ["server_id", "channel_id"],
        )

    # Drop new columns
    drop_column_if_exists("discord_message", "attachments")
    drop_column_if_exists("discord_message", "embeds")
    drop_column_if_exists("discord_message", "reactions")
    drop_column_if_exists("discord_message", "is_pinned")
    drop_column_if_exists("discord_message", "author_id")
    drop_column_if_exists("discord_message", "bot_id")

    # Restore discord_users columns
    for table in ["discord_users", "discord_channels", "discord_servers"]:
        if not column_exists(table, "last_proactive_at"):
            op.add_column(
                table,
                sa.Column(
                    "last_proactive_at", sa.DateTime(timezone=True), nullable=True
                ),
            )
        if not column_exists(table, "proactive_prompt"):
            op.add_column(
                table,
                sa.Column("proactive_prompt", sa.Text(), nullable=True),
            )
        if not column_exists(table, "proactive_cron"):
            op.add_column(
                table,
                sa.Column("proactive_cron", sa.Text(), nullable=True),
            )
        if not column_exists(table, "chattiness_threshold"):
            op.add_column(
                table,
                sa.Column("chattiness_threshold", sa.Integer(), nullable=True),
            )
        if not column_exists(table, "system_prompt"):
            op.add_column(
                table,
                sa.Column("system_prompt", sa.Text(), nullable=True),
            )
        if not column_exists(table, "summary"):
            op.add_column(
                table,
                sa.Column("summary", sa.Text(), nullable=True),
            )
        if not column_exists(table, "disallowed_tools"):
            op.add_column(
                table,
                sa.Column(
                    "disallowed_tools",
                    sa.ARRAY(sa.Text()),
                    server_default="{}",
                    nullable=False,
                ),
            )
        if not column_exists(table, "allowed_tools"):
            op.add_column(
                table,
                sa.Column(
                    "allowed_tools",
                    sa.ARRAY(sa.Text()),
                    server_default="{}",
                    nullable=False,
                ),
            )
        if not column_exists(table, "ignore_messages"):
            op.add_column(
                table,
                sa.Column("ignore_messages", sa.Boolean(), nullable=True),
            )

    # Restore discord_channels collect_messages -> drop it
    drop_column_if_exists("discord_channels", "collect_messages")

    # Restore discord_servers index
    drop_index_if_exists("discord_servers_collect_idx", table_name="discord_servers")
    if not index_exists("discord_servers_active_idx"):
        op.create_index(
            "discord_servers_active_idx",
            "discord_servers",
            ["ignore_messages", "last_sync_at"],
        )
    drop_column_if_exists("discord_servers", "collect_messages")

    # Drop association table and bots table
    if table_exists("discord_bot_users"):
        op.drop_table("discord_bot_users")
    if table_exists("discord_bots"):
        op.drop_table("discord_bots")
