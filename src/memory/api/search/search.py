"""
Search endpoints for the knowledge base API.
"""

import asyncio
import logging
import math
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import Integer, Text, and_, func, or_, values
from sqlalchemy import column as sql_column
from sqlalchemy.exc import InvalidRequestError, ProgrammingError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import DetachedInstanceError

from memory.agents.provider_keys import Credential, credential, system_key
from memory.agents.system import CHUNK_SCORE, HYDE, QUERY_ANALYSIS
from memory.common import embedding, extract, settings
from memory.common.instance_settings import (
    ENABLE_BM25_SEARCH,
    ENABLE_HYDE_EXPANSION,
    ENABLE_QUERY_ANALYSIS,
    ENABLE_RERANKING,
    ENABLE_SEARCH_SCORING,
    value as setting,
)
from memory.common.access_control import apply_access_filter_to_query
from memory.common.db.connection import make_session
from memory.common.db.models import (
    BlogPost,
    BookSection,
    CalendarEvent,
    Chunk,
    Comic,
    ForumPost,
    GithubItem,
    GoogleDoc,
    MailMessage,
    Meeting,
    Note,
    Report,
    SourceItem,
    Task,
)
from memory.common.db.models.source_item import item_summaries
from memory.common.db.orphans import orphaned_source_ids
from memory.common.collections import ALL_COLLECTIONS
from memory.api.search.date_range import date_bounds, source_ids_in_range
from memory.api.search.embeddings import require_access_filter, search_chunks_embeddings
from memory.api.search import scorer
from memory.api.search.constants import (
    RRF_K,
    CANDIDATE_MULTIPLIER,
    FINAL_CANDIDATE_MULTIPLIER,
    MAX_CHUNKS_PER_SOURCE,
    MAX_TITLE_CHUNKS_PER_SOURCE,
    QUERY_TERM_BOOST,
    TITLE_MATCH_BOOST,
    RECALLED_TITLE_BOOST,
    POPULARITY_BOOST,
    RECENCY_BOOST_MAX,
    SUMMARY_MATCH_BOOST,
    RECENCY_HALF_LIFE_DAYS,
    STOPWORDS,
)

from memory.api.search.bm25 import search_bm25_chunks
from memory.api.search.filters import apply_registry_filters_sql
from memory.api.search.hyde import expand_query_hyde
from memory.api.search.rerank import rerank_chunks, rerank_timeout
from memory.api.search.query_analysis import analyze_query, mentions_date, QueryAnalysis
from memory.api.search.types import SearchConfig, SearchFilters, SearchResult, chunk_score

logger = logging.getLogger(__name__)

SEARCH_AGENTS = (HYDE, QUERY_ANALYSIS, CHUNK_SCORE)


def searcher_keys(searcher_id: int | None) -> dict[str, Credential | None]:
    """Each search agent's key: personal defaults first, then shared system defaults."""
    if searcher_id is None:
        return {a.slug: None for a in SEARCH_AGENTS}
    agent_for = {a.providers: a for a in SEARCH_AGENTS}
    with make_session() as db:
        found = {p: system_key(db, searcher_id, a) for p, a in agent_for.items()}
        creds = {p: credential(k, searcher_id) if k is not None else None for p, k in found.items()}
    return {a.slug: creds[a.providers] for a in SEARCH_AGENTS}


def extract_query_terms(query: str) -> set[str]:
    """Extract meaningful terms from query, filtering stopwords."""
    words = query.lower().split()
    return {w for w in words if w not in STOPWORDS and len(w) >= 2}


def apply_query_term_boost(
    chunks: Sequence[Chunk],
    query_terms: set[str],
) -> None:
    """
    Boost chunks whose content contains query terms.

    This helps surface chunks that contain exact query words even if
    embedding similarity is lower.
    """
    if not query_terms:
        return

    for chunk in chunks:
        content = (chunk.content or "").lower()
        matches = sum(1 for term in query_terms if term in content)
        chunk.boost += QUERY_TERM_BOOST * (matches / len(query_terms))


def loadable(db: Session, sources: Sequence[SourceItem]) -> list[SourceItem]:
    """``sources`` minus those whose subclass row is gone: reading them raises."""
    orphans = set(orphaned_source_ids(db, [s.id for s in sources]))
    for source_id in sorted(orphans):
        logger.warning("Skipping source item %s: its subclass row is missing", source_id)
    return [s for s in sources if s.id not in orphans]


