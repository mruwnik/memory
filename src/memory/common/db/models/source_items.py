"""
Database models for the knowledge base system.
"""

from __future__ import annotations

import pathlib
import textwrap
from datetime import datetime
from typing import TYPE_CHECKING, Any, Annotated, NotRequired, Sequence

from PIL import Image
import zlib

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from memory.common import settings
import memory.common.extract as extract
import memory.common.summarizer as summarizer
import memory.common.formatters.observation as observation

from memory.common.db.models.base import Base
from memory.common.db.models.source_item import (
    SourceItem,
    SourceItemPayload,
    clean_filename,
    chunk_mixed,
)
if TYPE_CHECKING:
    from memory.common.db.models.discord import (
        DiscordBot,
        DiscordChannel,
        DiscordServer,
        DiscordUser,
    )
    from memory.common.db.models.sources import Person
    from memory.common.db.models.slack import (
        SlackChannel,
        SlackWorkspace,
    )
    from memory.common.db.models.sources import (
        ArticleFeed,
        Book,
        CalendarAccount,
        EmailAccount,
        GoogleFolder,
        Project,
    )
    from memory.common.db.models.observations import ObservationContradiction


class MailMessagePayload(SourceItemPayload):
    message_id: Annotated[str, "Unique email message identifier"]
    subject: Annotated[str, "Email subject line"]
    sender: Annotated[str, "Email sender address"]
    recipients: Annotated[list[str], "List of recipient email addresses"]
    folder: Annotated[str, "Email folder name"]
    date: Annotated[str | None, "Email sent date in ISO format"]


class MailMessage(SourceItem):
    __tablename__ = "mail_message"

    id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    message_id: Mapped[str | None] = mapped_column(Text, unique=True)
    subject: Mapped[str | None] = mapped_column(Text)
    sender: Mapped[str | None] = mapped_column(Text)
    recipients: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    folder: Mapped[str | None] = mapped_column(Text)
    tsv: Mapped[Any | None] = mapped_column(TSVECTOR)

    # Sync tracking for deletion detection
    email_account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("email_accounts.id", ondelete="CASCADE"), nullable=True
    )
    imap_uid: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __init__(self, **kwargs: Any) -> None:
        if not kwargs.get("modality"):
            kwargs["modality"] = "email"
        super().__init__(**kwargs)

    attachments: Mapped[list[EmailAttachment]] = relationship(
        "EmailAttachment",
        back_populates="mail_message",
        foreign_keys="EmailAttachment.mail_message_id",
        cascade="all, delete-orphan",
    )
    email_account: Mapped[EmailAccount | None] = relationship(
        "EmailAccount", back_populates="messages", foreign_keys=[email_account_id]
    )

    __mapper_args__ = {
        "polymorphic_identity": "mail_message",
    }

    @property
    def attachments_path(self) -> pathlib.Path:
        clean_sender = clean_filename(self.sender or "")
        clean_folder = clean_filename(self.folder or "INBOX")
        return pathlib.Path(settings.EMAIL_STORAGE_DIR) / clean_sender / clean_folder

    def safe_filename(self, filename: str) -> pathlib.Path:
        suffix = pathlib.Path(filename).suffix
        name = clean_filename(filename.removesuffix(suffix)) + suffix
        path = self.attachments_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def as_payload(self) -> MailMessagePayload:
        base = super().as_payload()
        base["tags"] = (self.tags or []) + [self.sender or ""] + (self.recipients or [])
        return MailMessagePayload(
            **base,
            message_id=self.message_id or "",  # type: ignore[arg-type]
            subject=self.subject or "",  # type: ignore[arg-type]
            sender=self.sender or "",  # type: ignore[arg-type]
            recipients=self.recipients or [],  # type: ignore[arg-type]
            folder=self.folder,  # type: ignore[arg-type]
            date=(self.sent_at and self.sent_at.isoformat() or None),
        )

    @property
    def parsed_content(self) -> dict[str, Any]:
        from memory.parsers.email import parse_email_message

        result = parse_email_message(self.content or "", self.message_id or "")
        return dict(result) if result else {}

    @property
    def body(self) -> str:
        return self.parsed_content["body"]

    def format_content(self, content: dict[str, Any]) -> str:
        sender = self.sender or content.get("from") or content.get("sender", "")
        recipients = (
            self.recipients or content.get("to") or content.get("recipients", [])
        )
        date = (self.sent_at and self.sent_at.isoformat()) or content.get("date", "")

        return (
            textwrap.dedent(
                """
            Subject: {subject}
            From: {sender}
            To: {recipients}
            Date: {date}
            Body:
            {body}
            """
            )
            .format(
                subject=self.subject or content.get("subject", ""),
                sender=sender,
                recipients=", ".join(recipients),
                date=date,
                body=content.get("body", ""),
            )
            .strip()
        )

    @property
    def display_contents(self) -> dict | None:
        base = super().display_contents
        return {
            **(base or {}),
            "content": self.body,
            "subject": self.subject,
            "sender": self.sender,
            "recipients": self.recipients,
            "date": self.sent_at and self.sent_at.isoformat(),
        }

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        content = self.parsed_content
        body = self.body

        chunks = extract.extract_text(body, modality="mail")

        def add_header(item: extract.MulitmodalChunk) -> extract.MulitmodalChunk:
            if isinstance(item, str):
                return self.format_content(content | {"body": item}).strip()
            return item

        for chunk in chunks:
            chunk.data = [add_header(item) for item in chunk.data]
        return chunks

    @classmethod
    def get_collections(cls) -> list[str]:
        return ["mail"]

    @property
    def title(self) -> str | None:
        return self.subject

    # Add indexes
    __table_args__ = (
        Index("mail_sent_idx", "sent_at"),
        Index("mail_recipients_idx", "recipients", postgresql_using="gin"),
        Index("mail_tsv_idx", "tsv", postgresql_using="gin"),
        Index("mail_account_idx", "email_account_id"),
        Index("mail_imap_uid_idx", "email_account_id", "folder", "imap_uid"),
    )

    def get_data_source(self) -> Any:
        """Get the email account for access control inheritance."""
        return self.email_account


class EmailAttachmentPayload(SourceItemPayload):
    filename: Annotated[str, "Name of the document file"]
    content_type: Annotated[str, "MIME type of the document"]
    mail_message_id: Annotated[int, "Associated email message ID"]
    sent_at: Annotated[str | None, "Document creation timestamp"]
    created_at: Annotated[str | None, "Document creation timestamp"]


