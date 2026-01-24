"""
Database models for the knowledge base system.
"""

from __future__ import annotations

import pathlib
import re
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Annotated, Sequence, TypedDict
import uuid

from PIL import Image
from sqlalchemy import (
    ARRAY,
    UUID,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    event,
    func,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.dialects.postgresql import BYTEA
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship
from sqlalchemy.types import Numeric

from memory.common import settings, tokens
import memory.common.extract as extract
import memory.common.collections as collections
import memory.common.chunker as chunker
import memory.common.summarizer as summarizer
from memory.common.db.models.base import Base

if TYPE_CHECKING:
    pass


class MetadataSchema(TypedDict):
    type: str
    description: str


class SourceItemPayload(TypedDict):
    source_id: Annotated[int, "Unique identifier of the source item"]
    tags: Annotated[list[str], "List of tags for categorization"]
    size: Annotated[int | None, "Size of the content in bytes"]


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

    vector: list[float] = []
    item_metadata: dict[str, Any] = {}
    images: list[Image.Image] = []
    relevance_score: float = 0.0

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
        BigInteger, ForeignKey("github_milestones.id", ondelete="SET NULL"), nullable=True
    )
    sensitivity: Mapped[str] = mapped_column(String(20), nullable=False, server_default="basic")

    # Person associations (for filtering content by person)
    people: Mapped[list[int] | None] = mapped_column(ARRAY(BigInteger), nullable=True)

    __mapper_args__: dict[str, Any] = {
        "polymorphic_on": type,
        "polymorphic_identity": "source_item",
    }

    # Add table-level constraint and indexes
    __table_args__ = (
        CheckConstraint("embed_status IN ('RAW','QUEUED','STORED','FAILED')"),
        CheckConstraint(
            "verification_failures >= 0", name="verification_failures_non_negative"
        ),
        CheckConstraint(
            "sensitivity IN ('basic', 'internal', 'confidential')",
            name="valid_sensitivity_level",
        ),
        Index("source_modality_idx", "modality"),
        Index("source_status_idx", "embed_status"),
        Index("source_tags_idx", "tags", postgresql_using="gin"),
        Index("source_filename_idx", "filename"),
        Index("source_verified_at_idx", "type", "last_verified_at"),
        Index("source_project_idx", "project_id"),
        Index("source_sensitivity_idx", "sensitivity"),
        Index("source_people_idx", "people", postgresql_using="gin"),
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
        self, data: extract.DataChunk, metadata: dict[str, Any] = {}
    ) -> Chunk:
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

    def data_chunks(self, metadata: dict[str, Any] = {}) -> Sequence[Chunk]:
        return [self._make_chunk(data, metadata) for data in self._chunk_contents()]

    def as_payload(self) -> SourceItemPayload:
        return SourceItemPayload(
            source_id=self.id,
            tags=self.tags,
            size=self.size,
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
