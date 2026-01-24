"""
Database models for the Slack integration.

This module provides models for:
- SlackWorkspace: Workspace metadata (shared across users)
- SlackChannel: Channels, DMs, group DMs, and MPIMs
- SlackUserCredentials: Per-user OAuth credentials for workspaces

Note: Slack user data is stored in Person.contact_info instead of a separate table.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from memory.common.db.models.base import Base
from memory.common.db.models.secrets import decrypt_value, encrypt_value

if TYPE_CHECKING:
    from memory.common.db.models.users import User


class SlackWorkspace(Base):
    """A Slack workspace (team) metadata.

    This represents a Slack workspace that one or more Memory users have connected to.
    The workspace itself is shared - any user with credentials can read messages from
    channels they have access to. Message collection is idempotent and user-agnostic.

    OAuth credentials are stored separately in SlackUserCredentials (per-user).
    """

    __tablename__ = "slack_workspaces"

    # Use Slack team_id as primary key for deduplication
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # Slack team_id
    name: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Collection settings (shared across all users of this workspace)
    collect_messages: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Default 5 minutes - more conservative to avoid Slack rate limits (tier 3/4)
    sync_interval_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)

    # Sync status
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    channels: Mapped[list[SlackChannel]] = relationship(
        "SlackChannel", back_populates="workspace", cascade="all, delete-orphan"
    )
    user_credentials: Mapped[list[SlackUserCredentials]] = relationship(
        "SlackUserCredentials", back_populates="workspace", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("slack_workspaces_collect_idx", "collect_messages"),)


class SlackUserCredentials(Base):
    """Per-user OAuth credentials for a Slack workspace.

    Each user who connects their Slack account to a workspace gets their own
    credentials. This allows:
    - Multiple users to connect to the same workspace
    - Each user to send messages using their own identity
    - Message collection to use any valid credential

    Tokens are encrypted at rest using the same mechanism as the Secret table.
    """

    __tablename__ = "slack_user_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(
        Text, ForeignKey("slack_workspaces.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # Encrypted OAuth tokens
    access_token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    refresh_token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # OAuth scopes granted
    scopes: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)

    # Slack user info for this credential
    slack_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    workspace: Mapped[SlackWorkspace] = relationship("SlackWorkspace", back_populates="user_credentials")
    user: Mapped[User] = relationship("User", back_populates="slack_credentials")

    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="unique_slack_credential_per_user"),
        Index("slack_credentials_workspace_idx", "workspace_id"),
        Index("slack_credentials_user_idx", "user_id"),
    )

    @property
    def access_token(self) -> str | None:
        """Decrypt and return the access token."""
        if self.access_token_encrypted is None:
            return None
        return decrypt_value(self.access_token_encrypted)

    @access_token.setter
    def access_token(self, value: str | None) -> None:
        """Encrypt and store the access token."""
        if value is None:
            self.access_token_encrypted = None
        else:
            self.access_token_encrypted = encrypt_value(value)

    @property
    def refresh_token(self) -> str | None:
        """Decrypt and return the refresh token."""
        if self.refresh_token_encrypted is None:
            return None
        return decrypt_value(self.refresh_token_encrypted)

    @refresh_token.setter
    def refresh_token(self, value: str | None) -> None:
        """Encrypt and store the refresh token."""
        if value is None:
            self.refresh_token_encrypted = None
        else:
            self.refresh_token_encrypted = encrypt_value(value)

    def is_token_expired(self) -> bool:
        """Check if the access token has expired."""
        if self.token_expires_at is None:
            return False  # No expiration = valid
        now = datetime.now(timezone.utc)
        expires = self.token_expires_at
        # Handle naive datetime (assume UTC)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return now >= expires


class SlackChannel(Base):
    """Slack channel, DM, group DM, or MPIM metadata and collection settings."""

    __tablename__ = "slack_channels"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # Slack channel_id
    workspace_id: Mapped[str] = mapped_column(
        Text, ForeignKey("slack_workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)

    # Channel type: "channel", "dm", "private_channel", "mpim"
    channel_type: Mapped[str] = mapped_column(Text, nullable=False)
    is_private: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Collection setting: null = inherit from workspace, True/False = explicit override
    collect_messages: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)

    # Access control: link to project (milestone) and sensitivity level
    project_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("github_milestones.id", ondelete="SET NULL"), nullable=True
    )
    sensitivity: Mapped[str] = mapped_column(String(20), nullable=False, server_default="basic")

    # Sync cursor for incremental message fetching
    last_message_ts: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    workspace: Mapped[SlackWorkspace] = relationship("SlackWorkspace", back_populates="channels")

    __table_args__ = (
        Index("slack_channels_workspace_idx", "workspace_id"),
        Index("slack_channels_type_idx", "channel_type"),
        Index("slack_channels_project_idx", "project_id"),
    )

    @property
    def should_collect(self) -> bool:
        """Determine if messages should be collected for this channel.

        Returns the explicit setting if set, otherwise inherits from workspace.
        """
        if self.collect_messages is not None:
            return self.collect_messages
        return self.workspace.collect_messages