class EmailAttachment(SourceItem):
    __tablename__ = "email_attachment"

    id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    mail_message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("mail_message.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    mail_message: Mapped[MailMessage] = relationship(
        "MailMessage", back_populates="attachments", foreign_keys=[mail_message_id]
    )

    __mapper_args__ = {
        "polymorphic_identity": "email_attachment",
    }

    def as_payload(self) -> EmailAttachmentPayload:
        return EmailAttachmentPayload(
            **super().as_payload(),
            created_at=(self.created_at and self.created_at.isoformat() or None),
            filename=self.filename or "",
            content_type=self.mime_type or "",
            mail_message_id=self.mail_message_id,
            sent_at=(
                self.mail_message.sent_at and self.mail_message.sent_at.isoformat() or None
            ),
        )

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        if self.filename:
            contents = (settings.FILE_STORAGE_DIR / self.filename).read_bytes()
        else:
            contents = self.content or ""

        return extract.extract_data_chunks(self.mime_type or "", contents)

    @property
    def display_contents(self) -> dict:
        base = super().display_contents
        return {
            **(base or {}),
            **(self.mail_message.display_contents or {}),
        }

    # Add indexes
    __table_args__ = (Index("email_attachment_message_idx", "mail_message_id"),)

    @classmethod
    def get_collections(cls) -> list[str]:
        """EmailAttachment can go to different collections based on mime_type"""
        return ["doc", "text", "blog", "photo", "book"]


class ChatMessage(SourceItem):
    __tablename__ = "chat_message"

    id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    platform: Mapped[str | None] = mapped_column(Text)
    channel_id: Mapped[str | None] = mapped_column(Text)  # Keep as Text for cross-platform compatibility
    author: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __mapper_args__ = {
        "polymorphic_identity": "chat_message",
    }

    # Add index
    __table_args__ = (Index("chat_channel_idx", "platform", "channel_id"),)


class DiscordMessage(SourceItem):
    """Discord message collected from a channel or DM.

    This is a simplified model focused on data collection. Messages are
    stored with their metadata and embedded for semantic search.
    """

    __tablename__ = "discord_message"

    id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )

    # Discord IDs
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)  # Discord snowflake
    channel_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("discord_channels.id"), nullable=False
    )
    server_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("discord_servers.id"), nullable=True
    )
    author_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("discord_users.id"), nullable=False
    )
    # Nullable because existing messages from before bot system was added won't have this
    bot_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("discord_bots.id"), nullable=True
    )

    # Timestamps
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Threading/replies
    reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    thread_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Message metadata
    message_type: Mapped[str] = mapped_column(
        Text, server_default="default"
    )  # "default", "reply", "thread_starter", "system"
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Attachments - local paths to downloaded images
    images: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)

    # Rich content stored as JSON
    # reactions: [{"emoji": "👍", "count": 3, "users": [123, 456]}]
    reactions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # embeds: Discord embed objects (link previews, etc.)
    embeds: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)
    # attachments: Non-image attachments metadata
    attachments: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)

    # Relationships
    channel: Mapped[DiscordChannel | None] = relationship("DiscordChannel", foreign_keys=[channel_id])
    server: Mapped[DiscordServer | None] = relationship("DiscordServer", foreign_keys=[server_id])
    author: Mapped[DiscordUser | None] = relationship("DiscordUser", foreign_keys=[author_id])
    bot: Mapped[DiscordBot | None] = relationship("DiscordBot", foreign_keys=[bot_id])

    __mapper_args__ = {
        "polymorphic_identity": "discord_message",
    }

    __table_args__ = (
        Index("discord_message_discord_id_idx", "message_id", unique=True),
        Index("discord_message_channel_idx", "channel_id", "sent_at"),
        Index("discord_message_author_idx", "author_id"),
        Index("discord_message_bot_idx", "bot_id"),
    )

    @property
    def title(self) -> str:
        """Format message for display."""
        author_name = self.author.username if self.author else "unknown"
        return f"{author_name}: {self.content}"

    @property
    def should_embed(self) -> bool:
        """Skip embedding for very short messages (< 20 chars)."""
        return bool(self.content) and len(self.content) >= 20

    @property
    def embedding_text(self) -> str:
        """Text to use for embedding. Returns empty string for short messages."""
        if not self.should_embed:
            return ""
        return self.title

    def as_content(self) -> dict[str, Any]:
        """Return message content ready for LLM (text + images from disk)."""
        content: dict[str, Any] = {"text": self.title, "images": []}
        for path in self.images or []:
            try:
                full_path = settings.FILE_STORAGE_DIR / path
                if full_path.exists():
                    image = Image.open(full_path)
                    content["images"].append(image)
            except Exception:
                pass  # Skip failed image loads
        return content

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        """Generate chunks for embedding."""
        text = self.embedding_text
        if not text:
            return []  # Don't embed short messages
        return extract.extract_text(text, modality="message")

    def get_data_source(self) -> Any:
        """Get the data source for access control inheritance.

        Hierarchical resolution: channel -> server -> None

        Returns the most specific data source with access control settings:
        1. Channel if it has explicit project_id
        2. Server if channel exists and server is linked
        3. None if no channel exists

        Note: If channel exists but has no server link, we return None rather
        than the channel, since a channel without project_id provides no useful
        access control inheritance. The caller (resolve_access_control) will
        then fall back to class defaults.
        """
        if not self.channel:
            return None
        # Prefer channel if it has explicit access control
        if self.channel.project_id is not None:
            return self.channel
        # Fall back to server if available
        if self.channel.server:
            return self.channel.server
        # Channel has no project_id and no server - return None to use class defaults
        return None


