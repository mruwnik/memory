"""
Database models for the knowledge base system.
"""

from __future__ import annotations

import pathlib
import re
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Annotated, Sequence, TypedDict, cast
import uuid

from PIL import Image
from sqlalchemy import (
    ARRAY,
    UUID,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    event,
    func,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.dialects.postgresql import BYTEA
from sqlalchemy import orm
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship
from sqlalchemy.types import Numeric

from memory.common import settings, tokens
import memory.common.extract as extract
import memory.common.collections as collections
import memory.common.chunker as chunker
import memory.common.summarizer as summarizer
from memory.common.db.models.base import Base
from memory.common.access_control import SensitivityLevelLiteral as SensitivityLevel

PREVIEW_MAX_LENGTH = 300


def truncate_preview(text: str | None, limit: int = PREVIEW_MAX_LENGTH) -> str | None:
    """Truncate text to a preview-friendly length, adding ellipsis if needed."""
    if not text:
        return None
    return (text[:limit] + "...") if len(text) > limit else text

if TYPE_CHECKING:
    from memory.common.db.models.journal import JournalEntry
    from memory.common.db.models.sources import Person


class MetadataSchema(TypedDict):
    type: str
    description: str


class SourceItemPayload(TypedDict):
    source_id: Annotated[int, "Unique identifier of the source item"]
    tags: Annotated[list[str], "List of tags for categorization"]
    size: Annotated[int | None, "Size of the content in bytes"]
    people: Annotated[list[int], "IDs of associated Person records"]
    project_id: Annotated[int | None, "ID of the associated project"]
    sensitivity: Annotated[SensitivityLevel, "Sensitivity level: basic, internal, or confidential"]


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
    return re.sub(r"[^a-zA-Z0-9_]", "_", filename).strip("_")[:30]


def image_filenames(chunk_id: str, images: list[Image.Image]) -> list[str]:
    for i, image in enumerate(images):
        if not getattr(image, "filename", None):  # type: ignore
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


def chunk_mixed(content: str, image_paths: Sequence[str]) -> list[extract.DataChunk]:
    if not content.strip():
        return []

    images = [Image.open(settings.FILE_STORAGE_DIR / image) for image in image_paths]

    summary, tags = summarizer.summarize(content)
    full_text: extract.DataChunk = extract.DataChunk(
        data=[content.strip(), *images], metadata={"tags": tags}
    )

    chunks: list[extract.DataChunk] = [full_text]
    if tokens.approx_token_count(content) > chunker.DEFAULT_CHUNK_TOKENS * 2:
        chunks += [
            extract.DataChunk(data=add_pics(c, images), metadata={"tags": tags})
            for c in chunker.chunk_text(content)
        ]
        chunks.append(extract.DataChunk(data=[summary], metadata={"tags": tags}))

    return [c for c in chunks if c.data]


class Chunk(Base):
    """Stores content chunks with their vector embeddings."""

    __tablename__ = "chunk"
    __allow_unmapped__ = True

    # The ID is also used as the vector ID in the vector database
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    source_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), nullable=False
    )
    file_paths: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True
    )  # Path to content if stored as a file
    content: Mapped[str | None] = mapped_column(Text)  # Direct content storage
    embedding_model: Mapped[str | None] = mapped_column(Text)
    collection_name: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    search_vector: Mapped[Any | None] = mapped_column(TSVECTOR)  # Full-text search index

    # Populated by backref from SourceItem.chunks relationship
    source: SourceItem

    # Transient attributes (not stored in DB) - initialized per-instance to avoid
    # shared mutable state across instances
    vector: list[float]
    item_metadata: dict[str, Any]
    images: list[Image.Image]
    relevance_score: float

    def __init__(self, **kwargs: Any) -> None:
        # Initialize transient mutable attributes per-instance
        self.vector = kwargs.pop("vector", [])
        self.item_metadata = kwargs.pop("item_metadata", {})
        self.images = kwargs.pop("images", [])
        self.relevance_score = kwargs.pop("relevance_score", 0.0)
        super().__init__(**kwargs)

    @orm.reconstructor
    def init_on_load(self) -> None:
        """Initialize transient attributes when loading from database.

        SQLAlchemy doesn't call __init__ when loading objects from DB,
        so transient attributes need to be initialized here.
        """
        self.vector = []
        self.item_metadata = {}
        self.images = []
        self.relevance_score = 0.0

    # One of file_path or content must be populated
    __table_args__ = (
        CheckConstraint("(file_paths IS NOT NULL) OR (content IS NOT NULL)"),
        Index("chunk_source_idx", "source_id"),
        Index("chunk_collection_idx", "collection_name"),
    )

    @property
    def chunks(self) -> list[extract.MulitmodalChunk]:
        chunks: list[extract.MulitmodalChunk] = []
        if self.content:
            chunks = [self.content]
        if self.images:
            chunks += self.images
        elif self.file_paths:
            chunks += [Image.open(pathlib.Path(cp)) for cp in self.file_paths]
        return chunks

    @property
    def data(self) -> list[bytes | str | Image.Image]:
        items: list[bytes | str | Image.Image] = []
        if self.content:
            items = [self.content]

        if not self.file_paths:
            return items

        paths = [pathlib.Path(p) for p in self.file_paths]
        files = [path for path in paths if path.exists()]

        for file_path in files:
            if file_path.suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
                if file_path.exists():
                    items.append(Image.open(file_path))
            elif file_path.suffix == ".bin":
                items.append(file_path.read_bytes())
            else:
                items.append(file_path.read_text())
        return items


