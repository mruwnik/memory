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

if settings.ENABLE_HYDE_EXPANSION:
    from memory.api.search.hyde import expand_query_hyde

from memory.api.search.types import SearchConfig, SearchFilters, SearchResult

logger = logging.getLogger(__name__)

# Weight for embedding scores vs BM25 scores in hybrid fusion
# Higher values favor semantic similarity over keyword matching
EMBEDDING_WEIGHT = 0.7
BM25_WEIGHT = 0.3

# Bonus for results that appear in both embedding and BM25 search
# This rewards documents that match both semantically and lexically
HYBRID_BONUS = 0.15

# Multiplier for internal search limit before fusion
# We search for more candidates than requested, fuse scores, then return top N
# This helps find results that rank well in one method but not the other
CANDIDATE_MULTIPLIER = 5


def fuse_scores(
    embedding_scores: dict[str, float],
    bm25_scores: dict[str, float],
) -> dict[str, float]:
    """
    Fuse embedding and BM25 scores using weighted combination with hybrid bonus.

    Documents appearing in both search results get a bonus, as matching both
    semantic similarity AND keyword relevance is a strong signal.

    Args:
        embedding_scores: Dict mapping chunk IDs to embedding similarity scores (0-1)
        bm25_scores: Dict mapping chunk IDs to normalized BM25 scores (0-1)

    Returns:
        Dict mapping chunk IDs to fused scores (0-1 range)
    """
    all_ids = set(embedding_scores.keys()) | set(bm25_scores.keys())
    fused: dict[str, float] = {}

    for chunk_id in all_ids:
        emb_score = embedding_scores.get(chunk_id, 0.0)
        bm25_score = bm25_scores.get(chunk_id, 0.0)

        # Check if result appears in both methods
        in_both = chunk_id in embedding_scores and chunk_id in bm25_scores

        # Weighted combination
        combined = (EMBEDDING_WEIGHT * emb_score) + (BM25_WEIGHT * bm25_score)

        # Add bonus for appearing in both (strong relevance signal)
        if in_both:
            combined = min(1.0, combined + HYBRID_BONUS)

        fused[chunk_id] = combined

    return fused


async def search_chunks(
    data: list[extract.DataChunk],
    modalities: set[str] = set(),
    limit: int = 10,
    filters: SearchFilters = {},
    timeout: int = 2,
) -> list[Chunk]:
    """
    Search chunks using embedding similarity and optionally BM25.

    Combines results using weighted score fusion, giving bonus to documents
    that match both semantically and lexically.

    If HyDE is enabled, also generates a hypothetical document from the query
    and includes it in the embedding search for better semantic matching.
    """
    # Search for more candidates than requested, fuse scores, then return top N
    # This helps find results that rank well in one method but not the other
    internal_limit = limit * CANDIDATE_MULTIPLIER

    # Extract query text for HyDE expansion
    search_data = list(data)  # Copy to avoid modifying original
    if settings.ENABLE_HYDE_EXPANSION:
        query_text = " ".join(
            c for chunk in data for c in chunk.data if isinstance(c, str)
        )
        # Only expand queries with 4+ words (short queries are usually specific enough)
        if len(query_text.split()) >= 4:
            try:
                hyde_doc = await expand_query_hyde(
                    query_text, timeout=settings.HYDE_TIMEOUT
                )
                if hyde_doc:
                    logger.debug(f"HyDE expansion: '{query_text[:30]}...' -> '{hyde_doc[:50]}...'")
                    search_data.append(extract.DataChunk(data=[hyde_doc]))
            except Exception as e:
                logger.warning(f"HyDE expansion failed, using original query: {e}")

    # Run embedding search
    embedding_scores = await search_chunks_embeddings(
        search_data, modalities, internal_limit, filters, timeout
    )

    # Run BM25 search if enabled
    bm25_scores: dict[str, float] = {}
    if settings.ENABLE_BM25_SEARCH:
        try:
            bm25_scores = await search_bm25_chunks(
                data, modalities, internal_limit, filters, timeout
            )
        except asyncio.TimeoutError:
            logger.warning("BM25 search timed out, using embedding results only")

    # Fuse scores from both methods
    fused_scores = fuse_scores(embedding_scores, bm25_scores)

    if not fused_scores:
        return []

    # Sort by score and take top results
    sorted_ids = sorted(fused_scores.keys(), key=lambda x: fused_scores[x], reverse=True)
    top_ids = sorted_ids[:limit]

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
            .filter(Chunk.id.in_(top_ids))
            .all()
        )

        # Set relevance_score on each chunk from the fused scores
        for chunk in chunks:
            chunk.relevance_score = fused_scores.get(str(chunk.id), 0.0)

        db.expunge_all()
        return chunks


async def search_sources(
    chunks: list[Chunk], previews: bool = False
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
    modalities: set[str] = set(),
    filters: SearchFilters = {},
    config: SearchConfig = SearchConfig(),
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
        config.limit,
        filters,
        config.timeout,
    )
    if settings.ENABLE_SEARCH_SCORING and config.useScores:
        chunks = await scorer.rank_chunks(data[0].data[0], chunks, min_score=0.3)

    sources = await search_sources(chunks, config.previews)
    sources.sort(key=lambda x: x.search_score or 0, reverse=True)
    return sources[: config.limit]