class SlackMessage(SourceItem):
    """Slack message collected from a channel or DM.

    Stores messages with their metadata, resolved mentions, and attachments.
    """

    __tablename__ = "slack_message"

    id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )

    # Slack IDs (strings, unlike Discord snowflakes)
    message_ts: Mapped[str] = mapped_column(Text, nullable=False)  # Slack timestamp (message ID)
    channel_id: Mapped[str] = mapped_column(
        Text, ForeignKey("slack_channels.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        Text, ForeignKey("slack_workspaces.id", ondelete="CASCADE"), nullable=False
    )
    # Slack user ID (no FK - user info stored in Person.contact_info)
    author_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Author display name (cached for display, resolved at ingest time)
    author_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Threading
    thread_ts: Mapped[str | None] = mapped_column(Text, nullable=True)  # Parent message if in thread
    reply_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Message metadata
    message_type: Mapped[str] = mapped_column(
        Text, server_default="message"
    )  # "message", "thread_broadcast", "file_share", "channel_join", etc.
    edited_ts: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Rich content
    reactions: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)
    files: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)  # File metadata

    # Resolved content (mentions replaced with display names)
    resolved_content: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Local file paths for downloaded images
    images: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)

    # Relationships (no author relationship - use author_name field)
    channel: Mapped[SlackChannel | None] = relationship("SlackChannel", foreign_keys=[channel_id])
    workspace: Mapped[SlackWorkspace | None] = relationship("SlackWorkspace", foreign_keys=[workspace_id])

    __mapper_args__ = {
        "polymorphic_identity": "slack_message",
    }

    __table_args__ = (
        Index("slack_message_ts_workspace_channel_idx", "message_ts", "workspace_id", "channel_id", unique=True),
        Index("slack_message_workspace_idx", "workspace_id"),
        Index("slack_message_channel_idx", "channel_id"),
        Index("slack_message_author_idx", "author_id"),
        Index("slack_message_thread_idx", "thread_ts"),
    )

    @property
    def title(self) -> str:
        """Format message for display."""
        name = self.author_name or self.author_id or "unknown"
        content = self.resolved_content or self.content or ""
        return f"{name}: {content}"

    @property
    def should_embed(self) -> bool:
        """Skip embedding for very short messages (< 20 chars)."""
        content = self.resolved_content or self.content or ""
        return len(content) >= 20

    @property
    def embedding_text(self) -> str:
        """Text to use for embedding. Returns empty string for short messages."""
        if not self.should_embed:
            return ""
        return self.title

    def as_content(self) -> dict[str, Any]:
        """Return message content ready for LLM (text + images from disk)."""
        content: dict[str, Any] = {"text": self.title, "images": []}
        for path in self.images or []:
            try:
                full_path = settings.FILE_STORAGE_DIR / path
                if full_path.exists():
                    image = Image.open(full_path)
                    content["images"].append(image)
            except Exception:
                pass  # Skip failed image loads
        return content

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        """Generate chunks for embedding."""
        text = self.embedding_text
        if not text:
            return []  # Don't embed short messages
        return extract.extract_text(text, modality="message")

    def get_data_source(self) -> Any:
        """Get the data source for access control inheritance.

        Hierarchical resolution: channel -> workspace -> None

        Returns the most specific data source with access control settings:
        1. Channel if it has explicit project_id
        2. Workspace if channel exists and workspace is linked
        3. None if no channel exists

        Note: If channel exists but has no workspace link, we return None rather
        than the channel, since a channel without project_id provides no useful
        access control inheritance. The caller (resolve_access_control) will
        then fall back to class defaults.
        """
        if not self.channel:
            return None
        # Prefer channel if it has explicit access control
        if self.channel.project_id is not None:
            return self.channel
        # Fall back to workspace if available
        if self.channel.workspace:
            return self.channel.workspace
        # Channel has no project_id and no workspace - return None to use class defaults
        return None


class GitCommit(SourceItem):
    __tablename__ = "git_commit"

    id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    repo_path: Mapped[str | None] = mapped_column(Text)
    commit_sha: Mapped[str | None] = mapped_column(Text, unique=True)
    author_name: Mapped[str | None] = mapped_column(Text)
    author_email: Mapped[str | None] = mapped_column(Text)
    author_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    diff_summary: Mapped[str | None] = mapped_column(Text)
    files_changed: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    __mapper_args__ = {
        "polymorphic_identity": "git_commit",
    }

    # Add indexes
    __table_args__ = (
        Index("git_files_idx", "files_changed", postgresql_using="gin"),
        Index("git_date_idx", "author_date"),
    )


class Photo(SourceItem):
    __tablename__ = "photo"

    id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    exif_taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exif_lat: Mapped[Any | None] = mapped_column(Numeric(9, 6))
    exif_lon: Mapped[Any | None] = mapped_column(Numeric(9, 6))
    camera: Mapped[str | None] = mapped_column(Text)

    __mapper_args__ = {
        "polymorphic_identity": "photo",
    }

    # Add index
    __table_args__ = (Index("photo_taken_idx", "exif_taken_at"),)

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        image = Image.open(settings.FILE_STORAGE_DIR / (self.filename or ""))
        return [extract.DataChunk(data=[image])]

    @classmethod
    def get_collections(cls) -> list[str]:
        return ["photo"]


class ComicPayload(SourceItemPayload):
    title: Annotated[str, "Title of the comic"]
    author: Annotated[str | None, "Author of the comic"]
    published: Annotated[str | None, "Publication date in ISO format"]
    volume: Annotated[str | None, "Volume number"]
    issue: Annotated[str | None, "Issue number"]
    page: Annotated[int | None, "Page number"]
    url: Annotated[str | None, "URL of the comic"]


class Comic(SourceItem):
    __tablename__ = "comic"

    # Public by default - comics collected are typically from public webcomics
    default_sensitivity = "public"

    id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    title: Mapped[str | None] = mapped_column(Text)  # type: ignore[assignment]
    author: Mapped[str | None] = mapped_column(Text, nullable=True)
    published: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    volume: Mapped[str | None] = mapped_column(Text, nullable=True)
    issue: Mapped[str | None] = mapped_column(Text, nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)

    __mapper_args__ = {
        "polymorphic_identity": "comic",
    }

    __table_args__ = (Index("comic_author_idx", "author"),)

    def as_payload(self) -> ComicPayload:
        return ComicPayload(
            **super().as_payload(),
            title=self.title or "",
            author=self.author,
            published=(self.published and self.published.isoformat() or None),
            volume=self.volume,
            issue=self.issue,
            page=self.page,
            url=self.url,
        )

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        image = Image.open(settings.FILE_STORAGE_DIR / (self.filename or ""))
        description = f"{self.title} by {self.author}"
        return [extract.DataChunk(data=[image, description])]


class BookSectionPayload(SourceItemPayload):
    title: Annotated[str, "Title of the book"]
    author: Annotated[str | None, "Author of the book"]
    book_id: Annotated[int, "Unique identifier of the book"]
    section_title: Annotated[str, "Title of the section"]
    section_number: Annotated[int, "Number of the section"]
    section_level: Annotated[int, "Level of the section"]
    start_page: Annotated[int, "Starting page number"]
    end_page: Annotated[int, "Ending page number"]