class ConfidenceScore(Base):
    """
    Stores structured confidence scores for source items.
    Provides detailed confidence dimensions instead of a single score.
    """

    __tablename__ = "confidence_score"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_item.id", ondelete="CASCADE"), nullable=False
    )
    confidence_type: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # e.g., "observation_accuracy", "interpretation", "predictive_value"
    score: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)  # 0.0-1.0

    # Relationship back to source item
    source_item: Mapped[SourceItem] = relationship(
        "SourceItem", back_populates="confidence_scores"
    )

    __table_args__ = (
        Index("confidence_source_idx", "source_item_id"),
        Index("confidence_type_idx", "confidence_type"),
        Index("confidence_score_idx", "score"),
        CheckConstraint("score >= 0.0 AND score <= 1.0", name="score_range_check"),
        # Ensure each source_item can only have one score per confidence_type
        UniqueConstraint(
            "source_item_id", "confidence_type", name="unique_source_confidence_type"
        ),
    )

    def __repr__(self) -> str:
        return f"<ConfidenceScore(type={self.confidence_type}, score={self.score})>"


# Junction table for SourceItem <-> Person many-to-many relationship
# Used for associating content with people (e.g., meeting attendees, email recipients)
source_item_people = Table(
    "source_item_people",
    Base.metadata,
    Column(
        "source_item_id",
        BigInteger,
        ForeignKey("source_item.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "person_id",
        BigInteger,
        ForeignKey("people.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Index("source_item_people_source_idx", "source_item_id"),
    Index("source_item_people_person_idx", "person_id"),
)


class SourceItem(Base):
    """Base class for all content in the system using SQLAlchemy's joined table inheritance."""

    __tablename__ = "source_item"
    __allow_unmapped__ = True

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    modality: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False, unique=True)
    inserted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=func.now(),
        onupdate=func.now(),
    )
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    size: Mapped[int | None] = mapped_column(Integer)
    mime_type: Mapped[str | None] = mapped_column(Text)

    # Content is stored in the database if it's small enough and text
    content: Mapped[str | None] = mapped_column(Text)
    # Otherwise the content is stored on disk
    filename: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Chunks relationship
    embed_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="RAW")
    chunks: Mapped[list[Chunk]] = relationship(
        "Chunk", backref="source", cascade="all, delete-orphan"
    )

    # Confidence scores relationship
    confidence_scores: Mapped[list[ConfidenceScore]] = relationship(
        "ConfidenceScore", back_populates="source_item", cascade="all, delete-orphan"
    )

    # Discriminator column for SQLAlchemy inheritance
    type: Mapped[str | None] = mapped_column(String(50))

    # Orphan verification tracking
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verification_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    # Access control
    project_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    sensitivity: Mapped[str] = mapped_column(
        String(20), nullable=False, default="basic", server_default="basic"
    )
    # Creator of the content (for creator-based access control)
    creator_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Class-level defaults for access control inheritance
    # Subclasses can override (e.g., Book, BlogPost default to "public")
    default_project_id: int | None = None
    default_sensitivity: str = "basic"

    # Person associations (for filtering content by person)
    # Many-to-many relationship via source_item_people junction table
    # Using lazy="select" (default) to avoid eager loading on every query.
    # Callers needing person data should use joinedload() or selectinload() explicitly.
    people: Mapped[list["Person"]] = relationship(
        "Person",
        secondary=source_item_people,
        lazy="select",
    )

    # Journal entries (append-only notes attached to this item)
    # Uses polymorphic target: JournalEntry.target_type='source_item' + target_id=id
    journal_entries: Mapped[list["JournalEntry"]] = relationship(
        "JournalEntry",
        primaryjoin="and_(SourceItem.id == foreign(JournalEntry.target_id), "
        "JournalEntry.target_type == 'source_item')",
        cascade="all, delete-orphan",
        order_by="JournalEntry.created_at",
        viewonly=True,  # Polymorphic relationship - use helper functions to create entries
    )

    __mapper_args__: dict[str, Any] = {
        "polymorphic_on": type,
        "polymorphic_identity": "source_item",
    }

    # Add table-level constraint and indexes
    __table_args__ = (
        CheckConstraint("embed_status IN ('RAW','QUEUED','STORED','FAILED','SKIPPED')"),
        CheckConstraint(
            "verification_failures >= 0", name="verification_failures_non_negative"
        ),
        CheckConstraint(
            "sensitivity IN ('public', 'basic', 'internal', 'confidential')",
            name="valid_sensitivity_level",
        ),
        Index("source_modality_idx", "modality"),
        Index("source_status_idx", "embed_status"),
        Index("source_tags_idx", "tags", postgresql_using="gin"),
        Index("source_filename_idx", "filename"),
        Index("source_verified_at_idx", "type", "last_verified_at"),
        Index("source_project_idx", "project_id"),
        Index("source_sensitivity_idx", "sensitivity"),
        Index("source_creator_idx", "creator_id"),
    )

    @property
    def vector_ids(self):
        """Get vector IDs from associated chunks."""
        return [chunk.id for chunk in self.chunks]

    @property
    def confidence_dict(self) -> dict[str, float]:
        return {
            score.confidence_type: float(score.score)
            for score in self.confidence_scores
        }

    def update_confidences(self, confidence_updates: dict[str, float]) -> None:
        """
        Update confidence scores for this source item.
        Merges new scores with existing ones, overwriting duplicates.

        Args:
            confidence_updates: Dict mapping confidence_type to score (0.0-1.0)
        """
        if not confidence_updates:
            return

        current = {s.confidence_type: s for s in self.confidence_scores}

        for confidence_type, score in confidence_updates.items():
            if current_score := current.get(confidence_type):
                current_score.score = score  # type: ignore[assignment]
            else:
                new_score = ConfidenceScore(
                    source_item_id=self.id, confidence_type=confidence_type, score=score
                )
                self.confidence_scores.append(new_score)

    def _chunk_contents(self) -> Sequence[extract.DataChunk]:
        if self.content:
            chunks = extract.extract_text(self.content)
        else:
            chunks = []

        if self.mime_type and self.mime_type.startswith("image/"):
            chunks.append(extract.DataChunk(data=[Image.open(self.filename)]))
        return chunks

    def _make_chunk(
        self, data: extract.DataChunk, metadata: dict[str, Any] | None = None
    ) -> Chunk:
        metadata = metadata or {}
        chunk_id = str(uuid.uuid4())
        text = "\n\n".join(c for c in data.data if isinstance(c, str) and c.strip())
        images = [c for c in data.data if isinstance(c, Image.Image)]
        image_names = image_filenames(chunk_id, images)

        modality = data.modality if data.modality else self.modality
        chunk = Chunk(
            id=chunk_id,
            source=self,
            content=text or None,
            images=images,
            file_paths=image_names,
            collection_name=modality,
            embedding_model=collections.collection_model(modality, text, images),
            item_metadata=extract.merge_metadata(
                dict(self.as_payload()), data.metadata, metadata
            ),
        )
        return chunk

    def data_chunks(self, metadata: dict[str, Any] | None = None) -> Sequence[Chunk]:
        metadata = metadata or {}
        return [self._make_chunk(data, metadata) for data in self._chunk_contents()]

    def as_payload(self) -> SourceItemPayload:
        """
        Return payload dict for this item.

        Note: Accessing `self.people` triggers a lazy load if not already loaded.
        For bulk operations, callers should use `selectinload(SourceItem.people)`
        or `joinedload(SourceItem.people)` when querying to avoid N+1 queries.
        """
        # Use "basic" as fallback since SQLAlchemy defaults may not apply before flush
        sensitivity = cast(SensitivityLevel, self.sensitivity or "basic")
        return SourceItemPayload(
            source_id=self.id,
            tags=self.tags,
            size=self.size,
            people=[p.id for p in self.people],
            project_id=self.project_id,
            sensitivity=sensitivity,
        )

    @classmethod
    def get_collections(cls) -> list[str]:
        """Return the list of Qdrant collections this SourceItem type can be stored in."""
        return [cls.__tablename__]

    @property
    def popularity(self) -> float:
        """
        Return a popularity score for this item.

        Default is 1.0. Subclasses can override to provide custom popularity
        metrics (e.g., karma, view count, citations).
        """
        return 1.0

    @property
    def should_embed(self) -> bool:
        """
        Return whether this item should be embedded.

        Default is True. Subclasses can override to skip embedding for
        content that isn't suitable (e.g., very short messages, empty content).

        When False, the item's embed_status should be set to SKIPPED rather
        than attempting embedding and getting FAILED.
        """
        return True

    @property
    def preview_text(self) -> str | None:
        """Short text preview for listings. Subclasses can override."""
        return truncate_preview(self.content)

    @property
    def title(self) -> str | None:
        """
        Return a display title for this item.

        Subclasses should override to return their specific title field
        (e.g., subject for emails, title for blog posts).
        """
        return self.filename

    @property
    def display_contents(self) -> dict | None:
        payload = self.as_payload()
        payload.pop("source_id", None)  # type: ignore
        return {
            **payload,
            "tags": self.tags,
            "content": self.content,
            "filename": self.filename,
            "mime_type": self.mime_type,
        }

    def get_data_source(self) -> Any:
        """
        Get the data source for this item (e.g., EmailAccount, SlackWorkspace).

        Subclasses override to return their specific data source.
        Used for resolving inherited project_id and sensitivity.

        Returns:
            The data source object, or None if no data source.
        """
        return None

    def resolve_access_control(self) -> tuple[int | None, str]:
        """
        Resolve project_id and sensitivity from item, data source, or class defaults.

        Resolution order (first non-None wins):
        1. Item's own project_id/sensitivity
        2. Data source's project_id/sensitivity (e.g., EmailAccount, SlackChannel)
        3. Class-level defaults (default_project_id, default_sensitivity)

        Returns:
            Tuple of (resolved_project_id, resolved_sensitivity)
        """
        source = self.get_data_source()

        # Resolve project_id: item -> source -> class default
        project_id = self.project_id
        if project_id is None and source is not None:
            project_id = getattr(source, "project_id", None)
        if project_id is None:
            project_id = self.default_project_id

        # Resolve sensitivity: item -> source -> class default
        # Note: sensitivity column is NOT NULL with server_default="basic", but SQLAlchemy
        # may not apply defaults until flush. For in-memory objects before commit, sensitivity
        # could be None. We also check for empty string as an additional safety measure.
        sensitivity = self.sensitivity
        if not sensitivity and source is not None:
            sensitivity = getattr(source, "sensitivity", None)
        if not sensitivity:
            sensitivity = self.default_sensitivity

        return project_id, sensitivity
