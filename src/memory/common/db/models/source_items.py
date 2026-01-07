"""
Database models for the knowledge base system.
"""

import pathlib
import textwrap
from datetime import datetime
from collections.abc import Collection
from typing import Any, Annotated, Sequence, cast

from PIL import Image
import zlib

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import relationship

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
from memory.common.db.models.mcp import (
    MCPServer,
    MCPServerAssignment,
)


class MailMessagePayload(SourceItemPayload):
    message_id: Annotated[str, "Unique email message identifier"]
    subject: Annotated[str, "Email subject line"]
    sender: Annotated[str, "Email sender address"]
    recipients: Annotated[list[str], "List of recipient email addresses"]
    folder: Annotated[str, "Email folder name"]
    date: Annotated[str | None, "Email sent date in ISO format"]


class MailMessage(SourceItem):
    __tablename__ = "mail_message"

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    message_id = Column(Text, unique=True)
    subject = Column(Text)
    sender = Column(Text)
    recipients = Column(ARRAY(Text))
    sent_at = Column(DateTime(timezone=True))
    folder = Column(Text)
    tsv = Column(TSVECTOR)

    # Sync tracking for deletion detection
    email_account_id = Column(
        BigInteger, ForeignKey("email_accounts.id", ondelete="SET NULL"), nullable=True
    )
    imap_uid = Column(Text, nullable=True)

    def __init__(self, **kwargs):
        if not kwargs.get("modality"):
            kwargs["modality"] = "email"
        super().__init__(**kwargs)

    attachments = relationship(
        "EmailAttachment",
        back_populates="mail_message",
        foreign_keys="EmailAttachment.mail_message_id",
        cascade="all, delete-orphan",
    )
    email_account = relationship("EmailAccount", foreign_keys=[email_account_id])

    __mapper_args__ = {
        "polymorphic_identity": "mail_message",
    }

    @property
    def attachments_path(self) -> pathlib.Path:
        clean_sender = clean_filename(cast(str, self.sender))
        clean_folder = clean_filename(cast(str | None, self.folder) or "INBOX")
        return pathlib.Path(settings.EMAIL_STORAGE_DIR) / clean_sender / clean_folder

    def safe_filename(self, filename: str) -> pathlib.Path:
        suffix = pathlib.Path(filename).suffix
        name = clean_filename(filename.removesuffix(suffix)) + suffix
        path = self.attachments_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def as_payload(self) -> MailMessagePayload:
        base_payload = super().as_payload() | {
            "tags": (cast(list[str], self.tags) or [])
            + [cast(str, self.sender)]
            + (cast(list[str], self.recipients) or [])
        }
        return MailMessagePayload(
            **cast(dict, base_payload),
            message_id=cast(str, self.message_id),
            subject=cast(str, self.subject),
            sender=cast(str, self.sender),
            recipients=cast(list[str], self.recipients),
            folder=cast(str, self.folder),
            date=(self.sent_at and self.sent_at.isoformat() or None),  # type: ignore
        )

    @property
    def parsed_content(self) -> dict[str, Any]:
        from memory.parsers.email import parse_email_message

        return cast(
            dict[str, Any],
            parse_email_message(cast(str, self.content), cast(str, self.message_id)),
        )

    @property
    def body(self) -> str:
        return self.parsed_content["body"]

    def format_content(self, content: dict[str, Any]) -> str:
        sender = (
            cast(str, self.sender) or content.get("from") or content.get("sender", "")
        )
        recipients = (
            cast(list[str], self.recipients)
            or content.get("to")
            or content.get("recipients", [])
        )
        date = (
            cast(datetime, self.sent_at) and self.sent_at.isoformat()
        ) or content.get("date", "")

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
                subject=cast(str, self.subject) or content.get("subject", ""),
                sender=sender,
                recipients=", ".join(recipients),
                date=date,
                body=content.get("body", ""),
            )
            .strip()
        )

    @property
    def display_contents(self) -> dict | None:
        return {
            **cast(dict, super().display_contents),
            "content": self.body,
            "subject": self.subject,
            "sender": self.sender,
            "recipients": self.recipients,
            "date": cast(datetime | None, self.sent_at) and self.sent_at.isoformat(),
        }

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        content = self.parsed_content
        chunks = extract.extract_text(cast(str, self.body), modality="mail")

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
        return cast(str | None, self.subject)

    # Add indexes
    __table_args__ = (
        Index("mail_sent_idx", "sent_at"),
        Index("mail_recipients_idx", "recipients", postgresql_using="gin"),
        Index("mail_tsv_idx", "tsv", postgresql_using="gin"),
        Index("mail_account_idx", "email_account_id"),
        Index("mail_imap_uid_idx", "email_account_id", "folder", "imap_uid"),
    )