def apply_source_boosts(
    chunks: Sequence[Chunk],
    query_terms: set[str],
    recalled_titles: list[str] | None = None,
) -> None:
    """
    Apply title, popularity, and recency boosts to chunks in a single DB query.

    - Title boost: chunks get boosted when query terms appear in source title
    - Recalled title boost: chunks get large boost if source title matches recalled content
    - Popularity boost: chunks get boosted based on source karma/popularity
    - Recency boost: newer content gets a small boost that decays over time
    """
    if not chunks:
        return

    # Normalize recalled titles for matching
    recalled_lower = [t.lower() for t in (recalled_titles or [])]

    source_ids = list({chunk.source_id for chunk in chunks})
    now = datetime.now(timezone.utc)

    # Single query to fetch all source metadata
    with make_session() as db:
        sources = loadable(db, db.query(SourceItem).filter(SourceItem.id.in_(source_ids)).all())
        source_map = {}
        for s in sources:
            try:
                title = getattr(s, "title", None) or ""
            except (InvalidRequestError, DetachedInstanceError, ProgrammingError) as e:
                # Polymorphic subclasses may have deferred/missing title columns
                # (e.g. detached instance, schema drift). Anything broader was
                # swallowing real DB errors and silently zeroing search quality.
                logger.warning(
                    "Could not load title for source %s: %s", s.id, e
                )
                title = ""
            source_map[s.id] = {
                "title": title,
                "popularity": s.popularity,
                "inserted_at": s.inserted_at,
            }

    for chunk in chunks:
        source_data = source_map.get(chunk.source_id, {})
        boost = 0.0

        chunk.source_title = source_data.get("title") or None
        title = (chunk.source_title or "").lower()
        if query_terms and title:
            matches = sum(1 for term in query_terms if term in title)
            boost += TITLE_MATCH_BOOST * (matches / len(query_terms))

        if title and any(recalled in title or title in recalled for recalled in recalled_lower):
            boost += RECALLED_TITLE_BOOST

        boost += POPULARITY_BOOST * (source_data.get("popularity", 1.0) - 1.0)

        inserted_at = source_data.get("inserted_at")
        if inserted_at:
            # Handle timezone-naive timestamps
            if inserted_at.tzinfo is None:
                inserted_at = inserted_at.replace(tzinfo=timezone.utc)
            age_days = (now - inserted_at).total_seconds() / 86400
            boost += RECENCY_BOOST_MAX * math.pow(0.5, age_days / RECENCY_HALF_LIFE_DAYS)

        chunk.boost += boost


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
    now: datetime,
    use_query_analysis: bool,
    use_hyde: bool,
    keys: dict[str, Credential | None],
) -> tuple[QueryAnalysis | None, str | None]:
    """
    Run LLM-based query analysis (for queries that mention a date) and/or HyDE
    expansion in parallel.

    Returns:
        (analysis_result, hyde_doc) tuple
    """
    analysis_result: QueryAnalysis | None = None
    hyde_doc: str | None = None

    if not (use_query_analysis or use_hyde):
        return analysis_result, hyde_doc

    tasks = []

    if use_query_analysis and mentions_date(query_text):
        tasks.append(
            (
                "analysis",
                analyze_query(query_text, now=now, key=keys[QUERY_ANALYSIS.slug], timeout=3.0),
            )
        )

    if use_hyde and len(query_text.split()) >= 4:
        tasks.append(
            (
                "hyde",
                expand_query_hyde(
                    query_text, keys[HYDE.slug], timeout=settings.HYDE_TIMEOUT
                ),
            )
        )

    if not tasks:
        return analysis_result, hyde_doc

    try:
        results = await asyncio.gather(
            *[task for _, task in tasks], return_exceptions=True
        )

        for i, (name, _) in enumerate(tasks):
            result = results[i]
            if isinstance(result, BaseException):
                logger.warning(f"{name} failed: {type(result).__name__}")
                continue

            if name == "analysis" and result:
                analysis_result = result
            elif name == "hyde" and result:
                hyde_doc = result

    except Exception as e:
        logger.warning(f"Parallel LLM calls failed: {type(e).__name__}")

    return analysis_result, hyde_doc


