"""
Database models for the knowledge base system.
"""

import pathlib
import re
import textwrap
from datetime import datetime
from typing import Any, Sequence, cast
import uuid

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
from sqlalchemy.orm import Session, relationship

from memory.common import settings
import memory.common.extract as extract
import memory.common.collections as collections
import memory.common.chunker as chunker
import memory.common.summarizer as summarizer

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


def image_filenames(chunk_id: str, images: list[Image.Image]) -> list[str]:
    for i, image in enumerate(images):
        if not image.filename:  # type: ignore
            filename = settings.CHUNK_STORAGE_DIR / f"{chunk_id}_{i}.{image.format}"  # type: ignore
            image.save(filename)
            image.filename = str(filename)  # type: ignore

    return [image.filename for image in images]  # type: ignore


def add_pics(chunk: str, images: list[Image.Image]) -> list[extract.MulitmodalChunk]:
    return [chunk] + [
        i
        for i in images
        if getattr(i, "filename", None) and i.filename in chunk  # type: ignore
    ]


def merge_metadata(*metadata: dict[str, Any]) -> dict[str, Any]:
    final = {}
    for m in metadata:
        if tags := set(m.pop("tags", [])):
            final["tags"] = tags | final.get("tags", set())
        final |= m
    return final


def chunk_mixed(content: str, image_paths: Sequence[str]) -> list[extract.DataChunk]:
    if not content.strip():
        return []

    images = [Image.open(settings.FILE_STORAGE_DIR / image) for image in image_paths]

    summary, tags = summarizer.summarize(content)
    full_text: extract.DataChunk = extract.DataChunk(
        data=[content.strip(), *images], metadata={"tags": tags}
    )

    chunks: list[extract.DataChunk] = [full_text]
    tokens = chunker.approx_token_count(content)
    if tokens > chunker.DEFAULT_CHUNK_TOKENS * 2:
        chunks += [
            extract.DataChunk(data=add_pics(c, images), metadata={"tags": tags})
            for c in chunker.chunk_text(content)
        ]
        chunks.append(extract.DataChunk(data=[summary], metadata={"tags": tags}))

    return [c for c in chunks if c.data]


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
    file_paths = Column(
        ARRAY(Text), nullable=True
    )  # Path to content if stored as a file
    content = Column(Text)  # Direct content storage
    embedding_model = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    checked_at = Column(DateTime(timezone=True), server_default=func.now())
    vector: list[float] = []
    item_metadata: dict[str, Any] = {}
    images: list[Image.Image] = []

    # One of file_path or content must be populated
    __table_args__ = (
        CheckConstraint("(file_paths IS NOT NULL) OR (content IS NOT NULL)"),
        Index("chunk_source_idx", "source_id"),
    )

    @property
    def chunks(self) -> list[extract.MulitmodalChunk]:
        chunks: list[extract.MulitmodalChunk] = []
        if cast(str | None, self.content):
            chunks = [cast(str, self.content)]
        if self.images:
            chunks += self.images
        elif cast(Sequence[str] | None, self.file_paths):
            chunks += [
                Image.open(pathlib.Path(cast(str, cp))) for cp in self.file_paths
            ]
        return chunks

    @property
    def data(self) -> list[bytes | str | Image.Image]:
        if self.file_paths is None:
            return [cast(str, self.content)]

        paths = [pathlib.Path(cast(str, p)) for p in self.file_paths]
        files = [path for path in paths if path.exists()]

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
    __allow_unmapped__ = True

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

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        chunks: list[extract.DataChunk] = []
        content = cast(str | None, self.content)
        if content:
            chunks = [extract.DataChunk(data=[c]) for c in chunker.chunk_text(content)]

        if content and len(content) > chunker.DEFAULT_CHUNK_TOKENS * 2:
            summary, tags = summarizer.summarize(content)
            chunks.append(extract.DataChunk(data=[summary], metadata={"tags": tags}))

        mime_type = cast(str | None, self.mime_type)
        if mime_type and mime_type.startswith("image/"):
            chunks.append(extract.DataChunk(data=[Image.open(self.filename)]))
        return chunks

    def _make_chunk(
        self, data: extract.DataChunk, metadata: dict[str, Any] = {}
    ) -> Chunk:
        chunk_id = str(uuid.uuid4())
        text = "\n\n".join(c for c in data.data if isinstance(c, str) and c.strip())
        images = [c for c in data.data if isinstance(c, Image.Image)]
        image_names = image_filenames(chunk_id, images)

        chunk = Chunk(
            id=chunk_id,
            source=self,
            content=text or None,
            images=images,
            file_paths=image_names,
            embedding_model=collections.collection_model(cast(str, self.modality)),
            item_metadata=merge_metadata(self.as_payload(), data.metadata, metadata),
        )
        return chunk

    def data_chunks(self, metadata: dict[str, Any] = {}) -> Sequence[Chunk]:
        return [self._make_chunk(data) for data in self._chunk_contents()]

    def as_payload(self) -> dict:
        return {
            "source_id": self.id,
            "tags": self.tags,
            "size": self.size,
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

    def as_payload(self) -> dict:
        return {
            **super().as_payload(),
            "message_id": self.message_id,
            "subject": self.subject,
            "sender": self.sender,
            "recipients": self.recipients,
            "folder": self.folder,
            "tags": self.tags + [self.sender] + self.recipients,
            "date": (self.sent_at and self.sent_at.isoformat() or None),  # type: ignore
        }

    @property
    def parsed_content(self):
        from memory.parsers.email import parse_email_message

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
            **super().as_payload(),
            "filename": self.filename,
            "content_type": self.mime_type,
            "size": self.size,
            "created_at": (self.created_at and self.created_at.isoformat() or None),  # type: ignore
            "mail_message_id": self.mail_message_id,
        }

    def data_chunks(self, metadata: dict[str, Any] = {}) -> Sequence[Chunk]:
        if cast(str | None, self.filename):
            contents = pathlib.Path(cast(str, self.filename)).read_bytes()
        else:
            contents = cast(str, self.content)

        chunks = extract.extract_data_chunks(cast(str, self.mime_type), contents)
        return [self._make_chunk(c, metadata) for c in chunks]

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
            **super().as_payload(),
            "title": self.title,
            "author": self.author,
            "published": self.published,
            "volume": self.volume,
            "issue": self.issue,
            "page": self.page,
            "url": self.url,
        }
        return {k: v for k, v in payload.items() if v is not None}

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        image = Image.open(pathlib.Path(cast(str, self.filename)))
        description = f"{self.title} by {self.author}"
        return [extract.DataChunk(data=[image, description])]


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
            **super().as_payload(),
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

    def as_payload(self) -> dict:
        vals = {
            **super().as_payload(),
            "title": self.book.title,
            "author": self.book.author,
            "book_id": self.book_id,
            "section_title": self.section_title,
            "section_number": self.section_number,
            "section_level": self.section_level,
            "start_page": self.start_page,
            "end_page": self.end_page,
        }
        return {k: v for k, v in vals.items() if v}

    def data_chunks(self, metadata: dict[str, Any] = {}) -> Sequence[Chunk]:
        content = cast(str, self.content.strip())
        if not content:
            return []

        if len([p for p in self.pages if p.strip()]) == 1:
            return [
                self._make_chunk(
                    extract.DataChunk(data=[content]), metadata | {"type": "page"}
                )
            ]

        summary, tags = summarizer.summarize(content)
        return [
            self._make_chunk(
                extract.DataChunk(data=[content]),
                merge_metadata(metadata, {"type": "section", "tags": tags}),
            ),
            self._make_chunk(
                extract.DataChunk(data=[summary]),
                merge_metadata(metadata, {"type": "summary", "tags": tags}),
            ),
        ]


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

    def as_payload(self) -> dict:
        published_date = cast(datetime | None, self.published)
        metadata = cast(dict | None, self.webpage_metadata) or {}

        payload = {
            **super().as_payload(),
            "url": self.url,
            "title": self.title,
            "author": self.author,
            "published": published_date and published_date.isoformat(),
            "description": self.description,
            "domain": self.domain,
            "word_count": self.word_count,
            **metadata,
        }
        return {k: v for k, v in payload.items() if v}

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        return chunk_mixed(cast(str, self.content), cast(list[str], self.images))


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

    def as_payload(self) -> dict:
        return {
            **super().as_payload(),
            "url": self.url,
            "title": self.title,
            "description": self.description,
            "authors": self.authors,
            "published_at": self.published_at,
            "slug": self.slug,
            "karma": self.karma,
            "votes": self.votes,
            "score": self.score,
            "comments": self.comments,
        }

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        return chunk_mixed(cast(str, self.content), cast(list[str], self.images))


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