class BookSection(SourceItem):
    """Individual sections/chapters of books"""

    __tablename__ = "book_section"

    # Public by default - books are typically world-readable
    default_sensitivity = "public"

    id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    book_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("book.id", ondelete="CASCADE"), nullable=False
    )

    section_title: Mapped[str | None] = mapped_column(Text)
    section_number: Mapped[int | None] = mapped_column(Integer)
    section_level: Mapped[int | None] = mapped_column(Integer)  # 1=chapter, 2=section, 3=subsection
    start_page: Mapped[int | None] = mapped_column(Integer)
    end_page: Mapped[int | None] = mapped_column(Integer)

    # Parent-child relationships for nested sections
    parent_section_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("book_section.id"))

    book: Mapped[Book] = relationship("Book", back_populates="sections")
    parent: Mapped[BookSection | None] = relationship(
        "BookSection",
        remote_side=[id],
        backref="children",
        foreign_keys=[parent_section_id],
    )
    pages: list[str] = []

    __mapper_args__ = {"polymorphic_identity": "book_section"}
    __table_args__ = (
        Index("book_section_book_idx", "book_id"),
        Index("book_section_parent_idx", "parent_section_id"),
        Index("book_section_level_idx", "section_level", "section_number"),
    )

    @classmethod
    def get_collections(cls) -> list[str]:
        return ["book"]

    @property
    def title(self) -> str | None:
        return self.section_title

    def as_payload(self) -> BookSectionPayload:
        # Book uses old-style Column, access values explicitly
        book_title = getattr(self.book, "title", "") if self.book else ""
        book_author = getattr(self.book, "author", None) if self.book else None
        return BookSectionPayload(
            **super().as_payload(),
            title=str(book_title) if book_title else "",
            author=str(book_author) if book_author else None,
            book_id=self.book_id,
            section_title=self.section_title or "",
            section_number=self.section_number or 0,
            section_level=self.section_level or 0,
            start_page=self.start_page or 0,
            end_page=self.end_page or 0,
        )

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        content = (self.content or "").strip()
        if not content:
            return []

        if len([p for p in self.pages if p.strip()]) == 1:
            chunks = extract.extract_text(
                content, metadata={"type": "page"}, modality="book"
            )
            if len(chunks) > 1:
                chunks[-1].metadata["type"] = "summary"
            return chunks

        summary, tags = summarizer.summarize(content)
        return [
            extract.DataChunk(
                data=[content], metadata={"type": "section", "tags": tags}, modality="book"
            ),
            extract.DataChunk(
                data=[summary], metadata={"type": "summary", "tags": tags}, modality="book"
            ),
        ]


class BlogPostPayload(SourceItemPayload):
    url: Annotated[str, "URL of the blog post"]
    title: Annotated[str, "Title of the blog post"]
    author: Annotated[str | None, "Author of the blog post"]
    published: Annotated[str | None, "Publication date in ISO format"]
    description: Annotated[str | None, "Description of the blog post"]
    domain: Annotated[str | None, "Domain of the blog post"]
    word_count: Annotated[int | None, "Word count of the blog post"]


class BlogPost(SourceItem):
    __tablename__ = "blog_post"

    # Public by default - blogs are typically world-readable
    default_sensitivity = "public"

    id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    url: Mapped[str | None] = mapped_column(Text, unique=True)
    title: Mapped[str | None] = mapped_column(Text)  # type: ignore[assignment]
    author: Mapped[str | None] = mapped_column(Text, nullable=True)
    published: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Additional metadata from webpage parsing
    description: Mapped[str | None] = mapped_column(Text, nullable=True)  # Meta description or excerpt
    domain: Mapped[str | None] = mapped_column(Text, nullable=True)  # Domain of the source website
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Approximate word count
    images: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)  # List of image URLs

    # Store original metadata from parser
    webpage_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Link to article feed source
    feed_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("article_feeds.id", ondelete="SET NULL"), nullable=True
    )

    # Relationship to article feed
    feed: Mapped[ArticleFeed | None] = relationship("ArticleFeed", foreign_keys=[feed_id])

    __mapper_args__ = {
        "polymorphic_identity": "blog_post",
    }

    __table_args__ = (
        Index("blog_post_author_idx", "author"),
        Index("blog_post_domain_idx", "domain"),
        Index("blog_post_published_idx", "published"),
        Index("blog_post_word_count_idx", "word_count"),
        Index("blog_post_feed_idx", "feed_id"),
    )

    def get_data_source(self) -> Any:
        """Get the article feed for access control inheritance."""
        return self.feed

    def as_payload(self) -> BlogPostPayload:
        published_date = self.published
        metadata = self.webpage_metadata or {}

        return BlogPostPayload(
            **super().as_payload(),
            url=self.url or "",
            title=self.title or "",
            author=self.author,
            published=(published_date and published_date.isoformat() or None),
            description=self.description,
            domain=self.domain,
            word_count=self.word_count,
            **metadata,
        )

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        return chunk_mixed(self.content or "", self.images or [])


class ForumPostPayload(SourceItemPayload):
    url: Annotated[str, "URL of the forum post"]
    title: Annotated[str, "Title of the forum post"]
    description: Annotated[str | None, "Description of the forum post"]
    authors: Annotated[list[str] | None, "Authors of the forum post"]
    published: Annotated[str | None, "Publication date in ISO format"]
    slug: Annotated[str | None, "Slug of the forum post"]
    karma: Annotated[int | None, "Karma score of the forum post"]
    votes: Annotated[int | None, "Number of votes on the forum post"]
    score: Annotated[int | None, "Score of the forum post"]
    comments: Annotated[int | None, "Number of comments on the forum post"]


class ForumPost(SourceItem):
    __tablename__ = "forum_post"

    # Public by default - forums are typically world-readable
    default_sensitivity = "public"

    id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    url: Mapped[str | None] = mapped_column(Text, unique=True)
    title: Mapped[str | None] = mapped_column(Text)  # type: ignore[assignment]
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    authors: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    slug: Mapped[str | None] = mapped_column(Text, nullable=True)
    karma: Mapped[int | None] = mapped_column(Integer, nullable=True)
    votes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    words: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    images: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)

    __mapper_args__ = {
        "polymorphic_identity": "forum_post",
    }

    __table_args__ = (
        Index("forum_post_url_idx", "url"),
        Index("forum_post_slug_idx", "slug"),
        Index("forum_post_title_idx", "title"),
    )

    def as_payload(self) -> ForumPostPayload:
        return ForumPostPayload(
            **super().as_payload(),
            url=self.url or "",
            title=self.title or "",
            description=self.description,
            authors=self.authors,
            published=(self.published_at and self.published_at.isoformat() or None),
            slug=self.slug,
            karma=self.karma,
            votes=self.votes,
            score=self.score,
            comments=self.comments,
        )

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        return chunk_mixed(self.content or "", self.images or [])

    @classmethod
    def get_collections(cls) -> list[str]:
        # Very sad that I didn't keep the names consistent... Qdrant doesn't allow renaming collections
        return ["forum"]

    # Karma reference values for different forum sources.
    # Maps URL substring to karma value representing "very popular" (~90th percentile).
    # Posts at this karma get popularity=2.0; above caps at 2.5.
    # Based on actual LW data: 90th %ile ≈ 100, 95th ≈ 144, 99th ≈ 275
    KARMA_REFERENCES: dict[str, int] = {
        "lesswrong.com": 100,  # 90th percentile from data
        "greaterwrong.com": 100,  # LW mirror
        "alignmentforum.org": 50,  # Smaller community
        "forum.effectivealtruism.org": 75,
    }
    DEFAULT_KARMA_REFERENCE: int = 50

    @property
    def karma_reference(self) -> int:
        """Get the karma reference for this post based on its URL."""
        url = self.url or ""
        for pattern, ref in self.KARMA_REFERENCES.items():
            if pattern in url:
                return ref
        return self.DEFAULT_KARMA_REFERENCE

    @property
    def popularity(self) -> float:
        """
        Return popularity based on karma, normalized to karma_reference.

        - karma <= 0: returns 0.5 to 1.0
        - karma = karma_reference: returns 2.0
        - karma > karma_reference: capped at 2.5
        """
        karma = self.karma or 0
        if karma <= 0:
            # Downvoted or zero karma: scale between 0.5 and 1.0
            return max(0.5, 1.0 - abs(karma) / 100)
        # Positive karma: linear scale up to reference, then cap
        return min(2.5, 1.0 + karma / self.karma_reference)