class EmailAttachmentPayload(SourceItemPayload):
    filename: Annotated[str, "Name of the document file"]
    content_type: Annotated[str, "MIME type of the document"]
    mail_message_id: Annotated[int, "Associated email message ID"]
    sent_at: Annotated[str | None, "Document creation timestamp"]
    created_at: Annotated[str | None, "Document creation timestamp"]


class EmailAttachment(SourceItem):
    __tablename__ = "email_attachment"

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    mail_message_id = Column(
        BigInteger, ForeignKey("mail_message.id", ondelete="CASCADE"), nullable=False
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    mail_message = relationship(
        "MailMessage", back_populates="attachments", foreign_keys=[mail_message_id]
    )

    __mapper_args__ = {
        "polymorphic_identity": "email_attachment",
    }

    def as_payload(self) -> EmailAttachmentPayload:
        return EmailAttachmentPayload(
            **super().as_payload(),
            created_at=(self.created_at and self.created_at.isoformat() or None),  # type: ignore
            filename=cast(str, self.filename),
            content_type=cast(str, self.mime_type),
            mail_message_id=cast(int, self.mail_message_id),
            sent_at=(
                self.mail_message.sent_at
                and self.mail_message.sent_at.isoformat()
                or None
            ),  # type: ignore
        )

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        if cast(str | None, self.filename):
            contents = (
                settings.FILE_STORAGE_DIR / cast(str, self.filename)
            ).read_bytes()
        else:
            contents = cast(str, self.content)

        return extract.extract_data_chunks(cast(str, self.mime_type), contents)

    @property
    def display_contents(self) -> dict:
        return {
            **cast(dict, super().display_contents),
            **self.mail_message.display_contents,
        }

    # Add indexes
    __table_args__ = (Index("email_attachment_message_idx", "mail_message_id"),)

    @classmethod
    def get_collections(cls) -> list[str]:
        """EmailAttachment can go to different collections based on mime_type"""
        return ["doc", "text", "blog", "photo", "book"]


class ChatMessage(SourceItem):
    __tablename__ = "chat_message"

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    platform = Column(Text)
    channel_id = Column(Text)  # Keep as Text for cross-platform compatibility
    author = Column(Text)
    sent_at = Column(DateTime(timezone=True))

    __mapper_args__ = {
        "polymorphic_identity": "chat_message",
    }

    # Add index
    __table_args__ = (Index("chat_channel_idx", "platform", "channel_id"),)


class DiscordMessage(SourceItem):
    """Discord-specific chat message with rich metadata"""

    __tablename__ = "discord_message"

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )

    sent_at = Column(DateTime(timezone=True), nullable=False)
    server_id = Column(BigInteger, ForeignKey("discord_servers.id"), nullable=True)
    channel_id = Column(BigInteger, ForeignKey("discord_channels.id"), nullable=False)
    from_id = Column(BigInteger, ForeignKey("discord_users.id"), nullable=False)
    recipient_id = Column(BigInteger, ForeignKey("discord_users.id"), nullable=False)
    message_id = Column(BigInteger, nullable=False)  # Discord message snowflake ID

    # Discord-specific metadata
    message_type = Column(
        Text, server_default="default"
    )  # "default", "reply", "thread_starter"
    reply_to_message_id = Column(
        BigInteger, nullable=True
    )  # Discord message snowflake ID if replying
    thread_id = Column(
        BigInteger, nullable=True
    )  # Discord thread snowflake ID if in thread
    edited_at = Column(DateTime(timezone=True), nullable=True)
    images = Column(ARRAY(Text), nullable=True)  # List of image URLs

    channel = relationship("DiscordChannel", foreign_keys=[channel_id])
    server = relationship("DiscordServer", foreign_keys=[server_id])
    from_user = relationship("DiscordUser", foreign_keys=[from_id])
    recipient_user = relationship("DiscordUser", foreign_keys=[recipient_id])

    @property
    def allowed_tools(self) -> set[str]:
        return set(
            (self.channel.allowed_tools if self.channel else [])
            + (self.from_user.allowed_tools if self.from_user else [])
            + (self.server.allowed_tools if self.server else [])
        )

    @property
    def disallowed_tools(self) -> set[str]:
        return set(
            (self.channel.disallowed_tools if self.channel else [])
            + (self.from_user.disallowed_tools if self.from_user else [])
            + (self.server.disallowed_tools if self.server else [])
        )

    def tool_allowed(self, tool: str) -> bool:
        return not (self.disallowed_tools and tool in self.disallowed_tools) and (
            not self.allowed_tools or tool in self.allowed_tools
        )

    def filter_tools(self, tools: Collection[str] | None = None) -> set[str]:
        if tools is None:
            return self.allowed_tools - self.disallowed_tools
        return set(tools) - self.disallowed_tools & self.allowed_tools

    @property
    def ignore_messages(self) -> bool:
        return (
            (self.server and self.server.ignore_messages)
            or (self.channel and self.channel.ignore_messages)
            or (self.from_user and self.from_user.ignore_messages)
        )

    @property
    def system_prompt(self) -> str:
        prompts = [
            (self.from_user and self.from_user.system_prompt),
            (self.channel and self.channel.system_prompt),
            (self.server and self.server.system_prompt),
        ]
        return "\n\n".join(p for p in prompts if p)

    @property
    def chattiness_threshold(self) -> int:
        vals = [
            (self.from_user and self.from_user.chattiness_threshold),
            (self.channel and self.channel.chattiness_threshold),
            (self.server and self.server.chattiness_threshold),
            90,
        ]
        return min(val for val in vals if val is not None)

    @property
    def title(self) -> str:
        return textwrap.dedent("""
            <message>
                <id>{message_id}</id>
                <from>{from_user}</from>
                <sent_at>{sent_at}</sent_at>
                <content>{content}</content>
            </message>
        """).format(
            message_id=self.message_id,
            from_user=self.from_user.username,
            sent_at=self.sent_at.isoformat()[:19],
            content=self.content,
        )

    def as_content(self) -> dict[str, Any]:
        """Return message content ready for LLM (text + images from disk)."""
        content = {"text": self.title, "images": []}
        for path in cast(list[str] | None, self.images) or []:
            try:
                full_path = settings.FILE_STORAGE_DIR / path
                if full_path.exists():
                    image = Image.open(full_path)
                    content["images"].append(image)
            except Exception:
                pass  # Skip failed image loads

        return content

    def get_mcp_servers(self, session) -> list[MCPServer]:
        entity_ids = list(
            filter(
                None,
                [
                    self.recipient_id,
                    self.from_id,
                    self.channel_id,
                    self.server_id,
                ],
            )
        )
        if not entity_ids:
            return None

        return (
            session.query(MCPServer)
            .filter(
                MCPServerAssignment.entity_id.in_(entity_ids),
            )
            .all()
        )

    __mapper_args__ = {
        "polymorphic_identity": "discord_message",
    }

    __table_args__ = (
        Index("discord_message_discord_id_idx", "message_id", unique=True),
        Index(
            "discord_message_server_channel_idx",
            "server_id",
            "channel_id",
        ),
        Index("discord_message_from_idx", "from_id"),
        Index("discord_message_recipient_idx", "recipient_id"),
    )

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        content = cast(str | None, self.content)
        if not content:
            return []
        prev = getattr(self, "messages_before", [])
        content = "\n\n".join(prev) + "\n\n" + self.title
        return extract.extract_text(content)