def _apply_query_analysis(
    analysis_result: QueryAnalysis | None,
    query_text: str,
    data: list[extract.DataChunk],
    modalities: set[str],
) -> tuple[str, list[extract.DataChunk], set[str], list[str], list[str]]:
    """
    Apply query analysis results to modify query, data, and modalities.

    Returns:
        (updated_query_text, updated_data, updated_modalities, query_variants, recalled_content)
    """
    query_variants: list[str] = []
    recalled_content: list[str] = []

    if not (analysis_result and analysis_result.success):
        return query_text, data, modalities, query_variants, recalled_content

    # Log detected modalities but don't restrict - content may exist in multiple modalities
    # (e.g., "the sequences" is both forum posts AND a book compilation)
    if analysis_result.modalities:
        logger.debug(f"Query analysis detected modalities: {analysis_result.modalities}")

    # Use cleaned query
    if analysis_result.cleaned_query and analysis_result.cleaned_query != query_text:
        logger.debug(
            f"Query analysis cleaning: '{query_text[:40]}...' -> '{analysis_result.cleaned_query[:40]}...'"
        )
        query_text = analysis_result.cleaned_query
        data = [extract.DataChunk(data=[analysis_result.cleaned_query])]

    # Collect query variants
    query_variants.extend(analysis_result.query_variants)

    # Collect recalled content (titles/essays the LLM remembers)
    recalled_content.extend(analysis_result.recalled_content)
    if recalled_content:
        logger.debug(f"Query analysis recalled: {recalled_content}")

    return query_text, data, modalities, query_variants, recalled_content


def restrict_to_date_range(
    filters: SearchFilters, analysis: QueryAnalysis | None, modalities: set[str]
) -> SearchFilters:
    """Narrow ``filters`` to items dated within the analysed time window.

    An empty window leaves the filters alone: a wrong or over-tight range from
    the LLM should degrade to an undated search, not to no results.
    """
    if not (analysis and analysis.success and (analysis.start_date or analysis.end_date)):
        return filters

    access_filter = require_access_filter(filters, "restrict_to_date_range").get("access_filter")
    start, end = date_bounds(analysis.start_date, analysis.end_date)
    with make_session() as db:
        ids = set(source_ids_in_range(db, start, end, access_filter, modalities))
    if (existing := filters.get("source_ids")) is not None:
        ids &= set(existing)
    if not ids:
        logger.debug(f"No items dated {start}..{end}; searching without a date range")
        return filters
    return {**filters, "source_ids": sorted(ids)}


def _build_search_data(
    data: list[extract.DataChunk],
    hyde_doc: str | None,
    query_variants: list[str],
    recalled_content: list[str],
    query_text: str,
) -> list[extract.DataChunk]:
    """
    Build the list of data chunks to search with.

    Includes original query, HyDE expansion, query variants, and recalled content.
    """
    search_data = list(data)

    # Add HyDE expansion if we got one
    if hyde_doc:
        logger.debug(f"HyDE expansion: '{query_text[:30]}...' -> '{hyde_doc[:50]}...'")
        search_data.append(extract.DataChunk(data=[hyde_doc]))

    # Add query variants from analysis (limit to 3)
    for variant in query_variants[:3]:
        search_data.append(extract.DataChunk(data=[variant]))

    # Add recalled content (titles/essays the LLM remembers that match the query)
    for title in recalled_content[:3]:
        search_data.append(extract.DataChunk(data=[title]))

    return search_data


# Where each kind of item keeps the title a recalled essay or post would match.
TITLE_COLUMNS = (
    BlogPost.title,
    ForumPost.title,
    Comic.title,
    BookSection.section_title,
    GithubItem.title,
    GoogleDoc.title,
    Meeting.title,
    MailMessage.subject,
    Note.subject,
    Report.report_title,
    Task.task_title,
    CalendarEvent.event_title,
)

# Shorter titles ("Hi", "Re: x") sit inside too many recalled titles to mean a match.
MIN_CONTAINED_TITLE_LENGTH = 8


def title_matches(title, recalled):
    """Lowercased ``title`` contains ``recalled``, or ``recalled`` contains a long
    enough ``title``."""
    return or_(
        func.strpos(title, recalled) > 0,
        and_(func.length(title) >= MIN_CONTAINED_TITLE_LENGTH, func.strpos(recalled, title) > 0),
    )


