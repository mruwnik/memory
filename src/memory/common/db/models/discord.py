"""
Database models for the Discord integration.

This module provides models for:
- DiscordBot: Discord bots owned by their registrants
- DiscordServer: Discord servers (guilds)
- DiscordChannel: Discord channels (text, voice, DM, thread)
- DiscordUser: Discord user accounts (may link to a Person)
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from memory.common.db.models.base import Base
from memory.common.db.models.provider_keys import LLMKeyChoice
from memory.common.encryption import decrypt_value, encrypt_value

if TYPE_CHECKING:
    from memory.common.db.models.sources import Person
    from memory.common.db.models.users import User


DEFAULT_DISCORD_API_BASE_URL = "https://discord.com/api/v10"


class DiscordBot(Base):
    """A Discord bot (application) we receive messages for.

    One row per (Discord application, registering user): several users can
    each connect the same application with their own token and key. Every
    bot reads and sends over the REST API with its token; ``realtime`` picks
    how live events arrive: the gateway collector, or signed Webhook Events
    checked with ``public_key``. The token is stored encrypted; the API URL
    defaults to Discord's and also names the gateway (``GET /gateway/bot``).
    """

    __tablename__ = "discord_bots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Discord's application id, which is also the bot user's snowflake
    application_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)

    # Encrypted Discord bot token; NULL only on rows from before tokens were required, which stay paused
    token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Hex Ed25519 key that signs Webhook Events
    public_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    realtime: Mapped[str] = mapped_column(Text, nullable=False, default="gateway", server_default="gateway")
    api_base_url: Mapped[str] = mapped_column(
        Text, nullable=False, default=DEFAULT_DISCORD_API_BASE_URL,
        server_default=DEFAULT_DISCORD_API_BASE_URL,
    )
    last_event_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # When the bot last listed its servers: the gateway at READY or a refresh, or the REST discovery
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    owner: Mapped[User | None] = relationship("User", back_populates="discord_bots")

    __table_args__ = (
        UniqueConstraint(
            "application_id", "created_by_user_id", name="uq_discord_bots_application_user"
        ),
        CheckConstraint("realtime IN ('gateway', 'webhook')", name="ck_discord_bots_realtime"),
        CheckConstraint(
            "realtime <> 'webhook' OR public_key IS NOT NULL", name="ck_discord_bots_webhook_key"
        ),
    )

    @property
    def runs_gateway(self) -> bool:
        return self.realtime == "gateway"

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
        return self.created_by_user_id is not None and self.created_by_user_id == user.id


class DiscordServer(LLMKeyChoice, Base):
    """Discord server (guild) metadata and collection settings."""

    __tablename__ = "discord_servers"
    # source_type key for the access-control reconciliation pipeline.
    data_source_type: ClassVar[str] = "discord_server"

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
        BigInteger, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    sensitivity: Mapped[str] = mapped_column(String(20), nullable=False, server_default="basic")
    config_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    # Owning bot: the bot that first registered this server.
    # Used to scope visibility so users only see/edit servers their bots are in.
    bot_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("discord_bots.id", ondelete="SET NULL"), nullable=True
    )
    # Whether bot_id was set by a token-authenticated source (the gateway or a
    # REST listing). A server first seen in a pushed event gives its bot's
    # owner nothing until such a bot claims it.
    verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    # Relationships
    channels: Mapped[list[DiscordChannel]] = relationship(
        "DiscordChannel", back_populates="server", cascade="all, delete-orphan"
    )
    bot: Mapped[DiscordBot | None] = relationship("DiscordBot")

    __table_args__ = (
        CheckConstraint("sensitivity IN ('public', 'basic', 'internal', 'confidential')", name="valid_discord_server_sensitivity"),
        Index("discord_servers_collect_idx", "collect_messages"),
        Index("discord_servers_project_idx", "project_id"),
        Index("discord_servers_bot_idx", "bot_id"),
    )

    @property
    def llm_registrant_id(self) -> int | None:
        return self.bot.created_by_user_id if self.bot else None


class DiscordBotServer(Base):
    """A server a bot's own token says it is in (gateway or REST listing)."""

    __tablename__ = "discord_bot_servers"

    bot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("discord_bots.id", ondelete="CASCADE"), primary_key=True
    )
    server_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("discord_servers.id", ondelete="CASCADE"), primary_key=True
    )


class DiscordChannel(Base):
    """Discord channel metadata and collection settings."""

    __tablename__ = "discord_channels"
    # source_type key for the access-control reconciliation pipeline.
    data_source_type: ClassVar[str] = "discord_channel"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Discord channel snowflake ID
    server_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("discord_servers.id", ondelete="SET NULL"), nullable=True
    )
    category_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )  # Discord category snowflake ID (not a FK - fetched from Discord on demand)
    # A thread's parent channel (snowflake; not a FK, as the parent may be unstored).
    # Null until a thread listing or gateway Thread object names it.
    parent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    channel_type: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # "text", "voice", "dm", "group_dm", "thread"

    # Collection setting: null = inherit from server, True/False = explicit override
    collect_messages: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)

    # The bot that first delivered this channel. A DM has no server, so its
    # owner is this bot's owner.
    bot_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("discord_bots.id", ondelete="SET NULL"), nullable=True
    )

    # Access control: link to project and sensitivity level
    project_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
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
    bot: Mapped[DiscordBot | None] = relationship("DiscordBot")
    parent: Mapped[DiscordChannel | None] = relationship(
        "DiscordChannel",
        primaryjoin="foreign(DiscordChannel.parent_id) == remote(DiscordChannel.id)",
        viewonly=True,
    )

    __table_args__ = (
        Index("discord_channels_server_idx", "server_id"),
        Index("discord_channels_project_idx", "project_id"),
        Index("discord_channels_category_idx", "category_id"),
    )

    @property
    def should_collect(self) -> bool:
        """Determine if messages should be collected for this channel.

        Returns the explicit setting if set; otherwise a thread inherits from its
        parent channel and a channel from its server. For DMs/group DMs (no
        server), defaults to False unless explicitly enabled.
        """
        if self.collect_messages is not None:
            return self.collect_messages
        if self.parent:
            return self.parent.should_collect
        if self.server:
            return self.server.collect_messages
        return False

    @property
    def llm_key_holder(self) -> DiscordServer | None:
        return self.server


class DiscordUser(Base):
    """Discord user account metadata.

    May be linked to a Person, and through it to that Person's user.
    A Person can have multiple Discord accounts.
    """

    __tablename__ = "discord_users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Discord user snowflake ID
    username: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)

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

    # Relationship to Person contact
    person: Mapped[Person | None] = relationship("Person", back_populates="discord_accounts")

    __table_args__ = (
        Index("discord_users_person_idx", "person_id"),
    )

    @property
    def name(self) -> str:
        """Return the display name if set, otherwise username."""
        return self.display_name or self.username
