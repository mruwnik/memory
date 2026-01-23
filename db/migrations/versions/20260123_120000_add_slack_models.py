"""Add Slack integration models (workspaces, channels, users, messages).

Revision ID: 20260123_120000
Revises: 20260122_200000
Create Date: 2026-01-23 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260123_120000"
down_revision: Union[str, None] = "20260122_200000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create slack_workspaces table
    op.create_table(
        "slack_workspaces",
        sa.Column("id", sa.Text(), nullable=False),  # Slack team_id
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=True),
        sa.Column("access_token_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("refresh_token_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("collect_messages", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("sync_interval_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_error", sa.Text(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("slack_workspaces_user_idx", "slack_workspaces", ["user_id"])
    op.create_index("slack_workspaces_collect_idx", "slack_workspaces", ["collect_messages"])

    # Create oauth_client_states table for CSRF protection (generic for all OAuth clients)
    op.create_table(
        "oauth_client_states",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),  # "slack", "google", etc.
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("oauth_client_states_state_idx", "oauth_client_states", ["state"], unique=True)
    op.create_index("oauth_client_states_provider_idx", "oauth_client_states", ["provider"])

    # Create slack_channels table
    op.create_table(
        "slack_channels",
        sa.Column("id", sa.Text(), nullable=False),  # Slack channel_id
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("channel_type", sa.Text(), nullable=False),
        sa.Column("is_private", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("collect_messages", sa.Boolean(), nullable=True),
        sa.Column("last_message_ts", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["workspace_id"], ["slack_workspaces.id"], ondelete="CASCADE"),
    )
    op.create_index("slack_channels_workspace_idx", "slack_channels", ["workspace_id"])
    op.create_index("slack_channels_type_idx", "slack_channels", ["channel_type"])

    # Create slack_users table
    op.create_table(
        "slack_users",
        sa.Column("id", sa.Text(), nullable=False),  # Slack user_id
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("real_name", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("is_bot", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("system_user_id", sa.Integer(), nullable=True),
        sa.Column("person_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["workspace_id"], ["slack_workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["system_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="SET NULL"),
    )
    op.create_index("slack_users_workspace_idx", "slack_users", ["workspace_id"])
    op.create_index("slack_users_system_user_idx", "slack_users", ["system_user_id"])
    op.create_index("slack_users_person_idx", "slack_users", ["person_id"])

    # Create slack_message table (inherits from source_item)
    op.create_table(
        "slack_message",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("message_ts", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("author_id", sa.Text(), nullable=True),
        sa.Column("thread_ts", sa.Text(), nullable=True),
        sa.Column("reply_count", sa.Integer(), nullable=True),
        sa.Column("message_type", sa.Text(), server_default="message"),
        sa.Column("edited_ts", sa.Text(), nullable=True),
        sa.Column("reactions", postgresql.JSONB(), nullable=True),
        sa.Column("files", postgresql.JSONB(), nullable=True),
        sa.Column("resolved_content", sa.Text(), nullable=True),
        sa.Column("images", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["id"], ["source_item.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["channel_id"], ["slack_channels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["slack_workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_id"], ["slack_users.id"], ondelete="SET NULL"),
    )
    op.create_index("slack_message_ts_workspace_channel_idx", "slack_message", ["message_ts", "workspace_id", "channel_id"], unique=True)
    op.create_index("slack_message_workspace_idx", "slack_message", ["workspace_id"])
    op.create_index("slack_message_channel_idx", "slack_message", ["channel_id"])
    op.create_index("slack_message_author_idx", "slack_message", ["author_id"])
    op.create_index("slack_message_thread_idx", "slack_message", ["thread_ts"])


def downgrade() -> None:
    # Drop tables in reverse order of dependencies
    op.drop_index("slack_message_thread_idx", table_name="slack_message")
    op.drop_index("slack_message_author_idx", table_name="slack_message")
    op.drop_index("slack_message_channel_idx", table_name="slack_message")
    op.drop_index("slack_message_workspace_idx", table_name="slack_message")
    op.drop_index("slack_message_ts_workspace_channel_idx", table_name="slack_message")
    op.drop_table("slack_message")

    op.drop_index("slack_users_person_idx", table_name="slack_users")
    op.drop_index("slack_users_system_user_idx", table_name="slack_users")
    op.drop_index("slack_users_workspace_idx", table_name="slack_users")
    op.drop_table("slack_users")

    op.drop_index("slack_channels_type_idx", table_name="slack_channels")
    op.drop_index("slack_channels_workspace_idx", table_name="slack_channels")
    op.drop_table("slack_channels")

    op.drop_index("oauth_client_states_provider_idx", table_name="oauth_client_states")
    op.drop_index("oauth_client_states_state_idx", table_name="oauth_client_states")
    op.drop_table("oauth_client_states")

    op.drop_index("slack_workspaces_collect_idx", table_name="slack_workspaces")
    op.drop_index("slack_workspaces_user_idx", table_name="slack_workspaces")
    op.drop_table("slack_workspaces")