def titled_sources(
    db, titles: list[str], modalities: set[str], filters: SearchFilters, limit: int
) -> list[int]:
    """For each of ``titles``, the ``limit`` accessible sources whose titles match
    it, closest in length first. One scan per title column covers every title."""
    recalled = values(
        sql_column("n", Integer), sql_column("title", Text), name="recalled"
    ).data([(n, t.lower()) for n, t in enumerate(titles)])
    found: list[tuple[int, int, int]] = []
    for column in TITLE_COLUMNS:
        model = column.class_
        if modalities.isdisjoint(model.get_collections()):
            continue
        candidates = db.query(model.id, func.lower(column).label("title")).filter(
            model.modality.in_(modalities), func.length(column) > 0
        )
        if source_ids := filters.get("source_ids"):
            candidates = candidates.filter(model.id.in_(source_ids))
        candidates = apply_access_filter_to_query(candidates, filters.get("access_filter"))
        # Materialized so each title is lowercased once, not once per recalled title.
        titled = candidates.cte("titled").prefix_with("MATERIALIZED")
        distance = func.abs(func.length(titled.c.title) - func.length(recalled.c.title))
        position = func.row_number().over(
            partition_by=recalled.c.n, order_by=(distance, titled.c.id)
        )
        matches = (
            db.query(
                recalled.c.n,
                distance.label("distance"),
                titled.c.id,
                position.label("position"),
            )
            .select_from(titled)
            .join(recalled, title_matches(titled.c.title, recalled.c.title))
            .subquery()
        )
        found += db.query(matches.c.n, matches.c.distance, matches.c.id).filter(
            matches.c.position <= limit
        ).all()
    per_title: dict[int, list[int]] = defaultdict(list)
    for n, _, source_id in sorted(found):
        per_title[n].append(source_id)
    return [i for ids in per_title.values() for i in ids[:limit]]


def fetch_chunks_by_title(
    titles: list[str],
    modalities: set[str],
    filters: "SearchFilters | None" = None,
    limit_per_title: int = 5,
) -> dict[str, float]:
    """
    Fetch chunks from sources whose titles match the given titles.

    This ensures recalled content from LLM makes it into the candidate pool
    even if BM25/embedding search doesn't rank it highly.

    Access control is applied via the access_filter in `filters` so that
    LLM prompt-injection cannot exfiltrate unauthorized content by injecting
    confidential document titles into the recalled_content array.
    """
    # A blank title would match every row.
    titles = [t for t in titles if t.strip()][:5]
    if not titles or not modalities:
        return {}

    # Same fail-closed gate as the embedding/BM25 layers — a missing
    # ``access_filter`` key here would otherwise silently fall through to
    # an unfiltered SourceItem query (apply_access_filter_to_query treats
    # None as "no filter"), which is the exact prompt-injection
    # exfiltration path the docstring above describes.
    filters = require_access_filter(filters, "fetch_chunks_by_title")

    with make_session() as db:
        source_ids = list(dict.fromkeys(
            titled_sources(db, titles, modalities, filters, limit_per_title)
        ))
        if not source_ids:
            return {}

        position = (
            func.row_number()
            .over(
                partition_by=Chunk.source_id,
                order_by=(Chunk.is_summary.desc(), Chunk.created_at, Chunk.id),
            )
            .label("position")
        )
        ranked = (
            db.query(Chunk.id, position).filter(Chunk.source_id.in_(source_ids)).subquery()
        )
        chunks = (
            db.query(ranked.c.id)
            .filter(ranked.c.position <= MAX_TITLE_CHUNKS_PER_SOURCE)
            .all()
        )

        # Give these chunks a baseline score so they're included in fusion
        # The actual boost will be applied later by apply_source_boosts
        return {str(c.id): 0.5 for c in chunks}


