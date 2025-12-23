"""
Database models for the knowledge base system.
"""

import pathlib
import textwrap
from datetime import datetime
from collections.abc import Collection
from typing import Any, Annotated, Sequence, cast

from PIL import Image
from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import relationship

from memory.common import settings
import memory.common.extract as extract
import memory.common.summarizer as summarizer
import memory.common.formatters.observation as observation

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

    # Add indexes
    __table_args__ = (
        Index("mail_sent_idx", "sent_at"),
        Index("mail_recipients_idx", "recipients", postgresql_using="gin"),
        Index("mail_tsv_idx", "tsv", postgresql_using="gin"),
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
    milestone = Column(Text, nullable=True)
    comment_count = Column(Integer, nullable=True)

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
    )


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
