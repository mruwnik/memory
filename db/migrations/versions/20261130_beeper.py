"""Beeper accounts, networks, chats, participants and messages.

Revision ID: 20261130_beeper
Revises: 20261129_discord_bot_last_sync
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261130_beeper"
down_revision = "20261129_discord_bot_last_sync"
branch_labels = None
depends_on = None

SENSITIVITY = "sensitivity IN ('public', 'basic', 'internal', 'confidential')"


def timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    ]


def access_control(name: str, inherits: bool = False) -> list:
    sensitivity = (
        sa.Column("sensitivity", sa.String(20))
        if inherits
        else sa.Column("sensitivity", sa.String(20), nullable=False, server_default="basic")
    )
    return [
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("projects.id", ondelete="SET NULL")),
        sensitivity,
        sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(SENSITIVITY, name=f"valid_{name}_sensitivity"),
    ]


def upgrade() -> None:
    op.create_table(
        "beeper_accounts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "created_by_user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("name", sa.Text()),
        sa.Column("server_url", sa.Text(), nullable=False, unique=True),
        sa.Column("email", sa.Text()),
        sa.Column("token_encrypted", sa.LargeBinary()),
        sa.Column("setup_request_id", sa.Text()),
        sa.Column("beeper_user_id", sa.Text()),
        sa.Column("homeserver", sa.Text()),
        sa.Column("device_id", sa.Text()),
        sa.Column("setup_state", sa.Text(), nullable=False, server_default="needs-login"),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("server_version", sa.Text()),
        sa.Column("collect_by_default", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_reconcile_at", sa.DateTime(timezone=True)),
        sa.Column("last_event_at", sa.DateTime(timezone=True)),
        *timestamps(),
    )
    op.create_index("beeper_accounts_owner_idx", "beeper_accounts", ["created_by_user_id"])

    op.create_table(
        "beeper_networks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "account_id", sa.BigInteger(),
            sa.ForeignKey("beeper_accounts.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("network_account_id", sa.Text(), nullable=False),
        sa.Column("network", sa.Text(), nullable=False),
        sa.Column("bridge_type", sa.Text()),
        sa.Column("bridge_provider", sa.Text()),
        sa.Column("display_name", sa.Text()),
        sa.Column("status", sa.Text()),
        sa.Column("collect_messages", sa.Boolean(), nullable=False),
        sa.Column(
            "llm_key_id", sa.Integer(), sa.ForeignKey("llm_provider_keys.id", ondelete="SET NULL")
        ),
        sa.Column("llm_disabled", sa.Boolean(), nullable=False, server_default="false"),
        *access_control("beeper_network"),
        *timestamps(),
        sa.UniqueConstraint("account_id", "network_account_id", name="uq_beeper_networks_account"),
    )
    op.create_index("beeper_networks_project_idx", "beeper_networks", ["project_id"])

    op.create_table(
        "beeper_chats",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "network_id", sa.BigInteger(),
            sa.ForeignKey("beeper_networks.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("chat_id", sa.Text(), nullable=False),
        sa.Column("local_chat_id", sa.Text()),
        sa.Column("title", sa.Text()),
        sa.Column("chat_type", sa.Text()),
        sa.Column("is_merged", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("merged_into", sa.Text()),
        sa.Column("message_expiry_seconds", sa.Integer()),
        sa.Column("last_activity_at", sa.DateTime(timezone=True)),
        sa.Column("collect_messages", sa.Boolean()),
        *access_control("beeper_chat", inherits=True),
        sa.Column("newest_cursor", sa.Text()),
        sa.Column("oldest_cursor", sa.Text()),
        sa.Column("backfill_done", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("backfill_stalls", sa.Integer(), nullable=False, server_default="0"),
        *timestamps(),
        sa.UniqueConstraint("network_id", "chat_id", name="uq_beeper_chats_network_chat"),
    )
    op.create_index("beeper_chats_project_idx", "beeper_chats", ["project_id"])

    op.create_table(
        "beeper_participants",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "network_id", sa.BigInteger(),
            sa.ForeignKey("beeper_networks.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("participant_id", sa.Text(), nullable=False),
        sa.Column("username", sa.Text()),
        sa.Column("full_name", sa.Text()),
        sa.Column("is_self", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("person_id", sa.BigInteger(), sa.ForeignKey("people.id", ondelete="SET NULL")),
        *timestamps(),
        sa.UniqueConstraint("network_id", "participant_id", name="uq_beeper_participants_network"),
    )
    op.create_index("beeper_participants_person_idx", "beeper_participants", ["person_id"])

    op.create_table(
        "beeper_reactions",
        sa.Column(
            "chat_id", sa.BigInteger(),
            sa.ForeignKey("beeper_chats.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("message_id", sa.Text(), primary_key=True),
        sa.Column("parent_message_id", sa.Text(), nullable=False),
    )

    op.create_table(
        "beeper_message",
        sa.Column(
            "id", sa.BigInteger(),
            sa.ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column(
            "chat_id", sa.BigInteger(),
            sa.ForeignKey("beeper_chats.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Text()),
        sa.Column("sort_key", sa.Text()),
        sa.Column("sender_id", sa.Text()),
        sa.Column("sender_name", sa.Text()),
        sa.Column("is_sender", sa.Boolean(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True)),
        sa.Column("reply_to_message_id", sa.Text()),
        sa.Column("message_type", sa.Text(), nullable=False, server_default="TEXT"),
        sa.Column("mentions", postgresql.ARRAY(sa.Text())),
        sa.Column("reactions", postgresql.JSONB()),
        sa.Column("attachments", postgresql.JSONB()),
        sa.Column("images", postgresql.ARRAY(sa.Text())),
    )
    op.create_index(
        "beeper_message_chat_message_idx", "beeper_message", ["chat_id", "message_id"], unique=True
    )
    op.create_index("beeper_message_event_idx", "beeper_message", ["chat_id", "event_id"])
    op.create_index("beeper_message_chat_sent_idx", "beeper_message", ["chat_id", "sent_at"])
    # See 20261128_orphaned_source_items: every source_item subclass table needs it.
    op.execute(
        "CREATE CONSTRAINT TRIGGER delete_orphaned_source_item AFTER DELETE ON beeper_message "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION delete_orphaned_source_item()"
    )


def downgrade() -> None:
    op.execute("DELETE FROM source_item WHERE type = 'beeper_message'")
    op.drop_table("beeper_message")
    op.drop_table("beeper_reactions")
    op.drop_table("beeper_participants")
    op.drop_table("beeper_chats")
    op.drop_table("beeper_networks")
    op.drop_table("beeper_accounts")
