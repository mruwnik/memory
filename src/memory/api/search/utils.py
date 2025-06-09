import asyncio
import traceback
from datetime import datetime
import logging
from collections import defaultdict
from typing import Optional, TypedDict, NotRequired

from pydantic import BaseModel

from memory.common import settings
from memory.common.db.models import Chunk

logger = logging.getLogger(__name__)


class AnnotatedChunk(BaseModel):
    id: str
    score: float
    metadata: dict
    preview: Optional[str | None] = None
    search_method: str | None = None


class SourceData(BaseModel):
    """Holds source item data to avoid SQLAlchemy session issues"""

    id: int
    size: int | None
    mime_type: str | None
    filename: str | None
    content_length: int
    contents: dict | str | None
    created_at: datetime | None

    @staticmethod
    def from_chunk(chunk: Chunk) -> "SourceData":
        source = chunk.source
        display_contents = source.display_contents or {}
        return SourceData(
            id=source.id,
            size=source.size,
            mime_type=source.mime_type,
            filename=source.filename,
            content_length=len(source.content) if source.content else 0,
            contents=display_contents,
            created_at=source.inserted_at,
        )


class SearchResponse(BaseModel):
    collection: str
    results: list[dict]


class SearchResult(BaseModel):
    id: int
    size: int
    mime_type: str
    chunks: list[AnnotatedChunk]
    content: Optional[str | dict] = None
    filename: Optional[str] = None
    tags: list[str] | None = None
    metadata: dict | None = None
    created_at: datetime | None = None


class SearchFilters(TypedDict):
    subject: NotRequired[str | None]
    min_confidences: NotRequired[dict[str, float]]
    tags: NotRequired[list[str] | None]
    observation_types: NotRequired[list[str] | None]
    source_ids: NotRequired[list[int] | None]


async def with_timeout(
    call, timeout: int = 2
) -> list[tuple[SourceData, AnnotatedChunk]]:
    """
    Run a function with a timeout.

    Args:
        call: The function to run
        timeout: The timeout in seconds
    """
    try:
        return await asyncio.wait_for(call, timeout=timeout)
    except TimeoutError:
        logger.warning(f"Search timed out after {timeout}s")
        return []
    except Exception as e:
        traceback.print_exc()
        logger.error(f"Search failed: {e}")
        return []


def group_chunks(
    chunks: list[tuple[SourceData, AnnotatedChunk]], preview: bool = False
) -> list[SearchResult]:
    items = defaultdict(list)
    source_lookup = {}

    for source, chunk in chunks:
        items[source.id].append(chunk)
        source_lookup[source.id] = source

    def get_content(text: str | dict | None) -> str | dict | None:
        if preview or not text or not isinstance(text, str) or len(text) < 250:
            return text

        return text[:250] + "..."

    def make_result(source: SourceData, chunks: list[AnnotatedChunk]) -> SearchResult:
        contents = source.contents or {}
        tags = []
        if isinstance(contents, dict):
            tags = contents.pop("tags", [])
            content = contents.pop("content", None)
            print(content)
        else:
            content = contents
            contents = {}

        return SearchResult(
            id=source.id,
            size=source.size or source.content_length,
            mime_type=source.mime_type or "text/plain",
            filename=source.filename
            and source.filename.replace(
                str(settings.FILE_STORAGE_DIR).lstrip("/"), "/files"
            ),
            content=get_content(content),
            tags=tags,
            metadata=contents,
            chunks=sorted(chunks, key=lambda x: x.score, reverse=True),
            created_at=source.created_at,
        )

    return [
        make_result(source, chunks)
        for source_id, chunks in items.items()
        for source in [source_lookup[source_id]]
    ]
