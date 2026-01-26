"""
Database models for the Discord integration.

This module provides models for:
- DiscordBot: Discord bots we control (with many-to-many user authorization)
- DiscordServer: Discord servers (guilds)
- DiscordChannel: Discord channels (text, voice, DM, thread)
- DiscordUser: Discord user accounts (may link to Memory users)
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from memory.common.db.models.base import Base
from memory.common.db.models.secrets import decrypt_value, encrypt_value

if TYPE_CHECKING:
    from memory.common.db.models.people import Person
    from memory.common.db.models.users import User


# Association table for User <-> DiscordBot many-to-many
discord_bot_users = Table(
    "discord_bot_users",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("bot_id", BigInteger, ForeignKey("discord_bots.id", ondelete="CASCADE"), primary_key=True),
)


class DiscordBot(Base):
    """A Discord bot we control.

    Multiple Memory users can be authorized to use the same bot.
    The bot token is stored encrypted.
    """

    __tablename__ = "discord_bots"

    # Use Discord's bot user ID as primary key
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)

    # Encrypted Discord bot token
    token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Many-to-many relationship with users who can use this bot
    authorized_users: Mapped[list[User]] = relationship(
        "User",
        secondary=discord_bot_users,
        back_populates="discord_bots",
    )

    @property
    def token(self) -> str | None:
        """Decrypt and return the bot token."""
        if self.token_encrypted is None:
            return None
        return decrypt_value(self.token_encrypted)

    @token.setter
    def token(self, value: str | None) -> None:
        """Encrypt and store the bot token."""
        if value is None:
            self.token_encrypted = None
        else:
            self.token_encrypted = encrypt_value(value)

    def is_authorized(self, user: User) -> bool:
        """Check if a user is authorized to use this bot."""
        return user in self.authorized_users


class DiscordServer(Base):
    """Discord server (guild) metadata and collection settings."""

    __tablename__ = "discord_servers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Discord guild snowflake ID
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    member_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Collection setting: whether to collect messages from this server
    collect_messages: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Access control: channels inherit these unless overridden
    project_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("github_milestones.id", ondelete="SET NULL"), nullable=True
    )
    sensitivity: Mapped[str] = mapped_column(String(20), nullable=False, server_default="basic")
    config_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    # Relationships
    channels: Mapped[list[DiscordChannel]] = relationship(
        "DiscordChannel", back_populates="server", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("sensitivity IN ('public', 'basic', 'internal', 'confidential')", name="valid_discord_server_sensitivity"),
        Index("discord_servers_collect_idx", "collect_messages"),
        Index("discord_servers_project_idx", "project_id"),
    )


class DiscordChannel(Base):
    """Discord channel metadata and collection settings."""

    __tablename__ = "discord_channels"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Discord channel snowflake ID
    server_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("discord_servers.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    channel_type: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # "text", "voice", "dm", "group_dm", "thread"

    # Collection setting: null = inherit from server, True/False = explicit override
    collect_messages: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)

    # Access control: link to project and sensitivity level
    project_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("github_milestones.id", ondelete="SET NULL"), nullable=True
    )
    sensitivity: Mapped[str] = mapped_column(String(20), nullable=False, server_default="basic")
    config_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    server: Mapped[DiscordServer | None] = relationship("DiscordServer", back_populates="channels")

    __table_args__ = (
        Index("discord_channels_server_idx", "server_id"),
        Index("discord_channels_project_idx", "project_id"),
    )

    @property
    def should_collect(self) -> bool:
        """Determine if messages should be collected for this channel.

        Returns the explicit setting if set, otherwise inherits from server.
        For DMs/group DMs (no server), defaults to False unless explicitly enabled.
        """
        if self.collect_messages is not None:
            return self.collect_messages
        if self.server:
            return self.server.collect_messages
        return False


class DiscordUser(Base):
    """Discord user account metadata.

    May optionally be linked to a Memory system user and/or a Person contact.
    A Memory user can have multiple Discord accounts.
    """

    __tablename__ = "discord_users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Discord user snowflake ID
    username: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Optional link to Memory system user
    system_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Optional link to a Person contact
    person_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("people.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationship to Memory user
    system_user: Mapped[User | None] = relationship("User", back_populates="discord_accounts")

    # Relationship to Person contact
    person: Mapped[Person | None] = relationship("Person", back_populates="discord_accounts")

    __table_args__ = (
        Index("discord_users_system_user_idx", "system_user_id"),
        Index("discord_users_person_idx", "person_id"),
    )

    @property
    def name(self) -> str:
        """Return the display name if set, otherwise username."""
        return self.display_name or self.username
