"""
Search endpoints for the knowledge base API.
"""

import asyncio
import logging
import math
from collections import defaultdict
from datetime import datetime, timezone
from memory.common import extract, settings
from memory.common.db.connection import make_session
from memory.common.db.models import Chunk, SourceItem
from memory.common.collections import ALL_COLLECTIONS
from memory.api.search.embeddings import search_chunks_embeddings
from memory.api.search import scorer
from memory.api.search.constants import (
    RRF_K,
    CANDIDATE_MULTIPLIER,
    RERANK_CANDIDATE_MULTIPLIER,
    QUERY_TERM_BOOST,
    TITLE_MATCH_BOOST,
    POPULARITY_BOOST,
    RECENCY_BOOST_MAX,
    RECENCY_HALF_LIFE_DAYS,
    STOPWORDS,
)

if settings.ENABLE_BM25_SEARCH:
    from memory.api.search.bm25 import search_bm25_chunks

if settings.ENABLE_HYDE_EXPANSION:
    from memory.api.search.hyde import expand_query_hyde

if settings.ENABLE_RERANKING:
    from memory.api.search.rerank import rerank_chunks

from memory.api.search.query_analysis import analyze_query, QueryAnalysis
from memory.api.search.types import SearchConfig, SearchFilters, SearchResult

# Default config for when none is provided
_DEFAULT_CONFIG = SearchConfig()

logger = logging.getLogger(__name__)


def extract_query_terms(query: str) -> set[str]:
    """Extract meaningful terms from query, filtering stopwords."""
    words = query.lower().split()
    return {w for w in words if w not in STOPWORDS and len(w) >= 2}


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
        elif (chunk.relevance_score or 0) > (
            best_by_source[source_id].relevance_score or 0
        ):
            best_by_source[source_id] = chunk
    return list(best_by_source.values())


def apply_source_boosts(
    chunks: list[Chunk],
    query_terms: set[str],
) -> None:
    """
    Apply title, popularity, and recency boosts to chunks in a single DB query.

    - Title boost: chunks get boosted when query terms appear in source title
    - Popularity boost: chunks get boosted based on source karma/popularity
    - Recency boost: newer content gets a small boost that decays over time
    """
    if not chunks:
        return

    source_ids = list({chunk.source_id for chunk in chunks})
    now = datetime.now(timezone.utc)

    # Single query to fetch all source metadata
    with make_session() as db:
        sources = db.query(SourceItem).filter(SourceItem.id.in_(source_ids)).all()
        source_map = {
            s.id: {
                "title": (getattr(s, "title", None) or "").lower(),
                "popularity": s.popularity,
                "inserted_at": s.inserted_at,
            }
            for s in sources
        }

    for chunk in chunks:
        source_data = source_map.get(chunk.source_id, {})
        score = chunk.relevance_score or 0

        # Apply title boost if query terms match
        if query_terms:
            title = source_data.get("title", "")
            if title:
                matches = sum(1 for term in query_terms if term in title)
                if matches > 0:
                    score += TITLE_MATCH_BOOST * (matches / len(query_terms))

        # Apply popularity boost
        popularity = source_data.get("popularity", 1.0)
        if popularity != 1.0:
            multiplier = 1.0 + POPULARITY_BOOST * (popularity - 1.0)
            score *= multiplier

        # Apply recency boost (exponential decay with half-life)
        inserted_at = source_data.get("inserted_at")
        if inserted_at:
            # Handle timezone-naive timestamps
            if inserted_at.tzinfo is None:
                inserted_at = inserted_at.replace(tzinfo=timezone.utc)
            age_days = (now - inserted_at).total_seconds() / 86400
            # Exponential decay: boost = max_boost * 0.5^(age/half_life)
            decay = math.pow(0.5, age_days / RECENCY_HALF_LIFE_DAYS)
            score += RECENCY_BOOST_MAX * decay

        chunk.relevance_score = score


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
    emb_ranked = sorted(
        embedding_scores.keys(), key=lambda x: embedding_scores[x], reverse=True
    )
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


