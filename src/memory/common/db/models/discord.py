"""
Database models for the Discord system.
"""

import textwrap

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
)
from sqlalchemy.orm import relationship

from memory.common.db.models.base import Base


class MessageProcessor:
    track_messages = Column(Boolean, nullable=False, server_default="true")
    ignore_messages = Column(Boolean, nullable=True, default=False)

    allowed_tools = Column(ARRAY(Text), nullable=False, server_default="{}")
    disallowed_tools = Column(ARRAY(Text), nullable=False, server_default="{}")

    system_prompt = Column(
        Text,
        nullable=True,
        doc="System prompt for this processor. The precedence is user -> channel -> server -> default.",
    )
    chattiness_threshold = Column(
        Integer,
        nullable=False,
        default=50,
        doc="The threshold for the bot to continue the conversation, between 0 and 100.",
    )

    summary = Column(
        Text,
        nullable=True,
        doc=textwrap.dedent(
            """
            A summary of this processor, made by and for AI systems.
        
            The idea here is that AI systems can use this summary to keep notes on the given processor.
            These should automatically be injected into the context of the messages that are processed by this processor.   
            """
        ),
    )

    def as_xml(self) -> str:
        return (
            textwrap.dedent("""
            <{type}>
                <name>{name}</name>
                <summary>{summary}</summary>
            </{type}>
        """)
            .format(
                type=self.__class__.__tablename__[8:],  # type: ignore
                name=getattr(self, "name", None) or getattr(self, "username", None),
                summary=self.summary,
            )
            .strip()
        )


class DiscordServer(Base, MessageProcessor):
    """Discord server configuration and metadata"""

    __tablename__ = "discord_servers"

    id = Column(BigInteger, primary_key=True)  # Discord guild snowflake ID
    name = Column(Text, nullable=False)
    description = Column(Text)
    member_count = Column(Integer)

    # Collection settings
    last_sync_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    channels = relationship(
        "DiscordChannel", back_populates="server", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("discord_servers_active_idx", "track_messages", "last_sync_at"),
    )


class DiscordChannel(Base, MessageProcessor):
    """Discord channel metadata and configuration"""

    __tablename__ = "discord_channels"

    id = Column(BigInteger, primary_key=True)  # Discord channel snowflake ID
    server_id = Column(BigInteger, ForeignKey("discord_servers.id"), nullable=True)
    name = Column(Text, nullable=False)
    channel_type = Column(Text, nullable=False)  # "text", "voice", "dm", "group_dm"

    # Collection settings (null = inherit from server)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    server = relationship("DiscordServer", back_populates="channels")
    __table_args__ = (Index("discord_channels_server_idx", "server_id"),)


class DiscordUser(Base, MessageProcessor):
    """Discord user metadata and preferences"""

    __tablename__ = "discord_users"

    id = Column(BigInteger, primary_key=True)  # Discord user snowflake ID
    username = Column(Text, nullable=False)
    display_name = Column(Text)

    # Link to system user if registered
    system_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Basic DM settings
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    system_user = relationship("User", back_populates="discord_users")

    __table_args__ = (Index("discord_users_system_user_idx", "system_user_id"),)