class MiscDoc(SourceItem):
    __tablename__ = "misc_doc"

    id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    path: Mapped[str | None] = mapped_column(Text)

    __mapper_args__ = {
        "polymorphic_identity": "misc_doc",
    }


class GithubItemPayload(SourceItemPayload):
    kind: Annotated[str, "Type: issue, pr, comment, or project_card"]
    repo_path: Annotated[str, "Repository path (owner/name)"]
    number: Annotated[int | None, "Issue or PR number"]
    state: Annotated[str | None, "State: open, closed, merged"]
    title: Annotated[str | None, "Issue or PR title"]
    author: Annotated[str | None, "Author username"]
    labels: Annotated[list[str] | None, "GitHub labels"]
    assignees: Annotated[list[str] | None, "Assigned users"]
    milestone: Annotated[str | None, "Milestone name"]
    project_status: Annotated[str | None, "GitHub Project status"]
    project_priority: Annotated[str | None, "GitHub Project priority"]
    created_at: Annotated[datetime | None, "Creation date"]
    closed_at: Annotated[datetime | None, "Close date"]
    merged_at: Annotated[datetime | None, "Merge date (PRs only)"]


class GithubItem(SourceItem):
    __tablename__ = "github_item"

    id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    repo_path: Mapped[str] = mapped_column(Text, nullable=False)
    number: Mapped[int | None] = mapped_column(Integer)
    parent_number: Mapped[int | None] = mapped_column(Integer)
    commit_sha: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)  # type: ignore[assignment]
    labels: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    author: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    diff_summary: Mapped[str | None] = mapped_column(Text)

    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # New fields for change detection and tracking
    github_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # GitHub's updated_at
    content_hash: Mapped[str | None] = mapped_column(Text)  # Hash of body + comments for change detection
    repo_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("github_repos.id", ondelete="SET NULL"), nullable=True
    )

    # GitHub Projects v2 fields
    project_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_priority: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_fields: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)  # All project field values

    # Additional tracking
    assignees: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    milestone_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
    )
    comment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Relationship to milestone/project
    # foreign_keys needed because SourceItem.project_id also references projects
    milestone_rel: Mapped[Project | None] = relationship(
        "Project", back_populates="items", foreign_keys=[milestone_id]
    )

    # Relationship to PR-specific data
    pr_data: Mapped[GithubPRData | None] = relationship(
        "GithubPRData",
        back_populates="github_item",
        uselist=False,
        cascade="all, delete-orphan",
    )

    __mapper_args__ = {
        "polymorphic_identity": "github_item",
    }

    __table_args__ = (
        CheckConstraint("kind IN ('issue', 'pr', 'comment', 'project_card')"),
        Index("gh_repo_kind_idx", "repo_path", "kind"),
        Index("gh_issue_lookup_idx", "repo_path", "kind", "number"),
        Index("gh_labels_idx", "labels", postgresql_using="gin"),
        Index("gh_github_updated_at_idx", "github_updated_at"),
        Index("gh_repo_id_idx", "repo_id"),
        Index("gh_milestone_id_idx", "milestone_id"),
    )

    @classmethod
    def get_collections(cls) -> list[str]:
        return ["github"]

    def as_payload(self) -> GithubItemPayload:
        return GithubItemPayload(
            **super().as_payload(),
            kind=self.kind,
            repo_path=self.repo_path,
            number=self.number,
            state=self.state,
            title=self.title,
            author=self.author,
            labels=self.labels,
            assignees=self.assignees,
            milestone=str(getattr(self.milestone_rel, "title", "")) if self.milestone_rel and getattr(self.milestone_rel, "title", None) else None,
            project_status=self.project_status,
            project_priority=self.project_priority,
            created_at=self.created_at,
            closed_at=self.closed_at,
            merged_at=self.merged_at,
        )

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        """Override to use 'github' modality instead of default 'text'."""
        content = self.content
        if content:
            return extract.extract_text(content, modality="github")
        return []


class GithubPRData(Base):
    """PR-specific data linked to GithubItem. Not a SourceItem - not indexed separately."""

    __tablename__ = "github_pr_data"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    github_item_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("github_item.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # Diff (compressed with zlib)
    diff_compressed: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    # File changes as structured data
    # [{filename, status, additions, deletions, patch?}]
    files: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)

    # Stats
    additions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deletions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    changed_files_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Reviews (structured)
    # [{user, state, body, submitted_at}]
    reviews: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)

    # Review comments (line-by-line code comments)
    # [{user, body, path, line, diff_hunk, created_at}]
    review_comments: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)

    # Relationship back to GithubItem
    github_item: Mapped[GithubItem] = relationship("GithubItem", back_populates="pr_data")

    @property
    def diff(self) -> str | None:
        """Decompress and return the full diff text."""
        if self.diff_compressed:
            return zlib.decompress(self.diff_compressed).decode("utf-8")
        return None

    @diff.setter
    def diff(self, value: str | None) -> None:
        """Compress and store the diff text."""
        if value:
            self.diff_compressed = zlib.compress(value.encode("utf-8"))
        else:
            self.diff_compressed = None


class NotePayload(SourceItemPayload):
    note_type: Annotated[str | None, "Category of the note"]
    subject: Annotated[str | None, "What the note is about"]
    confidence: Annotated[dict[str, float], "Confidence scores for the note"]