async def _run_searches(
    search_data: list[extract.DataChunk],
    modalities: set[str],
    internal_limit: int,
    filters: SearchFilters,
    timeout: int,
    use_bm25: bool,
    recalled_titles: list[str] | None = None,
    use_embeddings: bool = True,
) -> tuple[dict[str, float], dict[str, float]]:
    """
    Run embedding and optionally BM25 searches in parallel.

    Returns ``(fused_scores, embedding_scores)``: the RRF-fused scores used
    for candidate selection, plus the raw per-chunk embedding similarities
    (max cosine across collections) so reranking can fall back to them for
    chunks the text cross-encoder cannot score.
    """
    async def no_scores() -> dict[str, float]:
        return {}

    embedding_task = (
        search_chunks_embeddings(search_data, modalities, internal_limit, filters, timeout)
        if use_embeddings
        else no_scores()
    )

    if use_bm25:
        # Run both searches in parallel
        # Note: BM25 uses search_data to include query variants and recalled content
        results = await asyncio.gather(
            embedding_task,
            search_bm25_chunks(search_data, modalities, internal_limit, filters, timeout),
            return_exceptions=True,
        )

        embedding_scores = results[0] if not isinstance(results[0], BaseException) else {}
        if isinstance(results[0], BaseException):
            logger.warning(f"Embedding search failed: {results[0]}")

        bm25_scores = results[1] if not isinstance(results[1], BaseException) else {}
        if isinstance(results[1], BaseException):
            logger.warning(f"BM25 search failed: {results[1]}")
    else:
        embedding_scores = await embedding_task
        bm25_scores = {}

    # Fuse scores from both methods using Reciprocal Rank Fusion
    fused = fuse_scores_rrf(embedding_scores, bm25_scores)

    # Add chunks from recalled titles (direct title match)
    # This ensures LLM-recalled content makes it into the candidate pool.
    # Pass filters so access control prevents prompt-injection exfiltration.
    if recalled_titles:
        title_chunks = await asyncio.to_thread(
            fetch_chunks_by_title, recalled_titles, modalities, filters
        )
        for chunk_id, score in title_chunks.items():
            if chunk_id not in fused:
                fused[chunk_id] = score

    return fused, embedding_scores


def _fetch_chunks(
    fused_scores: dict[str, float],
    embedding_scores: dict[str, float],
    limit: int,
) -> list[Chunk]:
    """
    Fetch the top ``limit`` fused chunks from the database and set their relevance scores.

    ``embedding_scores`` carries the raw per-chunk embedding similarity, stashed
    on each chunk so reranking can fall back to it for content-less chunks.
    """
    if not fused_scores:
        return []

    sorted_ids = sorted(
        fused_scores.keys(), key=lambda x: fused_scores[x], reverse=True
    )
    top_ids = sorted_ids[:limit]

    with make_session() as db:
        chunks = (
            db.query(Chunk)
            .filter(Chunk.id.in_(top_ids))
            .all()
        )

        # Set relevance_score on each chunk from the fused scores, and stash
        # the raw embedding similarity for rerank's content-less fallback.
        for chunk in chunks:
            chunk.relevance_score = fused_scores.get(str(chunk.id), 0.0)
            chunk.embedding_score = embedding_scores.get(str(chunk.id), 0.0)

        db.expunge_all()

    return chunks


def _apply_boosts(
    chunks: Sequence[Chunk],
    data: list[extract.DataChunk],
    recalled_content: list[str] | None = None,
) -> None:
    """
    Set each chunk's boost from its summary flag, query terms, title, popularity and recency.
    """
    if not chunks:
        return

    for chunk in chunks:
        chunk.boost = SUMMARY_MATCH_BOOST if chunk.is_summary else 0.0

    # Extract query text for boosting
    query_text = " ".join(
        c for chunk in data for c in chunk.data if isinstance(c, str)
    )

    if query_text.strip():
        query_terms = extract_query_terms(query_text)
        apply_query_term_boost(chunks, query_terms)
        # Apply title + popularity boosts (single DB query)
        apply_source_boosts(chunks, query_terms, recalled_content)
    else:
        # No query terms, just apply popularity and recalled title boosts
        apply_source_boosts(chunks, set(), recalled_content)


async def _apply_reranking(
    chunks: Sequence[Chunk],
    query_text: str,
    limit: int,
    use_reranking: bool,
) -> list[Chunk]:
    """
    The chunks of the ``limit`` best sources by boosted score, reranked by the
    cross-encoder if enabled.
    """
    if use_reranking and chunks and query_text.strip():
        try:
            chunks = await asyncio.wait_for(
                rerank_chunks(query_text, chunks, model=settings.RERANK_MODEL),
                rerank_timeout(len(chunks)),
            )
        except Exception as e:
            logger.warning(f"Reranking failed, using RRF order: {e!r}")
    return top_sources(chunks, limit)


