"""
Search endpoints for the knowledge base API.
"""

import asyncio
import logging
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
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

# Default config for when none is provided
_DEFAULT_CONFIG = SearchConfig()

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

# Recency boost settings
# Maximum bonus for brand new content (additive)
RECENCY_BOOST_MAX = 0.005
# Half-life in days: content loses half its recency boost every N days
RECENCY_HALF_LIFE_DAYS = 90

# Query expansion: map abbreviations/acronyms to full forms
# These help match when users search for "ML" but documents say "machine learning"
QUERY_EXPANSIONS: dict[str, list[str]] = {
    # AI/ML abbreviations
    "ai": ["artificial intelligence"],
    "ml": ["machine learning"],
    "dl": ["deep learning"],
    "nlp": ["natural language processing"],
    "cv": ["computer vision"],
    "rl": ["reinforcement learning"],
    "llm": ["large language model", "language model"],
    "gpt": ["generative pretrained transformer", "language model"],
    "nn": ["neural network"],
    "cnn": ["convolutional neural network"],
    "rnn": ["recurrent neural network"],
    "lstm": ["long short term memory"],
    "gan": ["generative adversarial network"],
    "rag": ["retrieval augmented generation"],
    # Rationality/EA terms
    "ea": ["effective altruism"],
    "lw": ["lesswrong", "less wrong"],
    "gwwc": ["giving what we can"],
    "agi": ["artificial general intelligence"],
    "asi": ["artificial superintelligence"],
    "fai": ["friendly ai", "ai alignment"],
    "x-risk": ["existential risk"],
    "xrisk": ["existential risk"],
    "p(doom)": ["probability of doom", "ai risk"],
    # Reverse mappings (full forms -> abbreviations)
    "artificial intelligence": ["ai"],
    "machine learning": ["ml"],
    "deep learning": ["dl"],
    "natural language processing": ["nlp"],
    "computer vision": ["cv"],
    "reinforcement learning": ["rl"],
    "neural network": ["nn"],
    "effective altruism": ["ea"],
    "existential risk": ["x-risk", "xrisk"],
    # Family relationships (bidirectional)
    "father": ["son", "daughter", "child", "parent", "dad"],
    "mother": ["son", "daughter", "child", "parent", "mom"],
    "parent": ["child", "son", "daughter", "father", "mother"],
    "son": ["father", "parent", "child"],
    "daughter": ["mother", "parent", "child"],
    "child": ["parent", "father", "mother"],
    "dad": ["father", "son", "daughter", "child"],
    "mom": ["mother", "son", "daughter", "child"],
}

# Modality detection patterns: map query phrases to collection names
# Each entry is (pattern, modalities, strip_pattern)
# - pattern: regex to match in query
# - modalities: set of collection names to filter to
# - strip_pattern: whether to remove the matched text from query
MODALITY_PATTERNS: list[tuple[str, set[str], bool]] = [
    # Comics
    (r"\b(comic|comics|webcomic|webcomics)\b", {"comic"}, True),
    # Forum posts (LessWrong, EA Forum, etc.)
    (r"\b(on\s+)?(lesswrong|lw|less\s+wrong)\b", {"forum"}, True),
    (r"\b(on\s+)?(ea\s+forum|effective\s+altruism\s+forum)\b", {"forum"}, True),
    (r"\b(on\s+)?(alignment\s+forum|af)\b", {"forum"}, True),
    (r"\b(forum\s+post|lw\s+post|post\s+on)\b", {"forum"}, True),
    # Books
    (r"\b(in\s+a\s+book|in\s+the\s+book|book|chapter)\b", {"book"}, True),
    # Blog posts / articles
    (r"\b(blog\s+post|blog|article)\b", {"blog"}, True),
    # Email
    (r"\b(email|e-mail|mail)\b", {"mail"}, True),
    # Photos / images
    (r"\b(photo|photograph|picture|image)\b", {"photo"}, True),
    # Documents
    (r"\b(document|pdf|doc)\b", {"doc"}, True),
    # Chat / messages
    (r"\b(chat|message|discord|slack)\b", {"chat"}, True),
    # Git
    (r"\b(commit|git|pull\s+request|pr)\b", {"git"}, True),
]

# Meta-language patterns to strip (these don't indicate modality, just noise)
META_LANGUAGE_PATTERNS: list[str] = [
    r"\bthere\s+was\s+(something|some|some\s+\w+|an?\s+\w+)\s+(about|on)\b",
    r"\bi\s+remember\s+(reading|seeing|there\s+being)\s*(an?\s+)?",
    r"\bi\s+(read|saw|found)\s+(something|an?\s+\w+)\s+about\b",
    r"\bsomething\s+about\b",
    r"\bsome\s+about\b",
    r"\bthis\s+whole\s+\w+\s+thing\b",
    r"\bthat\s+\w+\s+thing\b",
    r"\bthat\s+about\b",  # Clean up leftover "that about"
    r"\ba\s+about\b",  # Clean up leftover "a about"
    r"\bthe\s+about\b",  # Clean up leftover "the about"
    r"\bthere\s+was\s+some\s+about\b",  # Clean up leftover
]


