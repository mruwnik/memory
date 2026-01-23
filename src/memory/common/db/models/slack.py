"""
Database models for the Slack integration.

This module provides models for:
- SlackWorkspace: OAuth credentials + workspace metadata (user's connected workspace)
- SlackChannel: Channels, DMs, group DMs, and MPIMs
- SlackUser: Slack user accounts (may link to Memory users and Persons)
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
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from memory.common.db.models.base import Base
from memory.common.db.models.secrets import decrypt_value, encrypt_value

if TYPE_CHECKING:
    from memory.common.db.models.people import Person
    from memory.common.db.models.users import User


class SlackWorkspace(Base):
    """A Slack workspace connected via OAuth2 user token.

    Each workspace represents a user's OAuth connection to Slack, allowing
    access to their channels, DMs, and messages. Unlike Discord bots,
    each workspace is owned by a single Memory user.

    Design Decision - Primary Key:
        We use Slack's team_id as the primary key. This means:
        - A workspace can only be connected by ONE user in the system
        - If user A connects workspace X, user B cannot also connect workspace X
        - If a user disconnects and reconnects, the existing row is updated (not duplicated)

        This is intentional for this use case where each Memory user connects their
        own personal Slack account. If multi-user-per-workspace is needed in the future,
        use a composite key (team_id, user_id) or an auto-increment id with a unique
        constraint on (team_id, user_id).
    """

    __tablename__ = "slack_workspaces"

    # Use Slack team_id as primary key (see class docstring for design rationale)
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # Slack team_id
    name: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Encrypted OAuth tokens
    access_token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    refresh_token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # OAuth scopes granted (stored as JSON array)
    scopes: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)

    # Collection settings
    collect_messages: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sync_interval_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)

    # Sync status
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Owner - the Memory user who connected this workspace
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user: Mapped[User] = relationship("User", back_populates="slack_workspaces")
    channels: Mapped[list[SlackChannel]] = relationship(
        "SlackChannel", back_populates="workspace", cascade="all, delete-orphan"
    )
    users: Mapped[list[SlackUser]] = relationship(
        "SlackUser", back_populates="workspace", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("slack_workspaces_user_idx", "user_id"),
        Index("slack_workspaces_collect_idx", "collect_messages"),
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

    # Channel type: "channel", "dm", "group_dm", "mpim"
    channel_type: Mapped[str] = mapped_column(Text, nullable=False)
    is_private: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Collection setting: null = inherit from workspace, True/False = explicit override
    collect_messages: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)

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
    )

    @property
    def should_collect(self) -> bool:
        """Determine if messages should be collected for this channel.

        Returns the explicit setting if set, otherwise inherits from workspace.
        """
        if self.collect_messages is not None:
            return self.collect_messages
        return self.workspace.collect_messages


class SlackUser(Base):
    """Slack user account metadata.

    Used for resolving mentions (<@U123> -> @display_name) and
    optionally linking to Memory users and Person contacts.
    """

    __tablename__ = "slack_users"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # Slack user_id
    workspace_id: Mapped[str] = mapped_column(
        Text, ForeignKey("slack_workspaces.id", ondelete="CASCADE"), nullable=False
    )

    username: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    real_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Optional links
    system_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    person_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("people.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    workspace: Mapped[SlackWorkspace] = relationship("SlackWorkspace", back_populates="users")
    system_user: Mapped[User | None] = relationship("User", foreign_keys=[system_user_id])
    person: Mapped[Person | None] = relationship("Person", back_populates="slack_accounts")

    __table_args__ = (
        Index("slack_users_workspace_idx", "workspace_id"),
        Index("slack_users_system_user_idx", "system_user_id"),
        Index("slack_users_person_idx", "person_id"),
    )

    @property
    def name(self) -> str:
        """Return the best available name for display."""
        return self.display_name or self.real_name or self.username


class SlackOAuthState(Base):
    """Temporary storage for OAuth state tokens to prevent CSRF attacks.

    States are created when initiating OAuth flow and consumed (deleted)
    upon callback. They expire after 10 minutes if not used.
    """

    __tablename__ = "slack_oauth_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    state: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