class GitCommit(SourceItem):
    __tablename__ = "git_commit"

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    repo_path = Column(Text)
    commit_sha = Column(Text, unique=True)
    author_name = Column(Text)
    author_email = Column(Text)
    author_date = Column(DateTime(timezone=True))
    diff_summary = Column(Text)
    files_changed = Column(ARRAY(Text))

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

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    exif_taken_at = Column(DateTime(timezone=True))
    exif_lat = Column(Numeric(9, 6))
    exif_lon = Column(Numeric(9, 6))
    camera = Column(Text)

    __mapper_args__ = {
        "polymorphic_identity": "photo",
    }

    # Add index
    __table_args__ = (Index("photo_taken_idx", "exif_taken_at"),)

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        image = Image.open(settings.FILE_STORAGE_DIR / cast(str, self.filename))
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

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    title = Column(Text)
    author = Column(Text, nullable=True)
    published = Column(DateTime(timezone=True), nullable=True)
    volume = Column(Text, nullable=True)
    issue = Column(Text, nullable=True)
    page = Column(Integer, nullable=True)
    url = Column(Text, nullable=True)

    __mapper_args__ = {
        "polymorphic_identity": "comic",
    }

    __table_args__ = (Index("comic_author_idx", "author"),)

    def as_payload(self) -> ComicPayload:
        return ComicPayload(
            **super().as_payload(),
            title=cast(str, self.title),
            author=cast(str | None, self.author),
            published=(self.published and self.published.isoformat() or None),  # type: ignore
            volume=cast(str | None, self.volume),
            issue=cast(str | None, self.issue),
            page=cast(int | None, self.page),
            url=cast(str | None, self.url),
        )

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        image = Image.open(settings.FILE_STORAGE_DIR / cast(str, self.filename))
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

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    book_id = Column(
        BigInteger, ForeignKey("book.id", ondelete="CASCADE"), nullable=False
    )

    section_title = Column(Text)
    section_number = Column(Integer)
    section_level = Column(Integer)  # 1=chapter, 2=section, 3=subsection
    start_page = Column(Integer)
    end_page = Column(Integer)

    # Parent-child relationships for nested sections
    parent_section_id = Column(BigInteger, ForeignKey("book_section.id"))

    book = relationship("Book", backref="sections")
    parent = relationship(
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
        return cast(str | None, self.section_title)

    def as_payload(self) -> BookSectionPayload:
        return BookSectionPayload(
            **super().as_payload(),
            title=cast(str, self.book.title),
            author=cast(str | None, self.book.author),
            book_id=cast(int, self.book_id),
            section_title=cast(str, self.section_title),
            section_number=cast(int, self.section_number),
            section_level=cast(int, self.section_level),
            start_page=cast(int, self.start_page),
            end_page=cast(int, self.end_page),
        )

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        content = cast(str, self.content.strip())
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

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    url = Column(Text, unique=True)
    title = Column(Text)
    author = Column(Text, nullable=True)
    published = Column(DateTime(timezone=True), nullable=True)

    # Additional metadata from webpage parsing
    description = Column(Text, nullable=True)  # Meta description or excerpt
    domain = Column(Text, nullable=True)  # Domain of the source website
    word_count = Column(Integer, nullable=True)  # Approximate word count
    images = Column(ARRAY(Text), nullable=True)  # List of image URLs

    # Store original metadata from parser
    webpage_metadata = Column(JSONB, nullable=True)

    __mapper_args__ = {
        "polymorphic_identity": "blog_post",
    }

    __table_args__ = (
        Index("blog_post_author_idx", "author"),
        Index("blog_post_domain_idx", "domain"),
        Index("blog_post_published_idx", "published"),
        Index("blog_post_word_count_idx", "word_count"),
    )

    def as_payload(self) -> BlogPostPayload:
        published_date = cast(datetime | None, self.published)
        metadata = cast(dict | None, self.webpage_metadata) or {}

        return BlogPostPayload(
            **super().as_payload(),
            url=cast(str, self.url),
            title=cast(str, self.title),
            author=cast(str | None, self.author),
            published=(published_date and published_date.isoformat() or None),  # type: ignore
            description=cast(str | None, self.description),
            domain=cast(str | None, self.domain),
            word_count=cast(int | None, self.word_count),
            **metadata,
        )

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        return chunk_mixed(cast(str, self.content), cast(list[str], self.images))


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

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    url = Column(Text, unique=True)
    title = Column(Text)
    description = Column(Text, nullable=True)
    authors = Column(ARRAY(Text), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    modified_at = Column(DateTime(timezone=True), nullable=True)
    slug = Column(Text, nullable=True)
    karma = Column(Integer, nullable=True)
    votes = Column(Integer, nullable=True)
    comments = Column(Integer, nullable=True)
    words = Column(Integer, nullable=True)
    score = Column(Integer, nullable=True)
    images = Column(ARRAY(Text), nullable=True)

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
            url=cast(str, self.url),
            title=cast(str, self.title),
            description=cast(str | None, self.description),
            authors=cast(list[str] | None, self.authors),
            published=(self.published_at and self.published_at.isoformat() or None),  # type: ignore
            slug=cast(str | None, self.slug),
            karma=cast(int | None, self.karma),
            votes=cast(int | None, self.votes),
            score=cast(int | None, self.score),
            comments=cast(int | None, self.comments),
        )

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        return chunk_mixed(cast(str, self.content), cast(list[str], self.images))

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

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    path = Column(Text)

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

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    kind = Column(Text, nullable=False)
    repo_path = Column(Text, nullable=False)
    number = Column(Integer)
    parent_number = Column(Integer)
    commit_sha = Column(Text)
    state = Column(Text)
    title = Column(Text)
    labels = Column(ARRAY(Text))
    author = Column(Text)
    created_at = Column(DateTime(timezone=True))
    closed_at = Column(DateTime(timezone=True))
    merged_at = Column(DateTime(timezone=True))
    diff_summary = Column(Text)

    payload = Column(JSONB)

    # New fields for change detection and tracking
    github_updated_at = Column(DateTime(timezone=True))  # GitHub's updated_at
    content_hash = Column(Text)  # Hash of body + comments for change detection
    repo_id = Column(
        BigInteger, ForeignKey("github_repos.id", ondelete="SET NULL"), nullable=True
    )

    # GitHub Projects v2 fields
    project_status = Column(Text, nullable=True)
    project_priority = Column(Text, nullable=True)
    project_fields = Column(JSONB, nullable=True)  # All project field values

    # Additional tracking
    assignees = Column(ARRAY(Text), nullable=True)
    milestone_id = Column(
        BigInteger,
        ForeignKey("github_milestones.id", ondelete="SET NULL"),
        nullable=True,
    )
    comment_count = Column(Integer, nullable=True)

    # Relationship to milestone
    milestone_rel = relationship("GithubMilestone", back_populates="items")

    # Relationship to PR-specific data
    pr_data = relationship(
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
            kind=cast(str, self.kind),
            repo_path=cast(str, self.repo_path),
            number=cast(int | None, self.number),
            state=cast(str | None, self.state),
            title=cast(str | None, self.title),
            author=cast(str | None, self.author),
            labels=cast(list[str] | None, self.labels),
            assignees=cast(list[str] | None, self.assignees),
            milestone=self.milestone_rel.title if self.milestone_rel else None,
            project_status=cast(str | None, self.project_status),
            project_priority=cast(str | None, self.project_priority),
            created_at=cast(datetime | None, self.created_at),
            closed_at=cast(datetime | None, self.closed_at),
            merged_at=cast(datetime | None, self.merged_at),
        )

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        """Override to use 'github' modality instead of default 'text'."""
        content = cast(str | None, self.content)
        if content:
            return extract.extract_text(content, modality="github")
        return []


class GithubPRData(Base):
    """PR-specific data linked to GithubItem. Not a SourceItem - not indexed separately."""

    __tablename__ = "github_pr_data"

    id = Column(BigInteger, primary_key=True)
    github_item_id = Column(
        BigInteger,
        ForeignKey("github_item.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # Diff (compressed with zlib)
    diff_compressed = Column(LargeBinary, nullable=True)

    # File changes as structured data
    # [{filename, status, additions, deletions, patch?}]
    files = Column(JSONB, nullable=True)

    # Stats
    additions = Column(Integer, nullable=True)
    deletions = Column(Integer, nullable=True)
    changed_files_count = Column(Integer, nullable=True)

    # Reviews (structured)
    # [{user, state, body, submitted_at}]
    reviews = Column(JSONB, nullable=True)

    # Review comments (line-by-line code comments)
    # [{user, body, path, line, diff_hunk, created_at}]
    review_comments = Column(JSONB, nullable=True)

    # Relationship back to GithubItem
    github_item = relationship("GithubItem", back_populates="pr_data")

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

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    note_type = Column(Text, nullable=True)
    subject = Column(Text, nullable=True)

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
            note_type=cast(str | None, self.note_type),
            subject=cast(str | None, self.subject),
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

    def save_to_file(self):
        if not self.filename:
            self.filename = f"{self.subject}.md"
        path = settings.NOTES_STORAGE_DIR / self.filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(cast(str, self.content))

    @staticmethod
    def as_text(content: str, subject: str | None = None) -> str:
        text = content
        if subject:
            text = f"# {subject}\n\n{text}"
        return text

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        return extract.extract_text(
            self.as_text(cast(str, self.content), cast(str | None, self.subject))
        )

    @classmethod
    def get_collections(cls) -> list[str]:
        return ["text"]  # Notes go to the text collection

    @property
    def title(self) -> str | None:
        return cast(str | None, self.subject)


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

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    session_id = Column(Text, nullable=True)
    observation_type = Column(
        Text, nullable=False
    )  # belief, preference, pattern, contradiction, behavior
    subject = Column(Text, nullable=False)  # What/who the observation is about
    evidence = Column(JSONB)  # Supporting context, quotes, etc.
    agent_model = Column(Text, nullable=False)  # Which AI model made this observation

    # Relationships
    contradictions_as_first = relationship(
        "ObservationContradiction",
        foreign_keys="ObservationContradiction.observation_1_id",
        back_populates="observation_1",
        cascade="all, delete-orphan",
    )
    contradictions_as_second = relationship(
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

    def __init__(self, **kwargs):
        if not kwargs.get("modality"):
            kwargs["modality"] = "observation"
        super().__init__(**kwargs)

    def as_payload(self) -> AgentObservationPayload:
        return AgentObservationPayload(
            **super().as_payload(),
            observation_type=cast(str, self.observation_type),
            subject=cast(str, self.subject),
            confidence=self.confidence_dict,
            evidence=cast(dict | None, self.evidence),
            agent_model=cast(str, self.agent_model),
            session_id=cast(str | None, self.session_id) and str(self.session_id),
        )

    @property
    def all_contradictions(self):
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
        semantic_text = observation.generate_semantic_text(
            cast(str, self.subject),
            cast(str, self.observation_type),
            cast(str, self.content),
            cast(observation.Evidence | None, self.evidence),
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
            cast(str, self.subject),
            cast(str, self.content),
            cast(datetime, self.inserted_at),
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
            cast(dict | None, self.evidence) and self.evidence.get("quote"),
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
        return cast(str | None, self.subject)


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

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )

    # Google-specific identifiers
    google_file_id = Column(Text, nullable=False, unique=True)  # Drive file ID
    google_modified_at = Column(
        DateTime(timezone=True), nullable=True
    )  # For change detection

    # Document metadata
    title = Column(Text, nullable=False)
    original_mime_type = Column(
        Text, nullable=True
    )  # e.g., "application/vnd.google-apps.document"
    folder_id = Column(
        BigInteger, ForeignKey("google_folders.id", ondelete="SET NULL"), nullable=True
    )
    folder_path = Column(Text, nullable=True)  # e.g., "My Drive/Work/Projects"

    # Authorship tracking
    owner = Column(Text, nullable=True)  # Email of owner
    last_modified_by = Column(Text, nullable=True)  # Email of last modifier

    # Content stats
    word_count = Column(Integer, nullable=True)

    # Content hash for change detection
    content_hash = Column(Text, nullable=True)

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
            google_file_id=cast(str, self.google_file_id),
            title=cast(str, self.title),
            original_mime_type=cast(str | None, self.original_mime_type),
            folder_path=cast(str | None, self.folder_path),
            owner=cast(str | None, self.owner),
            last_modified_by=cast(str | None, self.last_modified_by),
            google_modified_at=(
                self.google_modified_at and self.google_modified_at.isoformat()
            ),
            word_count=cast(int | None, self.word_count),
        )

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        content = cast(str | None, self.content)
        if content:
            return extract.extract_text(content, modality="doc")
        return []

    @classmethod
    def get_collections(cls) -> list[str]:
        return ["doc"]


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

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )

    task_title = Column(Text, nullable=False)
    due_date = Column(DateTime(timezone=True), nullable=True)
    priority = Column(Text, nullable=True)  # low, medium, high, urgent
    status = Column(Text, nullable=False, server_default="pending")
    recurrence = Column(Text, nullable=True)  # RRULE format for habits
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Link to source that spawned this task (email, note, etc.)
    source_item_id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="SET NULL"), nullable=True
    )
    source_item = relationship(
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

    def __init__(self, **kwargs):
        if not kwargs.get("modality"):
            kwargs["modality"] = "task"
        super().__init__(**kwargs)

    def as_payload(self) -> TaskPayload:
        return TaskPayload(
            **super().as_payload(),
            title=cast(str, self.task_title),
            due_date=(self.due_date and self.due_date.isoformat() or None),
            priority=cast(str | None, self.priority),
            status=cast(str, self.status),
            recurrence=cast(str | None, self.recurrence),
            source_item_id=cast(int | None, self.source_item_id),
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
        parts = [cast(str, self.task_title)]
        if self.content:
            parts.append(cast(str, self.content))
        if self.due_date:
            parts.append(f"Due: {self.due_date.isoformat()}")
        text = "\n\n".join(parts)
        return extract.extract_text(text, modality="task")

    @classmethod
    def get_collections(cls) -> list[str]:
        return ["task"]

    @property
    def title(self) -> str | None:
        return cast(str | None, self.task_title)


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

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )

    # Core event fields
    event_title = Column(Text, nullable=False)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=True)
    all_day = Column(Boolean, default=False, nullable=False)
    location = Column(Text, nullable=True)
    recurrence_rule = Column(Text, nullable=True)  # RRULE format

    # Sync metadata
    calendar_account_id = Column(
        BigInteger, ForeignKey("calendar_accounts.id", ondelete="SET NULL"), nullable=True
    )
    calendar_name = Column(Text, nullable=True)
    external_id = Column(Text, nullable=True)  # For dedup/sync

    # Relationship
    calendar_account = relationship("CalendarAccount", foreign_keys=[calendar_account_id])

    # Flexible metadata (attendees, meeting links, conference info, etc.)
    event_metadata = Column(JSONB, default=dict)

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

    def __init__(self, **kwargs):
        if not kwargs.get("modality"):
            kwargs["modality"] = "calendar"
        super().__init__(**kwargs)

    def as_payload(self) -> CalendarEventPayload:
        return CalendarEventPayload(
            **super().as_payload(),
            event_title=cast(str, self.event_title),
            start_time=cast(datetime, self.start_time).isoformat(),
            end_time=(self.end_time and self.end_time.isoformat() or None),
            all_day=cast(bool, self.all_day),
            location=cast(str | None, self.location),
            recurrence_rule=cast(str | None, self.recurrence_rule),
            calendar_account_id=cast(int | None, self.calendar_account_id),
            calendar_name=cast(str | None, self.calendar_name),
            external_id=cast(str | None, self.external_id),
            event_metadata=cast(dict | None, self.event_metadata),
        )

    @property
    def display_contents(self) -> dict:
        return {
            "title": self.event_title,
            "description": self.content,
            "start_time": cast(datetime, self.start_time).isoformat(),
            "end_time": self.end_time and self.end_time.isoformat(),
            "all_day": self.all_day,
            "location": self.location,
            "calendar": self.calendar_name,
            "attendees": (self.event_metadata or {}).get("attendees"),
            "tags": self.tags,
        }

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        parts = [cast(str, self.event_title)]

        if self.content:
            parts.append(cast(str, self.content))

        if self.location:
            parts.append(f"Location: {self.location}")

        metadata = cast(dict | None, self.event_metadata) or {}
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
        return cast(str | None, self.event_title)


