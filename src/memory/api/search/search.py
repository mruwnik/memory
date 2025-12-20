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

if settings.ENABLE_RERANKING:
    from memory.api.search.rerank import rerank_chunks

from memory.api.search.types import SearchConfig, SearchFilters, SearchResult

logger = logging.getLogger(__name__)

# Reciprocal Rank Fusion constant (k parameter)
# Higher values reduce the influence of top-ranked documents
# 60 is the standard value from the original RRF paper
RRF_K = 60

# Multiplier for internal search limit before fusion
# We search for more candidates than requested, fuse scores, then return top N
# This helps find results that rank well in one method but not the other
CANDIDATE_MULTIPLIER = 5

# How many candidates to pass to reranker (multiplier of final limit)
# Higher = more accurate but slower and more expensive
RERANK_CANDIDATE_MULTIPLIER = 3

# Bonus for chunks containing query terms (added to RRF score)
QUERY_TERM_BOOST = 0.005

# Bonus when query terms match the source title (stronger signal)
TITLE_MATCH_BOOST = 0.01

# Bonus multiplier for popularity (applied as: score * (1 + POPULARITY_BOOST * (popularity - 1)))
# This gives a small boost to popular items without dominating relevance
POPULARITY_BOOST = 0.02

# Common words to ignore when checking for query term presence
STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "shall", "can", "need", "dare",
    "ought", "used", "to", "of", "in", "for", "on", "with", "at", "by",
    "from", "as", "into", "through", "during", "before", "after", "above",
    "below", "between", "under", "again", "further", "then", "once", "here",
    "there", "when", "where", "why", "how", "all", "each", "few", "more",
    "most", "other", "some", "such", "no", "nor", "not", "only", "own",
    "same", "so", "than", "too", "very", "just", "and", "but", "if", "or",
    "because", "until", "while", "although", "though", "after", "before",
    "what", "which", "who", "whom", "this", "that", "these", "those", "i",
    "me", "my", "myself", "we", "our", "ours", "ourselves", "you", "your",
    "yours", "yourself", "yourselves", "he", "him", "his", "himself", "she",
    "her", "hers", "herself", "it", "its", "itself", "they", "them", "their",
    "theirs", "themselves", "about", "get", "got", "getting", "like", "also",
}


def extract_query_terms(query: str) -> set[str]:
    """Extract meaningful terms from query, filtering stopwords."""
    words = query.lower().split()
    return {w for w in words if w not in STOPWORDS and len(w) > 2}


def apply_query_term_boost(
    chunks: list[Chunk],
    query_terms: set[str],
) -> None:
    """
    Boost chunk scores when query terms appear in content.

    This helps surface chunks that contain exact query words even if
    embedding similarity is lower.
    """
    if not query_terms:
        return

    for chunk in chunks:
        content = (chunk.content or "").lower()
        matches = sum(1 for term in query_terms if term in content)
        if matches > 0:
            # Boost proportional to fraction of query terms matched
            boost = QUERY_TERM_BOOST * (matches / len(query_terms))
            chunk.relevance_score = (chunk.relevance_score or 0) + boost


def deduplicate_by_source(chunks: list[Chunk]) -> list[Chunk]:
    """
    Keep only the highest-scoring chunk per source.

    This prevents multiple chunks from the same article from crowding out
    other potentially relevant sources.
    """
    best_by_source: dict[int, Chunk] = {}
    for chunk in chunks:
        source_id = chunk.source_id
        if source_id not in best_by_source:
            best_by_source[source_id] = chunk
        elif (chunk.relevance_score or 0) > (best_by_source[source_id].relevance_score or 0):
            best_by_source[source_id] = chunk
    return list(best_by_source.values())


def apply_title_boost(
    chunks: list[Chunk],
    query_terms: set[str],
) -> None:
    """
    Boost chunks when query terms match the source title.

    Title matches are a strong signal since titles summarize content.
    """
    if not query_terms or not chunks:
        return

    # Get unique source IDs
    source_ids = list({chunk.source_id for chunk in chunks})

    # Fetch full source items (polymorphic) to access title attribute
    with make_session() as db:
        sources = db.query(SourceItem).filter(
            SourceItem.id.in_(source_ids)
        ).all()
        titles = {s.id: (getattr(s, 'title', None) or "").lower() for s in sources}

    # Apply boost to chunks whose source title matches query terms
    for chunk in chunks:
        title = titles.get(chunk.source_id, "")
        if not title:
            continue

        matches = sum(1 for term in query_terms if term in title)
        if matches > 0:
            boost = TITLE_MATCH_BOOST * (matches / len(query_terms))
            chunk.relevance_score = (chunk.relevance_score or 0) + boost


