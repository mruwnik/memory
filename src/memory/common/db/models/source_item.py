"""
Database models for the knowledge base system.
"""

import pathlib
import re
from typing import Any, Sequence, cast
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
    Text,
    event,
    func,
)
from sqlalchemy.dialects.postgresql import BYTEA
from sqlalchemy.orm import Session, relationship

from memory.common import settings
import memory.common.extract as extract
import memory.common.collections as collections
import memory.common.chunker as chunker
import memory.common.summarizer as summarizer
from memory.common.db.models.base import Base


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
    collection_name = Column(Text)
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
            collection_name=data.collection_name or cast(str, self.modality),
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