class Note(SourceItem):
    """A quick note of something of interest."""

    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    note_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)

    __mapper_args__ = {
        "polymorphic_identity": "note",
    }

    __table_args__ = (
        Index("note_type_idx", "note_type"),
        Index("note_subject_idx", "subject"),
    )

    def as_payload(self) -> NotePayload:
        return NotePayload(
            **super().as_payload(),
            note_type=self.note_type,
            subject=self.subject,
            confidence=self.confidence_dict,
        )

    @property
    def display_contents(self) -> dict:
        return {
            "subject": self.subject,
            "content": self.content,
            "note_type": self.note_type,
            "confidence": self.confidence_dict,
            "tags": self.tags,
        }

    def save_to_file(self) -> None:
        if not self.filename:
            self.filename = f"{self.subject}.md"
        path = settings.NOTES_STORAGE_DIR / self.filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.content or "")

    @staticmethod
    def as_text(content: str, subject: str | None = None) -> str:
        text = content
        if subject:
            text = f"# {subject}\n\n{text}"
        return text

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        return extract.extract_text(self.as_text(self.content or "", self.subject))

    @classmethod
    def get_collections(cls) -> list[str]:
        return ["text"]  # Notes go to the text collection

    @property
    def title(self) -> str | None:
        return self.subject


class AgentObservationPayload(SourceItemPayload):
    session_id: Annotated[str | None, "Session ID for the observation"]
    observation_type: Annotated[str, "Type of observation"]
    subject: Annotated[str, "What/who the observation is about"]
    confidence: Annotated[dict[str, float], "Confidence scores for the observation"]
    evidence: Annotated[dict | None, "Supporting context, quotes, etc."]
    agent_model: Annotated[str, "Which AI model made this observation"]


class AgentObservation(SourceItem):
    """
    Records observations made by AI agents about the user.
    This is the primary data model for the epistemic sparring partner.
    """

    __tablename__ = "agent_observation"

    id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    session_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    observation_type: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # belief, preference, pattern, contradiction, behavior
    subject: Mapped[str] = mapped_column(Text, nullable=False)  # What/who the observation is about
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # Supporting context, quotes, etc.
    agent_model: Mapped[str] = mapped_column(Text, nullable=False)  # Which AI model made this observation

    # Relationships
    contradictions_as_first: Mapped[list[ObservationContradiction]] = relationship(
        "ObservationContradiction",
        foreign_keys="ObservationContradiction.observation_1_id",
        back_populates="observation_1",
        cascade="all, delete-orphan",
    )
    contradictions_as_second: Mapped[list[ObservationContradiction]] = relationship(
        "ObservationContradiction",
        foreign_keys="ObservationContradiction.observation_2_id",
        back_populates="observation_2",
        cascade="all, delete-orphan",
    )

    __mapper_args__ = {
        "polymorphic_identity": "agent_observation",
    }

    __table_args__ = (
        Index("agent_obs_session_idx", "session_id"),
        Index("agent_obs_type_idx", "observation_type"),
        Index("agent_obs_subject_idx", "subject"),
        Index("agent_obs_model_idx", "agent_model"),
    )

    def __init__(self, **kwargs: Any) -> None:
        if not kwargs.get("modality"):
            kwargs["modality"] = "observation"
        super().__init__(**kwargs)

    def as_payload(self) -> AgentObservationPayload:
        return AgentObservationPayload(
            **super().as_payload(),
            observation_type=self.observation_type,
            subject=self.subject,
            confidence=self.confidence_dict,
            evidence=self.evidence,
            agent_model=self.agent_model,
            session_id=self.session_id and str(self.session_id),
        )

    @property
    def all_contradictions(self) -> list[ObservationContradiction]:
        """Get all contradictions involving this observation."""
        return self.contradictions_as_first + self.contradictions_as_second

    @property
    def display_contents(self) -> dict:
        return {
            "subject": self.subject,
            "content": self.content,
            "observation_type": self.observation_type,
            "evidence": self.evidence,
            "confidence": self.confidence_dict,
            "agent_model": self.agent_model,
            "tags": self.tags,
        }

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        """
        Generate multiple chunks for different embedding dimensions.
        Each chunk goes to a different Qdrant collection for specialized search.
        """
        # 1. Semantic chunk - standard content representation
        chunks: list[extract.DataChunk] = []
        # Cast to Evidence type (or None) for type checker
        evidence: observation.Evidence | None = self.evidence  # type: ignore[assignment]
        semantic_text = observation.generate_semantic_text(
            self.subject,
            self.observation_type,
            self.content or "",
            evidence,
        )
        if semantic_text:
            chunks += [
                extract.DataChunk(
                    data=[semantic_text],
                    metadata={"embedding_type": "semantic"},
                    modality="semantic",
                )
            ]

        # 2. Temporal chunk - time-aware representation
        temporal_text = observation.generate_temporal_text(
            self.subject,
            self.content or "",
            self.inserted_at or datetime.now(),
        )
        if temporal_text:
            chunks += [
                extract.DataChunk(
                    data=[temporal_text],
                    metadata={"embedding_type": "temporal"},
                    modality="temporal",
                )
            ]

        raw_data = [
            self.content,
            self.evidence and self.evidence.get("quote"),
        ]
        chunks += [
            extract.DataChunk(
                data=[datum],
                metadata={"embedding_type": "semantic"},
                modality="semantic",
            )
            for datum in raw_data
            if datum and all(datum)
        ]

        # TODO: Add more embedding dimensions here:
        # 3. Epistemic chunk - belief structure focused
        # epistemic_text = self._generate_epistemic_text()
        # chunks.append(extract.DataChunk(
        #     data=[epistemic_text],
        #     metadata={**base_metadata, "embedding_type": "epistemic"},
        #     collection_name="observations_epistemic"
        # ))
        #
        # 4. Emotional chunk - emotional context focused
        # emotional_text = self._generate_emotional_text()
        # chunks.append(extract.DataChunk(
        #     data=[emotional_text],
        #     metadata={**base_metadata, "embedding_type": "emotional"},
        #     collection_name="observations_emotional"
        # ))
        #
        # 5. Relational chunk - connection patterns focused
        # relational_text = self._generate_relational_text()
        # chunks.append(extract.DataChunk(
        #     data=[relational_text],
        #     metadata={**base_metadata, "embedding_type": "relational"},
        #     collection_name="observations_relational"
        # ))

        return chunks

    @classmethod
    def get_collections(cls) -> list[str]:
        return ["semantic", "temporal"]

    @property
    def title(self) -> str | None:
        return self.subject


class GoogleDocPayload(SourceItemPayload):
    google_file_id: Annotated[str, "Google Drive file ID"]
    title: Annotated[str, "Document title"]
    original_mime_type: Annotated[str | None, "Original Google/MIME type"]
    folder_path: Annotated[str | None, "Path in Google Drive"]
    owner: Annotated[str | None, "Document owner email"]
    last_modified_by: Annotated[str | None, "Last modifier email"]
    google_modified_at: Annotated[str | None, "Last modified time from Google"]
    word_count: Annotated[int | None, "Approximate word count"]