def apply_popularity_boost(chunks: list[Chunk]) -> None:
    """
    Boost chunks based on source popularity.

    Uses the popularity property from SourceItem subclasses.
    ForumPost uses karma, others default to 1.0.
    """
    if not chunks:
        return

    source_ids = list({chunk.source_id for chunk in chunks})

    with make_session() as db:
        sources = db.query(SourceItem).filter(
            SourceItem.id.in_(source_ids)
        ).all()
        popularity_map = {s.id: s.popularity for s in sources}

    for chunk in chunks:
        popularity = popularity_map.get(chunk.source_id, 1.0)
        if popularity != 1.0:
            # Apply boost: score * (1 + POPULARITY_BOOST * (popularity - 1))
            # For popularity=2.0: multiplier = 1.02
            # For popularity=0.5: multiplier = 0.99
            multiplier = 1.0 + POPULARITY_BOOST * (popularity - 1.0)
            chunk.relevance_score = (chunk.relevance_score or 0) * multiplier


def fuse_scores_rrf(
    embedding_scores: dict[str, float],
    bm25_scores: dict[str, float],
) -> dict[str, float]:
    """
    Fuse embedding and BM25 scores using Reciprocal Rank Fusion (RRF).

    RRF is more robust than weighted score combination because it uses ranks
    rather than raw scores, making it insensitive to score scale differences.

    Formula: score(d) = Σ 1/(k + rank_i(d))

    Args:
        embedding_scores: Dict mapping chunk IDs to embedding similarity scores
        bm25_scores: Dict mapping chunk IDs to BM25 scores

    Returns:
        Dict mapping chunk IDs to RRF scores
    """
    # Convert scores to ranks (1-indexed)
    emb_ranked = sorted(embedding_scores.keys(), key=lambda x: embedding_scores[x], reverse=True)
    bm25_ranked = sorted(bm25_scores.keys(), key=lambda x: bm25_scores[x], reverse=True)

    emb_ranks = {chunk_id: rank + 1 for rank, chunk_id in enumerate(emb_ranked)}
    bm25_ranks = {chunk_id: rank + 1 for rank, chunk_id in enumerate(bm25_ranked)}

    # Compute RRF scores
    all_ids = set(embedding_scores.keys()) | set(bm25_scores.keys())
    fused: dict[str, float] = {}

    for chunk_id in all_ids:
        rrf_score = 0.0

        if chunk_id in emb_ranks:
            rrf_score += 1.0 / (RRF_K + emb_ranks[chunk_id])

        if chunk_id in bm25_ranks:
            rrf_score += 1.0 / (RRF_K + bm25_ranks[chunk_id])

        fused[chunk_id] = rrf_score

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

    # Fuse scores from both methods using Reciprocal Rank Fusion
    fused_scores = fuse_scores_rrf(embedding_scores, bm25_scores)

    if not fused_scores:
        return []

    # Sort by score and take top results
    # If reranking is enabled, fetch more candidates for the reranker to work with
    sorted_ids = sorted(fused_scores.keys(), key=lambda x: fused_scores[x], reverse=True)
    if settings.ENABLE_RERANKING:
        fetch_limit = limit * RERANK_CANDIDATE_MULTIPLIER
    else:
        fetch_limit = limit
    top_ids = sorted_ids[:fetch_limit]

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

    # Extract query text for boosting and reranking
    query_text = " ".join(
        c for chunk in data for c in chunk.data if isinstance(c, str)
    )

    # Apply query term presence boost and title boost
    if chunks and query_text.strip():
        query_terms = extract_query_terms(query_text)
        apply_query_term_boost(chunks, query_terms)
        apply_title_boost(chunks, query_terms)

    # Apply popularity boost (karma-based for forum posts)
    if chunks:
        apply_popularity_boost(chunks)

    # Rerank using cross-encoder for better precision
    if settings.ENABLE_RERANKING and chunks and query_text.strip():
        try:
            chunks = await rerank_chunks(
                query_text, chunks, model=settings.RERANK_MODEL, top_k=limit
            )
        except Exception as e:
            logger.warning(f"Reranking failed, using RRF order: {e}")

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