def detect_modality_hints(query: str) -> tuple[str, set[str]]:
    """
    Detect content type hints in query and extract modalities.

    Returns:
        (cleaned_query, detected_modalities)
        - cleaned_query: query with modality indicators and meta-language removed
        - detected_modalities: set of collection names detected from query
    """
    query_lower = query.lower()
    detected: set[str] = set()
    cleaned = query

    # First, detect and strip modality patterns
    for pattern, modalities, strip in MODALITY_PATTERNS:
        if re.search(pattern, query_lower, re.IGNORECASE):
            detected.update(modalities)
            if strip:
                cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)

    # Strip meta-language patterns (regardless of modality detection)
    for pattern in META_LANGUAGE_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)

    # Clean up whitespace
    cleaned = " ".join(cleaned.split())

    return cleaned, detected


def expand_query(query: str) -> str:
    """
    Expand query with synonyms and abbreviations.

    This helps match documents that use different terminology for the same concept.
    For example, "ML algorithms" -> "ML machine learning algorithms"
    """
    query_lower = query.lower()
    expansions = []

    for term, synonyms in QUERY_EXPANSIONS.items():
        # Check if term appears as a whole word in the query
        # Use word boundaries to avoid matching partial words
        pattern = r'\b' + re.escape(term) + r'\b'
        if re.search(pattern, query_lower):
            expansions.extend(synonyms)

    if expansions:
        # Add expansions to the original query
        return query + " " + " ".join(expansions)
    return query


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
        sources = db.query(SourceItem).filter(
            SourceItem.id.in_(source_ids)
        ).all()
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


# Keep legacy functions for backwards compatibility and testing
def apply_title_boost(chunks: list[Chunk], query_terms: set[str]) -> None:
    """Legacy function - use apply_source_boosts instead."""
    apply_source_boosts(chunks, query_terms)


def apply_popularity_boost(chunks: list[Chunk]) -> None:
    """Legacy function - use apply_source_boosts instead."""
    apply_source_boosts(chunks, set())


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
    - useQueryExpansion: Enable synonym/abbreviation expansion
    - useModalityDetection: Detect content type hints from query
    """
    # Resolve enhancement flags: config overrides global settings
    use_bm25 = config.useBm25 if config.useBm25 is not None else settings.ENABLE_BM25_SEARCH
    use_hyde = config.useHyde if config.useHyde is not None else settings.ENABLE_HYDE_EXPANSION
    use_reranking = config.useReranking if config.useReranking is not None else settings.ENABLE_RERANKING
    use_query_expansion = config.useQueryExpansion if config.useQueryExpansion is not None else True
    use_modality_detection = config.useModalityDetection if config.useModalityDetection is not None else False

    # Search for more candidates than requested, fuse scores, then return top N
    # This helps find results that rank well in one method but not the other
    internal_limit = limit * CANDIDATE_MULTIPLIER

    # Extract query text
    query_text = " ".join(
        c for chunk in data for c in chunk.data if isinstance(c, str)
    )

    # Detect modality hints and clean query if enabled
    if use_modality_detection:
        cleaned_query, detected_modalities = detect_modality_hints(query_text)
        if detected_modalities:
            # Override passed modalities with detected ones
            modalities = detected_modalities
            logger.debug(f"Modality detection: '{query_text[:50]}...' -> modalities={detected_modalities}")
        if cleaned_query != query_text:
            logger.debug(f"Query cleaning: '{query_text[:50]}...' -> '{cleaned_query[:50]}...'")
            query_text = cleaned_query
            # Update data with cleaned query for downstream processing
            data = [extract.DataChunk(data=[cleaned_query])]

    if use_query_expansion:
        expanded_query = expand_query(query_text)
        # If query was expanded, use expanded version for search
        if expanded_query != query_text:
            logger.debug(f"Query expansion: '{query_text}' -> '{expanded_query}'")
            search_data = [extract.DataChunk(data=[expanded_query])]
        else:
            search_data = list(data)  # Copy to avoid modifying original
    else:
        search_data = list(data)

    # Apply HyDE expansion if enabled
    if use_hyde:
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
    if use_bm25:
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
    if use_reranking:
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

    # Apply query term presence boost
    if chunks and query_text.strip():
        query_terms = extract_query_terms(query_text)
        apply_query_term_boost(chunks, query_terms)
        # Apply title + popularity boosts (single DB query)
        apply_source_boosts(chunks, query_terms)
    elif chunks:
        # No query terms, just apply popularity boost
        apply_source_boosts(chunks, set())

    # Rerank using cross-encoder for better precision
    if use_reranking and chunks and query_text.strip():
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
    if settings.ENABLE_SEARCH_SCORING and config.useScores:
        chunks = await scorer.rank_chunks(data[0].data[0], chunks, min_score=0.3)

    sources = await search_sources(chunks, config.previews)
    sources.sort(key=lambda x: x.search_score or 0, reverse=True)
    return sources[: config.limit]