class GoogleDoc(SourceItem):
    """Google Drive document (Docs, PDFs, Word files, text)."""

    __tablename__ = "google_doc"

    id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )

    # Google-specific identifiers
    google_file_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)  # Drive file ID
    google_modified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # For change detection

    # Document metadata
    title: Mapped[str] = mapped_column(Text, nullable=False)  # type: ignore[assignment]
    original_mime_type: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # e.g., "application/vnd.google-apps.document"
    folder_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("google_folders.id", ondelete="SET NULL"), nullable=True
    )
    folder_path: Mapped[str | None] = mapped_column(Text, nullable=True)  # e.g., "My Drive/Work/Projects"

    # Relationship to folder
    folder: Mapped[GoogleFolder | None] = relationship("GoogleFolder", foreign_keys=[folder_id])

    # Authorship tracking
    owner: Mapped[str | None] = mapped_column(Text, nullable=True)  # Email of owner
    last_modified_by: Mapped[str | None] = mapped_column(Text, nullable=True)  # Email of last modifier

    # Content stats
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Content hash for change detection
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)

    __mapper_args__ = {
        "polymorphic_identity": "google_doc",
    }

    __table_args__ = (
        Index("google_doc_file_id_idx", "google_file_id", unique=True),
        Index("google_doc_folder_idx", "folder_id"),
        Index("google_doc_modified_idx", "google_modified_at"),
        Index("google_doc_title_idx", "title"),
    )

    def as_payload(self) -> GoogleDocPayload:
        return GoogleDocPayload(
            **super().as_payload(),
            google_file_id=self.google_file_id,
            title=self.title,
            original_mime_type=self.original_mime_type,
            folder_path=self.folder_path,
            owner=self.owner,
            last_modified_by=self.last_modified_by,
            google_modified_at=(
                self.google_modified_at and self.google_modified_at.isoformat()
            ),
            word_count=self.word_count,
        )

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        content = self.content
        if not content:
            return []

        return extract.extract_text(content, modality="doc")

    @classmethod
    def get_collections(cls) -> list[str]:
        return ["doc"]

    def get_data_source(self) -> Any:
        """Get the Google folder for access control inheritance."""
        return self.folder


class TaskPayload(SourceItemPayload):
    title: Annotated[str, "Title of the task"]
    due_date: Annotated[str | None, "Due date in ISO format"]
    priority: Annotated[str | None, "Priority level: low, medium, high, urgent"]
    status: Annotated[str, "Status: pending, in_progress, done, cancelled"]
    recurrence: Annotated[str | None, "Recurrence rule (RRULE format)"]
    source_item_id: Annotated[int | None, "Source item that spawned this task"]


class Task(SourceItem):
    """Explicit task/todo item."""

    __tablename__ = "task"

    id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )

    task_title: Mapped[str] = mapped_column(Text, nullable=False)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    priority: Mapped[str | None] = mapped_column(Text, nullable=True)  # low, medium, high, urgent
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    recurrence: Mapped[str | None] = mapped_column(Text, nullable=True)  # RRULE format for habits
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Link to source that spawned this task (email, note, etc.)
    source_item_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("source_item.id", ondelete="SET NULL"), nullable=True
    )
    source_item: Mapped[SourceItem | None] = relationship(
        "SourceItem", foreign_keys=[source_item_id], backref="spawned_tasks"
    )

    __mapper_args__ = {
        "polymorphic_identity": "task",
        "inherit_condition": id == SourceItem.id,
    }

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'done', 'cancelled')",
            name="task_status_check",
        ),
        CheckConstraint(
            "priority IS NULL OR priority IN ('low', 'medium', 'high', 'urgent')",
            name="task_priority_check",
        ),
        Index("task_due_date_idx", "due_date"),
        Index("task_status_idx", "status"),
        Index("task_priority_idx", "priority"),
        Index("task_source_item_idx", "source_item_id"),
    )

    def __init__(self, **kwargs: Any) -> None:
        if not kwargs.get("modality"):
            kwargs["modality"] = "task"
        super().__init__(**kwargs)

    def as_payload(self) -> TaskPayload:
        return TaskPayload(
            **super().as_payload(),
            title=self.task_title,
            due_date=(self.due_date and self.due_date.isoformat() or None),
            priority=self.priority,
            status=self.status,
            recurrence=self.recurrence,
            source_item_id=self.source_item_id,
        )

    @property
    def display_contents(self) -> dict:
        return {
            "title": self.task_title,
            "description": self.content,
            "due_date": self.due_date and self.due_date.isoformat(),
            "priority": self.priority,
            "status": self.status,
            "recurrence": self.recurrence,
            "tags": self.tags,
        }

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        parts = [self.task_title]
        if self.content:
            parts.append(self.content)
        if self.due_date:
            parts.append(f"Due: {self.due_date.isoformat()}")
        text = "\n\n".join(parts)
        return extract.extract_text(text, modality="task")

    @classmethod
    def get_collections(cls) -> list[str]:
        return ["task"]

    @property
    def title(self) -> str | None:
        return self.task_title


class CalendarEventPayload(SourceItemPayload):
    event_title: Annotated[str, "Title of the event"]
    start_time: Annotated[str, "Start time in ISO format"]
    end_time: Annotated[str | None, "End time in ISO format"]
    all_day: Annotated[bool, "Whether this is an all-day event"]
    location: Annotated[str | None, "Event location"]
    recurrence_rule: Annotated[str | None, "Recurrence rule (RRULE format)"]
    calendar_account_id: Annotated[int | None, "Calendar account this event belongs to"]
    calendar_name: Annotated[str | None, "Name of the calendar"]
    external_id: Annotated[str | None, "External calendar ID for sync"]
    event_metadata: Annotated[dict | None, "Additional metadata (attendees, links, etc.)"]


