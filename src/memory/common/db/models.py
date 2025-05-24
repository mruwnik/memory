"""
Database models for the knowledge base system.
"""

import pathlib
import re
from pathlib import Path
import textwrap
from typing import Any, ClassVar, cast
from PIL import Image
from sqlalchemy import (
    ARRAY,
    UUID,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    event,
    func,
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, TSVECTOR
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, Session

from memory.common import settings
from memory.common.parsers.email import parse_email_message, EmailMessage

Base = declarative_base()


@event.listens_for(Session, "before_flush")
def handle_duplicate_sha256(session, flush_context, instances):
    """
    Event listener that efficiently checks for duplicate sha256 values before flush
    and removes items with duplicate sha256 from the session.

    Uses a single query to identify all duplicates rather than querying for each item.
    """
    # Find all SourceItem objects being added
    new_items = [obj for obj in session.new if isinstance(obj, SourceItem)]
    if not new_items:
        return

    items = {}
    for item in new_items:
        try:
            if (sha256 := item.sha256) is None:
                continue

            if sha256 in items:
                session.expunge(item)
                continue

            items[sha256] = item
        except (AttributeError, TypeError):
            continue

    if not new_items:
        return

    # Query database for existing items with these sha256 values in a single query
    existing_sha256s = set(
        row[0]
        for row in session.query(SourceItem.sha256).filter(
            SourceItem.sha256.in_(items.keys())
        )
    )

    # Remove objects with duplicate sha256 values from the session
    for sha256 in existing_sha256s:
        if sha256 in items:
            session.expunge(items[sha256])


def clean_filename(filename: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", filename).strip("_")


class Chunk(Base):
    """Stores content chunks with their vector embeddings."""

    __tablename__ = "chunk"

    # The ID is also used as the vector ID in the vector database
    id = Column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    source_id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), nullable=False
    )
    file_path = Column(Text)  # Path to content if stored as a file
    content = Column(Text)  # Direct content storage
    embedding_model = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    checked_at = Column(DateTime(timezone=True), server_default=func.now())
    vector: ClassVar[list[float] | None] = None
    item_metadata: ClassVar[dict[str, Any] | None] = None

    # One of file_path or content must be populated
    __table_args__ = (
        CheckConstraint("(file_path IS NOT NULL) OR (content IS NOT NULL)"),
        Index("chunk_source_idx", "source_id"),
    )

    @property
    def data(self) -> list[bytes | str | Image.Image]:
        if self.file_path is None:
            return [cast(str, self.content)]

        path = pathlib.Path(self.file_path.replace("/app/", ""))
        if cast(str, self.file_path).endswith("*"):
            files = list(path.parent.glob(path.name))
        else:
            files = [path]

        items = []
        for file_path in files:
            if file_path.suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
                if file_path.exists():
                    items.append(Image.open(file_path))
            elif file_path.suffix == ".bin":
                items.append(file_path.read_bytes())
            else:
                items.append(file_path.read_text())
        return items


