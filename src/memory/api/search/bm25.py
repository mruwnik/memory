"""
Full-text search using PostgreSQL's built-in text search capabilities.

This replaces the previous in-memory BM25 implementation which caused OOM
with large collections (250K+ chunks).
"""

import asyncio
import logging
import re

from sqlalchemy import func, text

from memory.api.search.types import SearchFilters
from memory.common import extract
from memory.common.db.connection import make_session
from memory.common.db.models import Chunk, ConfidenceScore, SourceItem

logger = logging.getLogger(__name__)

# Pattern to remove special characters that confuse tsquery
_TSQUERY_SPECIAL_CHARS = re.compile(r"[&|!():*<>'\"-]")

# Common English stopwords to filter from queries
# These are words that appear in most documents and don't help with search relevance
_STOPWORDS = frozenset([
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "been",
    "be", "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "shall", "can", "need", "dare", "ought",
    "used", "it", "its", "this", "that", "these", "those", "i", "you", "he",
    "she", "we", "they", "what", "which", "who", "whom", "whose", "where",
    "when", "why", "how", "all", "each", "every", "both", "few", "more",
    "most", "other", "some", "such", "no", "nor", "not", "only", "own",
    "same", "so", "than", "too", "very", "just", "about", "into", "through",
    "during", "before", "after", "above", "below", "between", "under", "again",
    "further", "then", "once", "here", "there", "any", "being", "doing",
])


def build_tsquery(query: str) -> str:
    """
    Convert a natural language query to a PostgreSQL tsquery.

    Uses AND matching for multi-word queries to ensure all terms appear.
    Also adds prefix matching with :* for partial word matches.
    Filters out common stopwords that don't help with search relevance.
    """
    # Remove special characters that confuse tsquery
    clean_query = _TSQUERY_SPECIAL_CHARS.sub(" ", query)

    # Split query into words, filter stopwords and short words
    words = [
        w.strip().lower()
        for w in clean_query.split()
        if w.strip() and len(w.strip()) >= 2 and w.strip().lower() not in _STOPWORDS
    ]
    if not words:
        return ""

    # Join words with & for AND matching (all terms must appear)
    # Add :* for prefix matching to catch word variants
    tsquery_parts = [f"{word}:*" for word in words]
    return " & ".join(tsquery_parts)


async def search_bm25(
    query: str,
    modalities: set[str],
    limit: int = 10,
    filters: SearchFilters = SearchFilters(),
) -> dict[str, float]:
    """
    Search chunks using PostgreSQL full-text search.

    Uses ts_rank for relevance scoring, normalized to 0-1 range.

    Returns:
    - Dictionary mapping chunk IDs to their normalized scores (0-1 range)
    """
    tsquery = build_tsquery(query)
    if not tsquery:
        return {}

    with make_session() as db:
        # Build the base query with full-text search
        # ts_rank returns a relevance score based on term frequency
        rank_expr = func.ts_rank(
            Chunk.search_vector,
            func.to_tsquery("english", tsquery),
        )

        items_query = db.query(
            Chunk.id,
            rank_expr.label("rank"),
        ).filter(
            Chunk.collection_name.in_(modalities),
            Chunk.search_vector.isnot(None),
            Chunk.search_vector.op("@@")(func.to_tsquery("english", tsquery)),
        )

        # Join with SourceItem if we need size filters
        needs_source_join = any(filters.get(k) for k in ["min_size", "max_size"])
        if needs_source_join:
            items_query = items_query.join(
                SourceItem, SourceItem.id == Chunk.source_id
            )

        if source_ids := filters.get("source_ids"):
            items_query = items_query.filter(Chunk.source_id.in_(source_ids))

        # Size filters
        if min_size := filters.get("min_size"):
            items_query = items_query.filter(SourceItem.size >= min_size)
        if max_size := filters.get("max_size"):
            items_query = items_query.filter(SourceItem.size <= max_size)

        # Observation type filter - restricts to specific collection types
        if observation_types := filters.get("observation_types"):
            items_query = items_query.filter(
                Chunk.collection_name.in_(observation_types)
            )

        # Add confidence filtering if specified
        if min_confidences := filters.get("min_confidences"):
            for confidence_type, min_score in min_confidences.items():
                items_query = items_query.join(
                    ConfidenceScore,
                    (ConfidenceScore.source_item_id == Chunk.source_id)
                    & (ConfidenceScore.confidence_type == confidence_type)
                    & (ConfidenceScore.score >= min_score),
                )

        # Order by rank descending and limit results
        items_query = items_query.order_by(text("rank DESC")).limit(limit)

        items = items_query.all()
        if not items:
            return {}

        # Collect raw scores
        raw_scores = {str(item.id): float(item.rank) for item in items if item.rank > 0}

        if not raw_scores:
            return {}

        # Normalize scores to 0-1 range using min-max normalization
        # This makes them comparable to embedding cosine similarity scores
        min_score = min(raw_scores.values())
        max_score = max(raw_scores.values())
        score_range = max_score - min_score

        if score_range > 0:
            return {
                chunk_id: (score - min_score) / score_range
                for chunk_id, score in raw_scores.items()
            }
        else:
            # All scores are equal, return 0.5 for all
            return {chunk_id: 0.5 for chunk_id in raw_scores}


async def search_bm25_chunks(
    data: list[extract.DataChunk],
    modalities: set[str] = set(),
    limit: int = 10,
    filters: SearchFilters = SearchFilters(),
    timeout: int = 10,
) -> dict[str, float]:
    """
    Search chunks using PostgreSQL full-text search.

    Runs separate searches for each data chunk and merges results,
    similar to how embedding search handles multiple query variants.

    Returns:
    - Dictionary mapping chunk IDs to their normalized scores (0-1 range)
    """
    # Extract query strings from each data chunk
    queries = [
        " ".join(c for c in chunk.data if isinstance(c, str))
        for chunk in data
    ]
    queries = [q.strip() for q in queries if q.strip()]

    if not queries:
        return {}

    # Run separate searches for each query in parallel
    async def run_search(query: str) -> dict[str, float]:
        return await search_bm25(query, modalities, limit, filters)

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*[run_search(q) for q in queries], return_exceptions=True),
            timeout,
        )
    except asyncio.TimeoutError:
        return {}

    # Merge results - take max score for each chunk across all queries
    merged: dict[str, float] = {}
    for result in results:
        if isinstance(result, BaseException):
            continue
        for chunk_id, score in result.items():
            if chunk_id not in merged or score > merged[chunk_id]:
                merged[chunk_id] = score

    return merged
