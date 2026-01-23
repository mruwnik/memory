"""Add Slack integration models (workspaces, channels, user credentials, messages).

This migration creates the Slack integration tables with a multi-user design:
- SlackWorkspace: Shared workspace metadata (no tokens - those are per-user)
- SlackUserCredentials: Per-user OAuth credentials for workspaces
- SlackChannel: Channel metadata
- SlackMessage: Ingested messages (SourceItem subclass)

Note: Slack user data is stored in Person.contact_info["slack"] instead of
a separate table, avoiding duplication of the Person concept.

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
    # Create slack_workspaces table (shared workspace metadata, no tokens)
    op.create_table(
        "slack_workspaces",
        sa.Column("id", sa.Text(), nullable=False),  # Slack team_id
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=True),
        sa.Column("collect_messages", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("sync_interval_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
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

    # Create slack_user_credentials table (per-user OAuth credentials)
    op.create_table(
        "slack_user_credentials",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("access_token_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("refresh_token_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("slack_user_id", sa.Text(), nullable=True),  # User's Slack ID
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["workspace_id"], ["slack_workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("workspace_id", "user_id", name="unique_slack_credential_per_user"),
    )
    op.create_index("slack_credentials_workspace_idx", "slack_user_credentials", ["workspace_id"])
    op.create_index("slack_credentials_user_idx", "slack_user_credentials", ["user_id"])

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

    # Create slack_message table (inherits from source_item)
    # Note: author_id is a plain text field (Slack user ID), not an FK
    # Author name is cached in author_name for display purposes
    op.create_table(
        "slack_message",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("message_ts", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("author_id", sa.Text(), nullable=True),  # Slack user ID (not FK)
        sa.Column("author_name", sa.Text(), nullable=True),  # Cached display name
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

    op.drop_index("slack_channels_type_idx", table_name="slack_channels")
    op.drop_index("slack_channels_workspace_idx", table_name="slack_channels")
    op.drop_table("slack_channels")

    op.drop_index("slack_credentials_user_idx", table_name="slack_user_credentials")
    op.drop_index("slack_credentials_workspace_idx", table_name="slack_user_credentials")
    op.drop_table("slack_user_credentials")

    op.drop_index("oauth_client_states_provider_idx", table_name="oauth_client_states")
    op.drop_index("oauth_client_states_state_idx", table_name="oauth_client_states")
    op.drop_table("oauth_client_states")

    op.drop_index("slack_workspaces_collect_idx", table_name="slack_workspaces")
    op.drop_table("slack_workspaces")
