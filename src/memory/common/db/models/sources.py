"""
Database models for the knowledge base system.
"""

from typing import cast

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from memory.common.db.models.base import Base


class Book(Base):
    """Book-level metadata table"""

    __tablename__ = "book"

    id = Column(BigInteger, primary_key=True)
    isbn = Column(Text, unique=True)
    title = Column(Text, nullable=False)
    author = Column(Text)
    publisher = Column(Text)
    published = Column(DateTime(timezone=True))
    language = Column(Text)
    edition = Column(Text)
    series = Column(Text)
    series_number = Column(Integer)
    total_pages = Column(Integer)
    file_path = Column(Text)
    tags = Column(ARRAY(Text), nullable=False, server_default="{}")

    # Metadata from ebook parser
    book_metadata = Column(JSONB, name="metadata")

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("book_isbn_idx", "isbn"),
        Index("book_author_idx", "author"),
        Index("book_title_idx", "title"),
    )

    def as_payload(self, sections: bool = False) -> dict:
        data = {
            "id": self.id,
            "isbn": self.isbn,
            "title": self.title,
            "author": self.author,
            "publisher": self.publisher,
            "published": self.published,
            "language": self.language,
            "edition": self.edition,
            "series": self.series,
            "series_number": self.series_number,
        } | (cast(dict, self.book_metadata) or {})
        if sections:
            data["sections"] = [section.as_payload() for section in self.sections]
        return data


class ArticleFeed(Base):
    __tablename__ = "article_feeds"

    id = Column(BigInteger, primary_key=True)
    url = Column(Text, nullable=False, unique=True)
    title = Column(Text)
    description = Column(Text)
    tags = Column(ARRAY(Text), nullable=False, server_default="{}")
    check_interval = Column(
        Integer, nullable=False, server_default="60", doc="Minutes between checks"
    )
    last_checked_at = Column(DateTime(timezone=True))
    active = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Add indexes
    __table_args__ = (
        Index("article_feeds_active_idx", "active", "last_checked_at"),
        Index("article_feeds_tags_idx", "tags", postgresql_using="gin"),
    )


class EmailAccount(Base):
    __tablename__ = "email_accounts"

    id = Column(BigInteger, primary_key=True)
    name = Column(Text, nullable=False)
    email_address = Column(Text, nullable=False, unique=True)
    imap_server = Column(Text, nullable=False)
    imap_port = Column(Integer, nullable=False, server_default="993")
    username = Column(Text, nullable=False)
    password = Column(Text, nullable=False)
    use_ssl = Column(Boolean, nullable=False, server_default="true")
    folders = Column(ARRAY(Text), nullable=False, server_default="{}")
    tags = Column(ARRAY(Text), nullable=False, server_default="{}")
    last_sync_at = Column(DateTime(timezone=True))
    active = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Add indexes
    __table_args__ = (
        Index("email_accounts_address_idx", "email_address", unique=True),
        Index("email_accounts_active_idx", "active", "last_sync_at"),
        Index("email_accounts_tags_idx", "tags", postgresql_using="gin"),
    )


class DiscordServer(Base):
    """Discord server configuration and metadata"""

    __tablename__ = "discord_servers"

    id = Column(BigInteger, primary_key=True)  # Discord guild snowflake ID
    name = Column(Text, nullable=False)
    description = Column(Text)
    member_count = Column(Integer)

    # Collection settings
    track_messages = Column(Boolean, nullable=False, server_default="true")
    last_sync_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    channels = relationship(
        "DiscordChannel", back_populates="server", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("discord_servers_active_idx", "track_messages", "last_sync_at"),
    )


class DiscordChannel(Base):
    """Discord channel metadata and configuration"""

    __tablename__ = "discord_channels"

    id = Column(BigInteger, primary_key=True)  # Discord channel snowflake ID
    server_id = Column(BigInteger, ForeignKey("discord_servers.id"), nullable=True)
    name = Column(Text, nullable=False)
    channel_type = Column(Text, nullable=False)  # "text", "voice", "dm", "group_dm"

    # Collection settings (null = inherit from server)
    track_messages = Column(Boolean, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    server = relationship("DiscordServer", back_populates="channels")
    __table_args__ = (Index("discord_channels_server_idx", "server_id"),)


class DiscordUser(Base):
    """Discord user metadata and preferences"""

    __tablename__ = "discord_users"

    id = Column(BigInteger, primary_key=True)  # Discord user snowflake ID
    username = Column(Text, nullable=False)
    display_name = Column(Text)

    # Link to system user if registered
    system_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Basic DM settings
    allow_dm_tracking = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    system_user = relationship("User", back_populates="discord_users")

    __table_args__ = (Index("discord_users_system_user_idx", "system_user_id"),)