def top_sources(chunks: Sequence[Chunk], limit: int) -> list[Chunk]:
    """The best ``MAX_CHUNKS_PER_SOURCE`` chunks of each of the ``limit`` sources
    with the best chunks, best first.

    Cut by source, not by chunk, so one long item's many matching chunks can't
    crowd every other item out of the results.
    """
    kept: list[Chunk] = []
    per_source: dict[int, int] = {}
    for chunk in sorted(chunks, key=chunk_score, reverse=True):
        count = per_source.get(chunk.source_id, 0)
        if count == MAX_CHUNKS_PER_SOURCE or (count == 0 and len(per_source) == limit):
            continue
        per_source[chunk.source_id] = count + 1
        kept.append(chunk)
    return kept


async def score_sources(query: str, chunks: Sequence[Chunk], key: Credential | None) -> list[Chunk]:
    """LLM-score each source's best chunk, then keep the other chunks of the
    sources that pass, so scoring costs a call per result rather than per chunk.

    ``chunks`` must be best first, as ``top_sources`` returns them.
    """
    best: dict[int, Chunk] = {}
    for chunk in chunks:
        best.setdefault(chunk.source_id, chunk)
    scored = await scorer.rank_chunks(query, list(best.values()), key, min_score=0.3)
    passed = {c.source_id for c in scored}
    siblings = [c for c in chunks if c.source_id in passed and best[c.source_id] is not c]
    # A sibling's reranker score must not outrank the LLM's verdict on its source.
    for chunk in siblings:
        chunk.relevance_score = min(chunk.relevance_score, best[chunk.source_id].relevance_score)
    return sorted(scored + siblings, key=chunk_score, reverse=True)


async def search_chunks(
    data: list[extract.DataChunk],
    modalities: set[str] | None = None,
    limit: int = 10,
    filters: SearchFilters | None = None,
    timeout: int = 2,
    config: SearchConfig | None = None,
    keys: dict[str, Credential | None] | None = None,
) -> list[Chunk]:
    """
    The best chunks (at most MAX_CHUNKS_PER_SOURCE each) of the ``limit`` best
    sources, by embedding similarity and optionally BM25, fused by rank (RRF),
    boosted, and reranked by a cross-encoder.

    Date queries are also analysed by an LLM, whose date range narrows the
    search and whose variants and recalled titles are searched alongside the
    query. With HyDE, a hypothetical answer to the query is embedded too.

    Enhancement flags in config can turn off an enhancement the settings enable:
    - useBm25: BM25 lexical search (ENABLE_BM25_SEARCH)
    - useHyde: HyDE query expansion (ENABLE_HYDE_EXPANSION)
    - useReranking: cross-encoder reranking (ENABLE_RERANKING)
    - useQueryAnalysis: LLM-based query analysis, which extracts modalities,
      cleans the query and generates variants (ENABLE_QUERY_ANALYSIS)
    """
    # ``None`` sentinels: any defaults that look like containers must be
    # constructed per call. A shared mutable default like ``filters={}``
    # would let a future ``filters.setdefault(...)`` on the hot path
    # leak across users via the shared dict identity.
    if modalities is None:
        modalities = set()
    if filters is None:
        filters = {}
    if config is None:
        config = SearchConfig()

    # The settings are the operator's ceiling: a request can turn an
    # enhancement off, never on (they can cost LLM calls on the server's key).
    # Without a Voyage key only keyword search can run, so it runs regardless.
    use_embeddings = embedding.configured()
    use_bm25 = not use_embeddings or (
        setting(ENABLE_BM25_SEARCH) and config.useBm25 is not False
    )
    use_hyde = (
        use_embeddings and setting(ENABLE_HYDE_EXPANSION) and config.useHyde is not False
    )
    use_reranking = (
        use_embeddings and setting(ENABLE_RERANKING) and config.useReranking is not False
    )
    use_query_analysis = (
        setting(ENABLE_QUERY_ANALYSIS) and config.useQueryAnalysis is not False
    )

    internal_limit = limit * CANDIDATE_MULTIPLIER

    # Extract query text
    query_text = " ".join(c for chunk in data for c in chunk.data if isinstance(c, str))

    # Read before any await so a clock pinned around this call (evals) applies.
    now = datetime.now(timezone.utc)

    if keys is None:
        keys = searcher_keys(None)
    # Run LLM-based operations in parallel (query analysis + HyDE)
    analysis_result, hyde_doc = await _run_llm_analysis(
        query_text, now, use_query_analysis, use_hyde, keys
    )
    filters = await asyncio.to_thread(
        restrict_to_date_range, filters, analysis_result, modalities
    )

    # Apply query analysis results
    query_text, data, modalities, query_variants, recalled_content = _apply_query_analysis(
        analysis_result, query_text, data, modalities
    )

    # Build search data with HyDE, variants, and recalled content
    search_data = _build_search_data(data, hyde_doc, query_variants, recalled_content, query_text)

    # Run searches and fuse scores
    fused_scores, embedding_scores = await _run_searches(
        search_data, modalities, internal_limit, filters, timeout, use_bm25,
        recalled_titles=recalled_content, use_embeddings=use_embeddings,
    )

    # Fetch chunks from database
    # Boosts apply to whichever score ends up last (types.chunk_score), so they're
    # set on more candidates than the limit and every cut after this ranks by them.
    chunks = await asyncio.to_thread(
        _fetch_chunks, fused_scores, embedding_scores, limit * FINAL_CANDIDATE_MULTIPLIER
    )
    await asyncio.to_thread(_apply_boosts, chunks, data, recalled_content)
    chunks = await _apply_reranking(chunks, query_text, limit, use_reranking)

    return chunks


