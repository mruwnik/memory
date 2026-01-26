from collections.abc import Sequence
from datetime import datetime
import logging
from typing import Optional, cast, TypedDict, NotRequired, TYPE_CHECKING

from pydantic import BaseModel

from memory.common.db.models import Chunk, SourceItem
from memory.common import settings

if TYPE_CHECKING:
    from memory.common.access_control import AccessFilter

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
        cls, source: SourceItem, chunks: Sequence[Chunk], previews: Optional[bool] = False
    ) -> "SearchResult":
        try:
            metadata = source.display_contents or {}
            metadata.pop("content", None)
        except Exception:
            # Polymorphic subclass attributes may fail to load with deferred loading
            metadata = {"modality": source.modality}
        chunk_size = settings.DEFAULT_CHUNK_TOKENS * 4

        # Use max chunk score - we want to find documents with at least one
        # highly relevant section, not penalize long documents with some irrelevant parts.
        # This is better for "half-remembered" searches where users recall one specific detail.
        search_score = (
            max((chunk.relevance_score for chunk in chunks), default=0) if chunks else 0
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


class MCPSearchFilters(TypedDict):
    """Search filters exposed via MCP API.

    This is a subset of SearchFilters that excludes internal fields like
    access_filter and source_ids which should not be set by external callers.
    """

    # Size filters
    min_size: NotRequired[int]
    max_size: NotRequired[int]

    # Date range filters
    min_created_at: NotRequired[str]
    max_created_at: NotRequired[str]
    min_sent_at: NotRequired[str]
    max_sent_at: NotRequired[str]
    min_published: NotRequired[str]
    max_published: NotRequired[str]

    # List filters (match any)
    tags: NotRequired[list[str]]
    recipients: NotRequired[list[str]]
    authors: NotRequired[list[str]]
    observation_types: NotRequired[list[str] | None]

    # String match filters (exact match)
    folder_path: NotRequired[str]
    sender: NotRequired[str]
    domain: NotRequired[str]
    author: NotRequired[str]

    # Confidence filters
    min_confidences: NotRequired[dict[str, float]]

    # Subject filter (for observations)
    subject: NotRequired[str]

    # Person filter - only return items associated with this person
    # Items without a 'people' field in metadata are included (not filtered out)
    person_id: NotRequired[int]


class SearchFilters(MCPSearchFilters):
    """Full search filters including internal fields.

    Extends MCPSearchFilters with fields that should only be set internally:
    - source_ids: Pre-filtered list of source IDs
    - access_filter: Access control filter built from user's project memberships
    """

    # ID filters (internal - set by search pipeline)
    source_ids: NotRequired[list[int] | None]

    # Access control filter (built from user's project memberships).
    # Semantics:
    # - None: Superadmin access, no filtering applied (sees all content)
    # - AccessFilter with conditions: Only items matching at least one condition are returned.
    #   Each condition specifies a project_id and allowed sensitivity levels.
    # - AccessFilter with empty conditions: User has no project access, returns no results.
    #
    # Note: This filter requires SourceItem.project_id and SourceItem.sensitivity columns
    # to exist in the database schema. These columns are added by the access control migration.
    # Until that migration runs, access filtering will fail at runtime.
    access_filter: NotRequired["AccessFilter | None"]


class SearchConfig(BaseModel):
    limit: int = 20
    timeout: int = 20
    previews: bool = False
    useScores: bool = False

    # Optional enhancement flags (None = use global setting from env)
    useBm25: Optional[bool] = None
    useHyde: Optional[bool] = None
    useReranking: Optional[bool] = None
    useQueryAnalysis: Optional[bool] = None  # LLM-based query analysis (Haiku)

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
