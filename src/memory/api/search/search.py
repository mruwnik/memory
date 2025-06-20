"""
Search endpoints for the knowledge base API.
"""

import asyncio
import logging
from typing import Optional

from memory.api.search.embeddings import search_embeddings
from memory.api.search.bm25 import search_bm25
from memory.api.search.utils import SearchFilters, SearchResult

from memory.api.search.utils import group_chunks, with_timeout
from memory.common import extract
from memory.common.collections import (
    ALL_COLLECTIONS,
    MULTIMODAL_COLLECTIONS,
    TEXT_COLLECTIONS,
)
from memory.common import settings

logger = logging.getLogger(__name__)


async def search(
    data: list[extract.DataChunk],
    previews: Optional[bool] = False,
    modalities: set[str] = set(),
    limit: int = 10,
    min_text_score: float = 0.4,
    min_multimodal_score: float = 0.25,
    filters: SearchFilters = {},
    timeout: int = 2,
) -> list[SearchResult]:
    """
    Search across knowledge base using text query and optional files.

    Parameters:
    - query: Optional text search query
    - modalities: List of modalities to search in (e.g., "text", "photo", "doc")
    - files: Optional files to include in the search context
    - limit: Maximum number of results per modality

    Returns:
    - List of search results sorted by score
    """
    allowed_modalities = modalities & ALL_COLLECTIONS.keys()

    searches = []
    if settings.ENABLE_EMBEDDING_SEARCH:
        searches = [
            with_timeout(
                search_embeddings(
                    data,
                    previews,
                    allowed_modalities & TEXT_COLLECTIONS,
                    limit,
                    min_text_score,
                    filters,
                    multimodal=False,
                ),
                timeout,
            ),
            with_timeout(
                search_embeddings(
                    data,
                    previews,
                    allowed_modalities & MULTIMODAL_COLLECTIONS,
                    limit,
                    min_multimodal_score,
                    filters,
                    multimodal=True,
                ),
                timeout,
            ),
        ]
    if settings.ENABLE_BM25_SEARCH:
        searches.append(
            with_timeout(
                search_bm25(
                    " ".join(
                        [c for chunk in data for c in chunk.data if isinstance(c, str)]
                    ),
                    modalities,
                    limit=limit,
                    filters=filters,
                ),
                timeout,
            )
        )

    search_results = await asyncio.gather(*searches, return_exceptions=False)
    all_results = []
    for results in search_results:
        if len(all_results) >= limit:
            break
        all_results.extend(results)

    results = group_chunks(all_results, previews or False)
    return sorted(results, key=lambda x: max(c.score for c in x.chunks), reverse=True)