async def search_sources(
    chunks: Sequence[Chunk],
    previews: bool = False,
    filters: "SearchFilters | None" = None,
) -> list[SearchResult]:
    """Load SourceItems for a fused set of chunks.

    Final-merge layer of the documented three-layer access control:
    Qdrant payload filter, BM25 SQL filter, AND this final query. Pre-fix,
    this layer skipped the access check, so any future regression in the
    upstream layers (e.g. an as_payload() refactor that drops project_id,
    a stale-sensitivity ingestion race, or a Qdrant filter shape change)
    would silently exfiltrate the parent SourceItem rows. The chunk-level
    access filter is reused here verbatim so the three layers stay in
    lock-step.

    Fail-closed on missing filters: ``filters`` MUST contain an
    ``access_filter`` key. A caller that doesn't thread filters through
    is treated as a programming error and raises ``ValueError`` —
    defense-in-depth that's only effective when callers remember to opt
    in is a hint, not a defense (Parvati flagged in PR #77).
    """
    if filters is None or "access_filter" not in filters:
        raise ValueError(
            "search_sources requires `filters` with an `access_filter` key. "
            "The final-merge access check is mandatory — a None/missing "
            "filter would silently bypass the third layer of the documented "
            "three-layer access control invariant."
        )

    by_source = defaultdict(list)
    for chunk in chunks:
        by_source[chunk.source_id].append(chunk)

    if not by_source:
        return []

    def load() -> list[SearchResult]:
        with make_session() as db:
            query = db.query(SourceItem).filter(SourceItem.id.in_(by_source.keys()))
            query = apply_access_filter_to_query(query, filters["access_filter"])
            # Title recall adds chunks that never passed the Qdrant/BM25 tag filters.
            tag_filters = {k: filters.get(k) for k in ("tags", "exclude_tags")}
            query = apply_registry_filters_sql(query, tag_filters)
            sources = loadable(db, query.all())
            summaries = item_summaries(db, [source.id for source in sources])
            return [
                SearchResult.from_source_item(
                    source, by_source[source.id], previews, summaries.get(source.id)
                )
                for source in sources
            ]

    return await asyncio.to_thread(load)


async def search(
    data: list[extract.DataChunk],
    modalities: set[str] | None = None,
    filters: SearchFilters | None = None,
    config: SearchConfig | None = None,
    searcher_id: int | None = None,
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
    if modalities is None:
        modalities = set()
    if filters is None:
        filters = {}
    if config is None:
        config = SearchConfig()
    allowed_modalities = modalities & ALL_COLLECTIONS.keys()
    keys = await asyncio.to_thread(searcher_keys, searcher_id)
    chunks = await search_chunks(
        data,
        allowed_modalities,
        config.limit,
        filters,
        config.timeout,
        config,
        keys=keys,
    )
    if setting(ENABLE_SEARCH_SCORING) and config.useScores and data and data[0].data:
        query_item = data[0].data[0]
        if isinstance(query_item, str):
            chunks = await score_sources(query_item, chunks, keys[CHUNK_SCORE.slug])
        else:
            logger.debug(f"Skipping scoring: query is {type(query_item).__name__}, not str")

    sources = await search_sources(chunks, config.previews, filters=filters)
    sources.sort(key=lambda x: x.search_score or 0, reverse=True)
    return sources[: config.limit]