# Association table for Meeting <-> Person many-to-many relationship
meeting_attendees = Table(
    "meeting_attendees",
    Base.metadata,
    Column(
        "meeting_id",
        BigInteger,
        ForeignKey("meeting.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "person_id",
        BigInteger,
        ForeignKey("people.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class MeetingPayload(SourceItemPayload):
    title: Annotated[str | None, "Title of the meeting"]
    meeting_date: Annotated[str | None, "Date/time when the meeting occurred (ISO format)"]
    duration_minutes: Annotated[int | None, "Duration of the meeting in minutes"]
    source_tool: Annotated[str | None, "Tool that generated the transcript (fireflies, granola, etc.)"]
    summary: Annotated[str | None, "LLM-generated summary of the meeting"]
    notes: Annotated[str | None, "LLM-extracted key points and decisions"]
    extraction_status: Annotated[str, "Status of LLM extraction: pending, processing, complete, failed"]
    attendee_ids: Annotated[list[int], "IDs of Person records who attended"]
    task_ids: Annotated[list[int], "IDs of Task records extracted from this meeting"]
    calendar_event_id: Annotated[int | None, "ID of linked CalendarEvent if available"]


class Meeting(SourceItem):
    """A meeting transcript with extracted summary, notes, tasks, and attendee links."""

    __tablename__ = "meeting"

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )

    # Core metadata
    title = Column(Text, nullable=True)
    meeting_date = Column(DateTime(timezone=True), nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    source_tool = Column(Text, nullable=True)  # "fireflies", "granola", etc.
    external_id = Column(Text, nullable=True)  # Dedup key from source (unique via partial index)
    calendar_event_id = Column(
        BigInteger, ForeignKey("calendar_event.id", ondelete="SET NULL"), nullable=True
    )

    # LLM-extracted fields
    summary = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    extraction_status = Column(Text, nullable=False, server_default="pending")

    # Relationships
    attendees = relationship(
        "Person",
        secondary=meeting_attendees,
        backref="meetings",
        lazy="selectin",
    )
    calendar_event = relationship(
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

    def __init__(self, **kwargs):
        if not kwargs.get("modality"):
            kwargs["modality"] = "meeting"
        super().__init__(**kwargs)

    def as_payload(self) -> MeetingPayload:
        return MeetingPayload(
            **super().as_payload(),
            title=cast(str | None, self.title),
            meeting_date=(self.meeting_date and self.meeting_date.isoformat() or None),
            duration_minutes=cast(int | None, self.duration_minutes),
            source_tool=cast(str | None, self.source_tool),
            summary=cast(str | None, self.summary),
            notes=cast(str | None, self.notes),
            extraction_status=cast(str, self.extraction_status),
            attendee_ids=[p.id for p in self.attendees],
            task_ids=[t.id for t in self.spawned_tasks],
            calendar_event_id=cast(int | None, self.calendar_event_id),
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
            "attendees": [p.display_name for p in self.attendees],
            "task_count": len(self.spawned_tasks),
            "tags": self.tags,
            "calendar_event_id": self.calendar_event_id,
        }

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        parts = []

        if self.title:
            parts.append(f"Meeting: {self.title}")

        if self.meeting_date:
            parts.append(f"Date: {self.meeting_date.strftime('%Y-%m-%d')}")

        if self.attendees:
            attendee_names = [p.display_name for p in self.attendees]
            parts.append(f"Attendees: {', '.join(attendee_names)}")

        if self.summary:
            parts.append(f"Summary: {self.summary}")

        if self.notes:
            parts.append(f"Notes:\n{self.notes}")

        # Include full transcript for search
        if self.content:
            parts.append(f"Transcript:\n{cast(str, self.content)}")

        text = "\n\n".join(parts)
        return extract.extract_text(text, modality="meeting")

    @classmethod
    def get_collections(cls) -> list[str]:
        return ["meeting"]
