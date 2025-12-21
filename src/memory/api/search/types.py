from datetime import datetime
import logging
from typing import Optional, TypedDict, NotRequired, cast

from pydantic import BaseModel

from memory.common.db.models import Chunk, SourceItem
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
    search_score: float | None = None

    @classmethod
    def from_source_item(
        cls, source: SourceItem, chunks: list[Chunk], previews: Optional[bool] = False
    ) -> "SearchResult":
        metadata = source.display_contents or {}
        metadata.pop("content", None)
        chunk_size = settings.DEFAULT_CHUNK_TOKENS * 4

        # Use max chunk score - we want to find documents with at least one
        # highly relevant section, not penalize long documents with some irrelevant parts.
        # This is better for "half-remembered" searches where users recall one specific detail.
        search_score = (
            max((chunk.relevance_score for chunk in chunks), default=0)
            if chunks
            else 0
        )

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
            search_score=search_score,
        )


class SearchFilters(TypedDict):
    min_size: NotRequired[int]
    max_size: NotRequired[int]
    min_confidences: NotRequired[dict[str, float]]
    observation_types: NotRequired[list[str] | None]
    source_ids: NotRequired[list[int] | None]


class SearchConfig(BaseModel):
    limit: int = 20
    timeout: int = 20
    previews: bool = False
    useScores: bool = False

    # Optional enhancement flags (None = use global setting from env)
    useBm25: Optional[bool] = None
    useHyde: Optional[bool] = None
    useReranking: Optional[bool] = None
    useQueryExpansion: Optional[bool] = None

    def model_post_init(self, __context) -> None:
        # Enforce reasonable limits
        if self.limit < 1:
            object.__setattr__(self, "limit", 1)
        elif self.limit > 1000:
            object.__setattr__(self, "limit", 1000)

        if self.timeout < 1:
            object.__setattr__(self, "timeout", 1)
        elif self.timeout > 300:
            object.__setattr__(self, "timeout", 300)