async def _run_llm_analysis(
    query_text: str,
    use_query_analysis: bool,
    use_hyde: bool,
) -> tuple[QueryAnalysis | None, str | None]:
    """
    Run LLM-based query analysis and/or HyDE expansion in parallel.

    Returns:
        (analysis_result, hyde_doc) tuple
    """
    analysis_result: QueryAnalysis | None = None
    hyde_doc: str | None = None

    if not (use_query_analysis or use_hyde):
        return analysis_result, hyde_doc

    tasks = []

    if use_query_analysis:
        tasks.append(("analysis", analyze_query(query_text, timeout=3.0)))

    if use_hyde and len(query_text.split()) >= 4:
        tasks.append(
            ("hyde", expand_query_hyde(query_text, timeout=settings.HYDE_TIMEOUT))
        )

    if not tasks:
        return analysis_result, hyde_doc

    try:
        results = await asyncio.gather(
            *[task for _, task in tasks], return_exceptions=True
        )

        for i, (name, _) in enumerate(tasks):
            result = results[i]
            if isinstance(result, Exception):
                logger.warning(f"{name} failed: {result}")
                continue

            if name == "analysis" and result:
                analysis_result = result
            elif name == "hyde" and result:
                hyde_doc = result

    except Exception as e:
        logger.warning(f"Parallel LLM calls failed: {e}")

    return analysis_result, hyde_doc


def _apply_query_analysis(
    analysis_result: QueryAnalysis,
    query_text: str,
    data: list[extract.DataChunk],
    modalities: set[str],
) -> tuple[str, list[extract.DataChunk], set[str], list[str]]:
    """
    Apply query analysis results to modify query, data, and modalities.

    Returns:
        (updated_query_text, updated_data, updated_modalities, query_variants)
    """
    query_variants: list[str] = []

    if not (analysis_result and analysis_result.success):
        return query_text, data, modalities, query_variants

    # Use detected modalities if any
    if analysis_result.modalities:
        modalities = analysis_result.modalities
        logger.debug(f"Query analysis modalities: {modalities}")

    # Use cleaned query
    if analysis_result.cleaned_query and analysis_result.cleaned_query != query_text:
        logger.debug(
            f"Query analysis cleaning: '{query_text[:40]}...' -> '{analysis_result.cleaned_query[:40]}...'"
        )
        query_text = analysis_result.cleaned_query
        data = [extract.DataChunk(data=[analysis_result.cleaned_query])]

    # Collect query variants
    query_variants.extend(analysis_result.query_variants)

    return query_text, data, modalities, query_variants


def _build_search_data(
    data: list[extract.DataChunk],
    hyde_doc: str | None,
    query_variants: list[str],
    query_text: str,
) -> list[extract.DataChunk]:
    """
    Build the list of data chunks to search with.

    Includes original query, HyDE expansion, and query variants.
    """
    search_data = list(data)

    # Add HyDE expansion if we got one
    if hyde_doc:
        logger.debug(f"HyDE expansion: '{query_text[:30]}...' -> '{hyde_doc[:50]}...'")
        search_data.append(extract.DataChunk(data=[hyde_doc]))

    # Add query variants from analysis (limit to 3)
    for variant in query_variants[:3]:
        search_data.append(extract.DataChunk(data=[variant]))

    return search_data


async def _run_searches(
    search_data: list[extract.DataChunk],
    data: list[extract.DataChunk],
    modalities: set[str],
    internal_limit: int,
    filters: SearchFilters,
    timeout: int,
    use_bm25: bool,
) -> dict[str, float]:
    """
    Run embedding and optionally BM25 searches in parallel, returning fused scores.
    """
    # Build tasks to run in parallel
    embedding_task = search_chunks_embeddings(
        search_data, modalities, internal_limit, filters, timeout
    )

    if use_bm25:
        # Run both searches in parallel
        results = await asyncio.gather(
            embedding_task,
            search_bm25_chunks(data, modalities, internal_limit, filters, timeout),
            return_exceptions=True,
        )

        embedding_scores = results[0] if not isinstance(results[0], Exception) else {}
        if isinstance(results[0], Exception):
            logger.warning(f"Embedding search failed: {results[0]}")

        bm25_scores = results[1] if not isinstance(results[1], Exception) else {}
        if isinstance(results[1], Exception):
            logger.warning(f"BM25 search failed: {results[1]}")
    else:
        embedding_scores = await embedding_task
        bm25_scores = {}

    # Fuse scores from both methods using Reciprocal Rank Fusion
    return fuse_scores_rrf(embedding_scores, bm25_scores)


def _fetch_chunks(
    fused_scores: dict[str, float],
    limit: int,
    use_reranking: bool,
) -> list[Chunk]:
    """
    Fetch chunk objects from database and set their relevance scores.
    """
    if not fused_scores:
        return []

    # Sort by score and take top results
    # If reranking is enabled, fetch more candidates for the reranker to work with
    sorted_ids = sorted(
        fused_scores.keys(), key=lambda x: fused_scores[x], reverse=True
    )
    if use_reranking:
        fetch_limit = limit * RERANK_CANDIDATE_MULTIPLIER
    else:
        fetch_limit = limit
    top_ids = sorted_ids[:fetch_limit]

    with make_session() as db:
        chunks = (
            db.query(Chunk)
            .filter(Chunk.id.in_(top_ids))
            .all()
        )

        # Set relevance_score on each chunk from the fused scores
        for chunk in chunks:
            chunk.relevance_score = fused_scores.get(str(chunk.id), 0.0)

        db.expunge_all()

    return chunks


