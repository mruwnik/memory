"""
Search endpoints for the knowledge base API.
"""

import asyncio
import logging
from collections import defaultdict
from typing import Optional
from sqlalchemy.orm import load_only
from memory.common import extract, settings
from memory.common.db.connection import make_session
from memory.common.db.models import Chunk, SourceItem
from memory.common.collections import ALL_COLLECTIONS
from memory.api.search.embeddings import search_chunks_embeddings
from memory.api.search import scorer

if settings.ENABLE_BM25_SEARCH:
    from memory.api.search.bm25 import search_bm25_chunks

from memory.api.search.types import SearchFilters, SearchResult

logger = logging.getLogger(__name__)


async def search_chunks(
    data: list[extract.DataChunk],
    modalities: set[str] = set(),
    limit: int = 10,
    filters: SearchFilters = {},
    timeout: int = 2,
) -> list[Chunk]:
    funcs = [search_chunks_embeddings]
    if settings.ENABLE_BM25_SEARCH:
        funcs.append(search_bm25_chunks)

    all_ids = await asyncio.gather(
        *[func(data, modalities, limit, filters, timeout) for func in funcs]
    )
    all_ids = {id for ids in all_ids for id in ids}

    with make_session() as db:
        chunks = (
            db.query(Chunk)
            .options(
                load_only(
                    Chunk.id,  # type: ignore
                    Chunk.source_id,  # type: ignore
                    Chunk.content,  # type: ignore
                    Chunk.file_paths,  # type: ignore
                )
            )
            .filter(Chunk.id.in_(all_ids))
            .all()
        )
        db.expunge_all()
        return chunks


async def search_sources(
    chunks: list[Chunk], previews: Optional[bool] = False
) -> list[SearchResult]:
    by_source = defaultdict(list)
    for chunk in chunks:
        by_source[chunk.source_id].append(chunk)

    with make_session() as db:
        sources = db.query(SourceItem).filter(SourceItem.id.in_(by_source.keys())).all()
        return [
            SearchResult.from_source_item(source, by_source[source.id], previews)
            for source in sources
        ]


async def search(
    data: list[extract.DataChunk],
    previews: Optional[bool] = False,
    modalities: set[str] = set(),
    limit: int = 10,
    filters: SearchFilters = {},
    timeout: int = 20,
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
    chunks = await search_chunks(
        data,
        allowed_modalities,
        limit,
        filters,
        timeout,
    )
    if settings.ENABLE_SEARCH_SCORING:
        chunks = await scorer.rank_chunks(data[0].data[0], chunks, min_score=0.3)

    sources = await search_sources(chunks, previews)
    sources.sort(key=lambda x: x.search_score or 0, reverse=True)
    return sources