class CalendarEvent(SourceItem):
    """Calendar event from external calendar sources (CalDAV, Google, etc.)."""

    __tablename__ = "calendar_event"

    id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )

    # Core event fields
    event_title: Mapped[str] = mapped_column(Text, nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    all_day: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    recurrence_rule: Mapped[str | None] = mapped_column(Text, nullable=True)  # RRULE format

    # Sync metadata
    calendar_account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("calendar_accounts.id", ondelete="SET NULL"), nullable=True
    )
    calendar_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_id: Mapped[str | None] = mapped_column(Text, nullable=True)  # For dedup/sync

    # Relationship
    calendar_account: Mapped[CalendarAccount | None] = relationship(
        "CalendarAccount", foreign_keys=[calendar_account_id]
    )

    # Flexible metadata (attendees, meeting links, conference info, etc.)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    __mapper_args__ = {
        "polymorphic_identity": "calendar_event",
    }

    __table_args__ = (
        Index("calendar_event_start_idx", "start_time"),
        Index("calendar_event_end_idx", "end_time"),
        Index("calendar_event_account_idx", "calendar_account_id"),
        Index("calendar_event_calendar_idx", "calendar_name"),
        Index(
            "calendar_event_external_idx",
            "calendar_account_id",
            "external_id",
            unique=True,
            postgresql_where="external_id IS NOT NULL",
        ),
    )

    def __init__(self, **kwargs: Any) -> None:
        if not kwargs.get("modality"):
            kwargs["modality"] = "calendar"
        super().__init__(**kwargs)

    def as_payload(self) -> CalendarEventPayload:
        return CalendarEventPayload(
            **super().as_payload(),
            event_title=self.event_title,
            start_time=self.start_time.isoformat(),
            end_time=(self.end_time and self.end_time.isoformat() or None),
            all_day=self.all_day,
            location=self.location,
            recurrence_rule=self.recurrence_rule,
            calendar_account_id=self.calendar_account_id,
            calendar_name=self.calendar_name,
            external_id=self.external_id,
            event_metadata=self.event_metadata,
        )

    @property
    def display_contents(self) -> dict:
        return {
            "title": self.event_title,
            "description": self.content,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time and self.end_time.isoformat(),
            "all_day": self.all_day,
            "location": self.location,
            "calendar": self.calendar_name,
            "attendees": (self.event_metadata or {}).get("attendees"),
            "tags": self.tags,
        }

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        parts = [self.event_title]

        if self.content:
            parts.append(self.content)

        if self.location:
            parts.append(f"Location: {self.location}")

        metadata = self.event_metadata or {}
        if attendees := metadata.get("attendees"):
            if isinstance(attendees, list):
                parts.append(f"Attendees: {', '.join(str(a) for a in attendees)}")

        if meeting_link := metadata.get("meeting_link"):
            parts.append(f"Meeting link: {meeting_link}")

        text = "\n\n".join(parts)
        return extract.extract_text(text, modality="calendar")

    @classmethod
    def get_collections(cls) -> list[str]:
        return ["calendar"]

    @property
    def title(self) -> str | None:
        return self.event_title

    def get_data_source(self) -> Any:
        """Get the calendar account for access control inheritance."""
        return self.calendar_account


class MeetingPayload(SourceItemPayload):
    title: Annotated[str | None, "Title of the meeting"]
    meeting_date: Annotated[str | None, "Date/time when the meeting occurred (ISO format)"]
    duration_minutes: Annotated[int | None, "Duration of the meeting in minutes"]
    source_tool: Annotated[str | None, "Tool that generated the transcript (fireflies, granola, etc.)"]
    summary: Annotated[str | None, "LLM-generated summary of the meeting"]
    notes: Annotated[str | None, "LLM-extracted key points and decisions"]
    extraction_status: Annotated[str, "Status of LLM extraction: pending, processing, complete, failed"]
    # Note: 'people' is inherited from SourceItemPayload - for Meeting, it contains attendee IDs
    task_ids: Annotated[list[int], "IDs of Task records extracted from this meeting"]
    calendar_event_id: Annotated[int | None, "ID of linked CalendarEvent if available"]


class Meeting(SourceItem):
    """A meeting transcript with extracted summary, notes, tasks, and attendee links."""

    __tablename__ = "meeting"

    id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )

    # Core metadata
    title: Mapped[str | None] = mapped_column(Text, nullable=True)  # type: ignore[assignment]
    meeting_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_tool: Mapped[str | None] = mapped_column(Text, nullable=True)  # "fireflies", "granola", etc.
    external_id: Mapped[str | None] = mapped_column(Text, nullable=True)  # Dedup key from source (unique via partial index)
    calendar_event_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("calendar_event.id", ondelete="SET NULL"), nullable=True
    )

    # LLM-extracted fields
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")

    # Relationships
    calendar_event: Mapped[CalendarEvent | None] = relationship(
        "CalendarEvent",
        foreign_keys=[calendar_event_id],
        backref="meetings",
    )

    __mapper_args__ = {
        "polymorphic_identity": "meeting",
    }

    __table_args__ = (
        Index("meeting_date_idx", "meeting_date"),
        Index("meeting_source_tool_idx", "source_tool"),
        # Partial unique index: allows multiple NULLs, enforces uniqueness on non-NULL values
        Index(
            "meeting_external_id_idx",
            "external_id",
            unique=True,
            postgresql_where=external_id.isnot(None),
        ),
        Index("meeting_extraction_status_idx", "extraction_status"),
        Index("meeting_calendar_event_idx", "calendar_event_id"),
    )

    def __init__(self, **kwargs: Any) -> None:
        if not kwargs.get("modality"):
            kwargs["modality"] = "meeting"
        super().__init__(**kwargs)

    @property
    def attendees(self) -> list["Person"]:
        """Alias for people relationship - provides semantic name for meeting attendees."""
        return self.people

    @attendees.setter
    def attendees(self, value: list["Person"]) -> None:
        """Set attendees (alias for people)."""
        self.people = value

    def as_payload(self) -> MeetingPayload:
        spawned = getattr(self, "spawned_tasks", [])
        return MeetingPayload(
            **super().as_payload(),
            title=self.title,
            meeting_date=(self.meeting_date and self.meeting_date.isoformat() or None),
            duration_minutes=self.duration_minutes,
            source_tool=self.source_tool,
            summary=self.summary,
            notes=self.notes,
            extraction_status=self.extraction_status,
            task_ids=[t.id for t in spawned],
            calendar_event_id=self.calendar_event_id,
        )

    @property
    def display_contents(self) -> dict:
        return {
            "title": self.title,
            "meeting_date": self.meeting_date and self.meeting_date.isoformat(),
            "duration_minutes": self.duration_minutes,
            "source_tool": self.source_tool,
            "summary": self.summary,
            "notes": self.notes,
            "extraction_status": self.extraction_status,
            "people": [p.display_name for p in self.people],
            "task_count": len(getattr(self, "spawned_tasks", [])),
            "tags": self.tags,
            "calendar_event_id": self.calendar_event_id,
        }

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        parts = []

        if self.title:
            parts.append(f"Meeting: {self.title}")

        if self.meeting_date:
            parts.append(f"Date: {self.meeting_date.strftime('%Y-%m-%d')}")

        if self.people:
            attendee_names = [p.display_name for p in self.people]
            parts.append(f"Attendees: {', '.join(attendee_names)}")

        if self.summary:
            parts.append(f"Summary: {self.summary}")

        if self.notes:
            parts.append(f"Notes:\n{self.notes}")

        # Include full transcript for search
        if self.content:
            parts.append(f"Transcript:\n{self.content}")

        text = "\n\n".join(parts)

        return extract.extract_text(text, modality="meeting")

    @classmethod
    def get_collections(cls) -> list[str]:
        return ["meeting"]
