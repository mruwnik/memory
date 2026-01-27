"""
Full-text search using PostgreSQL's built-in text search capabilities.

This replaces the previous in-memory BM25 implementation which caused OOM
with large collections (250K+ chunks).
"""

import asyncio
import logging
import re

from sqlalchemy import bindparam, func, text, or_, exists, select, false as sql_false, literal_column

from memory.api.search.types import SearchFilters
from memory.common import extract
from memory.common.db.connection import make_session
from memory.common.db.models import Chunk, ConfidenceScore, SourceItem
from memory.common.db.models.source_item import source_item_people
from memory.common.access_control import AccessFilter

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


def apply_access_filter(query, access_filter: AccessFilter | None):
    """
    Apply access control filter to a SQLAlchemy query.

    Args:
        query: SQLAlchemy query (must have SourceItem joined)
        access_filter: Access filter from user's memberships, or None for superadmin

    Returns:
        Modified query with access control filter applied

    Access is granted if ANY of these conditions are true:
    1. User has admin scope (superadmin) - access_filter will be None
    2. Person override: user's person is attached to the item via source_item_people
    3. Public bypass: item's resolved sensitivity is "public"
    4. Project access: resolved project_id matches a project the user has access to,
       AND resolved sensitivity is within the user's allowed sensitivities for that role

    Uses source_item_access_view for resolved project_id and sensitivity (query-time inheritance).
    """
    if access_filter is None:
        # Superadmin - no filtering
        return query

    conditions = []

    # Person override: if user's person is attached to item, grant access
    if access_filter.person_id is not None:
        person_override = exists(
            select(source_item_people.c.source_item_id)
            .where(source_item_people.c.source_item_id == SourceItem.id)
            .where(source_item_people.c.person_id == access_filter.person_id)
        )
        conditions.append(person_override)

    # Public bypass: items with resolved sensitivity "public" are visible to all authenticated users
    if access_filter.include_public:
        # Use the view for resolved sensitivity (handles inheritance)
        # Join to the view and check resolved_sensitivity
        public_condition = exists(
            select(literal_column("1"))
            .select_from(text("source_item_access_view av"))
            .where(text("av.id = source_item.id"))
            .where(text("av.resolved_sensitivity = 'public'"))
        )
        conditions.append(public_condition)

    # Project access conditions using resolved values from view
    for i, condition in enumerate(access_filter.conditions):
        # Build the condition using the access view for resolved values
        # This handles inheritance: item -> data source -> class default
        # Use parameterized queries to prevent SQL injection
        project_param = bindparam(f"project_id_{i}", value=condition.project_id)
        sensitivity_params = [
            bindparam(f"sens_{i}_{j}", value=s)
            for j, s in enumerate(condition.sensitivities)
        ]
        sensitivity_placeholders = ", ".join(f":sens_{i}_{j}" for j in range(len(condition.sensitivities)))
        project_condition = exists(
            select(literal_column("1"))
            .select_from(text("source_item_access_view av"))
            .where(text("av.id = source_item.id"))
            .where(text(f"av.resolved_project_id = :project_id_{i}").bindparams(project_param))
            .where(text(f"av.resolved_sensitivity IN ({sensitivity_placeholders})").bindparams(*sensitivity_params))
        )
        conditions.append(project_condition)

    if not conditions:
        # No project access AND no person override AND no public bypass = match nothing
        return query.filter(sql_false())

    # Apply OR across all access conditions
    return query.filter(or_(*conditions))


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

        # Join with SourceItem if we need size filters, access control, or person filter
        access_filter = filters.get("access_filter")
        person_id = filters.get("person_id")
        needs_source_join = (
            any(filters.get(k) for k in ["min_size", "max_size"])
            or access_filter is not None
            or person_id is not None
        )

        # Date filters on Chunk.created_at
        if min_created_at := filters.get("min_created_at"):
            items_query = items_query.filter(Chunk.created_at >= min_created_at)
        if max_created_at := filters.get("max_created_at"):
            items_query = items_query.filter(Chunk.created_at <= max_created_at)
        if needs_source_join:
            items_query = items_query.join(
                SourceItem, SourceItem.id == Chunk.source_id
            )

        # Apply access control filter (requires source join)
        if access_filter is not None:
            items_query = apply_access_filter(items_query, access_filter)

        # Apply person filter (requires source join)
        # Include items where: no people associations exist OR person is associated
        #
        # This filtering logic now matches the Qdrant person filter in embeddings.py:
        # both filter by person associations via the source_item_people junction table.
        # Items without any person associations are always included (not filtered out).
        if person_id is not None:
            person_associated = exists(
                select(source_item_people.c.source_item_id)
                .where(source_item_people.c.source_item_id == SourceItem.id)
                .where(source_item_people.c.person_id == person_id)
            )
            no_people = ~exists(
                select(source_item_people.c.source_item_id)
                .where(source_item_people.c.source_item_id == SourceItem.id)
            )
            items_query = items_query.filter(or_(no_people, person_associated))

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
    timeout: float = 10,
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