def _apply_boosts(
    chunks: list[Chunk],
    data: list[extract.DataChunk],
) -> None:
    """
    Apply query term, title, popularity, and recency boosts to chunks.
    """
    if not chunks:
        return

    # Extract query text for boosting
    query_text = " ".join(
        c for chunk in data for c in chunk.data if isinstance(c, str)
    )

    if query_text.strip():
        query_terms = extract_query_terms(query_text)
        apply_query_term_boost(chunks, query_terms)
        # Apply title + popularity boosts (single DB query)
        apply_source_boosts(chunks, query_terms)
    else:
        # No query terms, just apply popularity boost
        apply_source_boosts(chunks, set())


async def _apply_reranking(
    chunks: list[Chunk],
    query_text: str,
    limit: int,
    use_reranking: bool,
) -> list[Chunk]:
    """
    Apply cross-encoder reranking if enabled.
    """
    if not (use_reranking and chunks and query_text.strip()):
        return chunks

    try:
        return await rerank_chunks(
            query_text, chunks, model=settings.RERANK_MODEL, top_k=limit
        )
    except Exception as e:
        logger.warning(f"Reranking failed, using RRF order: {e}")
        return chunks


async def search_chunks(
    data: list[extract.DataChunk],
    modalities: set[str] = set(),
    limit: int = 10,
    filters: SearchFilters = {},
    timeout: int = 2,
    config: SearchConfig = _DEFAULT_CONFIG,
) -> list[Chunk]:
    """
    Search chunks using embedding similarity and optionally BM25.

    Combines results using weighted score fusion, giving bonus to documents
    that match both semantically and lexically.

    If HyDE is enabled, also generates a hypothetical document from the query
    and includes it in the embedding search for better semantic matching.

    Enhancement flags in config override global settings when set:
    - useBm25: Enable BM25 lexical search
    - useHyde: Enable HyDE query expansion
    - useReranking: Enable cross-encoder reranking
    - useQueryAnalysis: LLM-based query analysis (extracts modalities, cleans query, generates variants)
    """
    # Resolve enhancement flags: config overrides global settings
    use_bm25 = (
        config.useBm25 if config.useBm25 is not None else settings.ENABLE_BM25_SEARCH
    )
    use_hyde = (
        config.useHyde if config.useHyde is not None else settings.ENABLE_HYDE_EXPANSION
    )
    use_reranking = (
        config.useReranking
        if config.useReranking is not None
        else settings.ENABLE_RERANKING
    )
    use_query_analysis = (
        config.useQueryAnalysis if config.useQueryAnalysis is not None else False
    )

    internal_limit = limit * CANDIDATE_MULTIPLIER

    # Extract query text
    query_text = " ".join(c for chunk in data for c in chunk.data if isinstance(c, str))

    # Run LLM-based operations in parallel (query analysis + HyDE)
    analysis_result, hyde_doc = await _run_llm_analysis(
        query_text, use_query_analysis, use_hyde
    )

    # Apply query analysis results
    query_text, data, modalities, query_variants = _apply_query_analysis(
        analysis_result, query_text, data, modalities
    )

    # Build search data with HyDE and variants
    search_data = _build_search_data(data, hyde_doc, query_variants, query_text)

    # Run searches and fuse scores
    fused_scores = await _run_searches(
        search_data, data, modalities, internal_limit, filters, timeout, use_bm25
    )

    # Fetch chunks from database
    chunks = _fetch_chunks(fused_scores, limit, use_reranking)

    # Apply various boosts
    _apply_boosts(chunks, data)

    # Apply reranking if enabled
    chunks = await _apply_reranking(chunks, query_text, limit, use_reranking)

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
    config: SearchConfig = _DEFAULT_CONFIG,
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
        config,
    )
    if settings.ENABLE_SEARCH_SCORING and config.useScores and data and data[0].data:
        chunks = await scorer.rank_chunks(data[0].data[0], chunks, min_score=0.3)

    sources = await search_sources(chunks, config.previews)
    sources.sort(key=lambda x: x.search_score or 0, reverse=True)
    return sources[: config.limit]
