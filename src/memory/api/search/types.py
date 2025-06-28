from datetime import datetime
import logging
from typing import Optional, TypedDict, NotRequired, cast

from memory.common.db.models.source_item import SourceItem
from pydantic import BaseModel

from memory.common.db.models import Chunk
from memory.common import settings

logger = logging.getLogger(__name__)


class SearchResponse(BaseModel):
    collection: str
    results: list[dict]


def elide_content(content: str, max_length: int = 100) -> str:
    if content and len(content) > max_length:
        return content[:max_length] + "..."
    return content


class SearchResult(BaseModel):
    id: int
    chunks: list[str]
    size: int | None = None
    mime_type: str | None = None
    content: Optional[str | dict] = None
    filename: Optional[str] = None
    tags: list[str] | None = None
    metadata: dict | None = None
    created_at: datetime | None = None

    @classmethod
    def from_source_item(
        cls, source: SourceItem, chunks: list[Chunk], previews: Optional[bool] = False
    ) -> "SearchResult":
        metadata = source.display_contents or {}
        metadata.pop("content", None)
        chunk_size = settings.DEFAULT_CHUNK_TOKENS * 4

        return cls(
            id=cast(int, source.id),
            size=cast(int, source.size),
            mime_type=cast(str, source.mime_type),
            chunks=[elide_content(str(chunk.content), chunk_size) for chunk in chunks],
            content=elide_content(
                cast(str, source.content),
                settings.MAX_PREVIEW_LENGTH
                if previews
                else settings.MAX_NON_PREVIEW_LENGTH,
            ),
            filename=cast(str, source.filename),
            tags=cast(list[str], source.tags),
            metadata=metadata,
            created_at=cast(datetime | None, source.inserted_at),
        )


class SearchFilters(TypedDict):
    min_size: NotRequired[int]
    max_size: NotRequired[int]
    min_confidences: NotRequired[dict[str, float]]
    observation_types: NotRequired[list[str] | None]
    source_ids: NotRequired[list[int] | None]