class SourceItem(Base):
    """Base class for all content in the system using SQLAlchemy's joined table inheritance."""

    __tablename__ = "source_item"

    id = Column(BigInteger, primary_key=True)
    modality = Column(Text, nullable=False)
    sha256 = Column(BYTEA, nullable=False, unique=True)
    inserted_at = Column(DateTime(timezone=True), server_default=func.now())
    tags = Column(ARRAY(Text), nullable=False, server_default="{}")
    size = Column(Integer)
    mime_type = Column(Text)

    # Content is stored in the database if it's small enough and text
    content = Column(Text)
    # Otherwise the content is stored on disk
    filename = Column(Text, nullable=True)

    # Chunks relationship
    embed_status = Column(Text, nullable=False, server_default="RAW")
    chunks = relationship("Chunk", backref="source", cascade="all, delete-orphan")

    # Discriminator column for SQLAlchemy inheritance
    type = Column(String(50))

    __mapper_args__ = {"polymorphic_on": type, "polymorphic_identity": "source_item"}

    # Add table-level constraint and indexes
    __table_args__ = (
        CheckConstraint("embed_status IN ('RAW','QUEUED','STORED','FAILED')"),
        Index("source_modality_idx", "modality"),
        Index("source_status_idx", "embed_status"),
        Index("source_tags_idx", "tags", postgresql_using="gin"),
        Index("source_filename_idx", "filename"),
    )

    @property
    def vector_ids(self):
        """Get vector IDs from associated chunks."""
        return [chunk.id for chunk in self.chunks]

    def as_payload(self) -> dict:
        return {
            "source_id": self.id,
            "tags": self.tags,
        }

    @property
    def display_contents(self) -> str | None:
        return cast(str | None, self.content) or cast(str | None, self.filename)


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
    def attachments_path(self) -> Path:
        clean_sender = clean_filename(cast(str, self.sender))
        clean_folder = clean_filename(cast(str | None, self.folder) or "INBOX")
        return Path(settings.FILE_STORAGE_DIR) / clean_sender / clean_folder

    def safe_filename(self, filename: str) -> Path:
        suffix = Path(filename).suffix
        name = clean_filename(filename.removesuffix(suffix)) + suffix
        path = self.attachments_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def as_payload(self) -> dict:
        return {
            "source_id": self.id,
            "message_id": self.message_id,
            "subject": self.subject,
            "sender": self.sender,
            "recipients": self.recipients,
            "folder": self.folder,
            "tags": self.tags + [self.sender] + self.recipients,
            "date": (self.sent_at and self.sent_at.isoformat() or None),  # type: ignore
        }

    @property
    def parsed_content(self) -> EmailMessage:
        return parse_email_message(cast(str, self.content), cast(str, self.message_id))

    @property
    def body(self) -> str:
        return self.parsed_content["body"]

    @property
    def display_contents(self) -> str | None:
        content = self.parsed_content
        return textwrap.dedent(
            """
            Subject: {subject}
            From: {sender}
            To: {recipients}
            Date: {date}
            Body: 
            {body}
            """
        ).format(
            subject=content.get("subject", ""),
            sender=content.get("from", ""),
            recipients=content.get("to", ""),
            date=content.get("date", ""),
            body=content.get("body", ""),
        )

    # Add indexes
    __table_args__ = (
        Index("mail_sent_idx", "sent_at"),
        Index("mail_recipients_idx", "recipients", postgresql_using="gin"),
        Index("mail_tsv_idx", "tsv", postgresql_using="gin"),
    )


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

    def as_payload(self) -> dict:
        return {
            "filename": self.filename,
            "content_type": self.mime_type,
            "size": self.size,
            "created_at": (self.created_at and self.created_at.isoformat() or None),  # type: ignore
            "mail_message_id": self.mail_message_id,
            "source_id": self.id,
            "tags": self.tags,
        }

    # Add indexes
    __table_args__ = (Index("email_attachment_message_idx", "mail_message_id"),)


class ChatMessage(SourceItem):
    __tablename__ = "chat_message"

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    platform = Column(Text)
    channel_id = Column(Text)
    author = Column(Text)
    sent_at = Column(DateTime(timezone=True))

    __mapper_args__ = {
        "polymorphic_identity": "chat_message",
    }

    # Add index
    __table_args__ = (Index("chat_channel_idx", "platform", "channel_id"),)


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

    def as_payload(self) -> dict:
        payload = {
            "source_id": self.id,
            "tags": self.tags,
            "title": self.title,
            "author": self.author,
            "published": self.published,
            "volume": self.volume,
            "issue": self.issue,
            "page": self.page,
            "url": self.url,
        }
        return {k: v for k, v in payload.items() if v is not None}


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

    def as_payload(self) -> dict:
        return {
            "source_id": self.id,
            "isbn": self.isbn,
            "title": self.title,
            "author": self.author,
            "publisher": self.publisher,
            "published": self.published,
            "language": self.language,
            "edition": self.edition,
            "series": self.series,
            "series_number": self.series_number,
            "tags": self.tags,
        } | (cast(dict, self.book_metadata) or {})


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

    __mapper_args__ = {"polymorphic_identity": "book_section"}
    __table_args__ = (
        Index("book_section_book_idx", "book_id"),
        Index("book_section_parent_idx", "parent_section_id"),
        Index("book_section_level_idx", "section_level", "section_number"),
    )

    def as_payload(self) -> dict:
        return {
            "source_id": self.id,
            "book_id": self.book_id,
            "section_title": self.section_title,
            "section_number": self.section_number,
            "section_level": self.section_level,
            "start_page": self.start_page,
            "end_page": self.end_page,
            "tags": self.tags,
        }


class BlogPost(SourceItem):
    __tablename__ = "blog_post"

    id = Column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    url = Column(Text, unique=True)
    title = Column(Text)
    published = Column(DateTime(timezone=True))

    __mapper_args__ = {
        "polymorphic_identity": "blog_post",
    }


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

    __mapper_args__ = {
        "polymorphic_identity": "github_item",
    }

    __table_args__ = (
        CheckConstraint("kind IN ('issue', 'pr', 'comment', 'project_card')"),
        Index("gh_repo_kind_idx", "repo_path", "kind"),
        Index("gh_issue_lookup_idx", "repo_path", "kind", "number"),
        Index("gh_labels_idx", "labels", postgresql_using="gin"),
    )


class RssFeed(Base):
    __tablename__ = "rss_feeds"

    id = Column(BigInteger, primary_key=True)
    url = Column(Text, nullable=False, unique=True)
    title = Column(Text)
    description = Column(Text)
    tags = Column(ARRAY(Text), nullable=False, server_default="{}")
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
        Index("rss_feeds_active_idx", "active", "last_checked_at"),
        Index("rss_feeds_tags_idx", "tags", postgresql_using="gin"),
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
