"""
LLM-based query analysis for intelligent search preprocessing.

Uses a fast LLM (Haiku) to analyze natural language queries and extract:
- Modalities: content types to search (forum, book, comic, etc.)
- Source hints: author names, domains, or specific sources
- Cleaned query: the actual search terms with meta-language removed
- Query variants: alternative phrasings to search
- Recalled content: specific titles/essays the LLM recalls that match the query
- Date range: absolute dates for relative time references ("since Monday")

This runs in parallel with HyDE for maximum efficiency.
"""

import asyncio
import logging
import time
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy.exc import SQLAlchemyError

from memory.agents.call import Record, run_agent
from memory.agents.provider_keys import Credential
from memory.agents.system import QUERY_ANALYSIS
from memory.common.db.connection import make_session
from memory.common.db.models import SourceItem
from memory.common.metrics import record_metric

logger = logging.getLogger(__name__)

# Modalities with content, refreshed hourly. Only their names reach the prompt: it goes
# to the searcher's own provider account, and counts or source domains would describe
# other users' content.
_modality_cache: frozenset[str] = frozenset()
_cache_timestamp: float = 0
_CACHE_TTL_SECONDS = 3600


def _refresh_modality_cache() -> None:
    global _modality_cache, _cache_timestamp
    try:
        with make_session() as db:
            rows = db.query(SourceItem.modality).distinct().all()
    except SQLAlchemyError as e:
        logger.warning(f"Database error refreshing modality cache: {e}")
        return
    _modality_cache, _cache_timestamp = frozenset(m for (m,) in rows if m), time.time()


def _get_available_modalities() -> frozenset[str]:
    if time.time() - _cache_timestamp > _CACHE_TTL_SECONDS or not _modality_cache:
        _refresh_modality_cache()
    return _modality_cache


@dataclass
class QueryAnalysis:
    """Result of LLM-based query analysis."""

    modalities: set[str] = field(default_factory=set)
    sources: list[str] = field(default_factory=list)
    cleaned_query: str = ""
    query_variants: list[str] = field(default_factory=list)
    recalled_content: list[str] = field(default_factory=list)  # Titles/essays LLM recalls
    start_date: date | None = None
    end_date: date | None = None
    success: bool = False


def parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def date_range(start: str | None, end: str | None) -> tuple[date | None, date | None]:
    start_date, end_date = parse_date(start), parse_date(end)
    if start_date and end_date and start_date > end_date:
        return end_date, start_date
    return start_date, end_date


def format_now(now: datetime) -> str:
    return f"{now.astimezone(timezone.utc):%A %Y-%m-%d %H:%M} UTC"


def analysis_prompt(query: str, now: datetime, modalities: Collection[str]) -> str:
    """The per-call half of the analysis: what is indexed, the clock, the query."""
    listed = "\n".join(f"  - {name}" for name in sorted(modalities))
    return (
        f"Indexed content:\n{listed or '  (no content indexed yet)'}\n"
        f"Current date/time: {format_now(now)}\n"
        f"Query: {query}"
    )


# Cache for recent analyses (LRU-style: accessed entries move to end)
_analysis_cache: dict[str, QueryAnalysis] = {}
_analysis_cache_lock: asyncio.Lock | None = None
_CACHE_MAX_SIZE = 100


def _get_cache_lock() -> asyncio.Lock:
    """Lazily initialize the cache lock to avoid event loop issues."""
    global _analysis_cache_lock
    if _analysis_cache_lock is None:
        _analysis_cache_lock = asyncio.Lock()
    return _analysis_cache_lock


async def analyze_query(
    query: str, now: datetime, key: Credential | None, timeout: float = 3.0
) -> QueryAnalysis:
    """Modalities, sources, a cleaned query, variants and a date range for `query`,
    with relative dates resolved against `now`. Without a key, or when the call
    fails or times out, the query is searched as typed."""
    if key is None:
        return QueryAnalysis(cleaned_query=query)

    # Relative dates resolve differently each day
    cache_key = f"{now.astimezone(timezone.utc):%Y-%m-%d} {query.lower().strip()}"
    async with _get_cache_lock():
        if cache_key in _analysis_cache:
            # Move to end for LRU behavior (Python 3.7+ dicts maintain insertion order)
            result = _analysis_cache.pop(cache_key)
            _analysis_cache[cache_key] = result
            return result

    result = QueryAnalysis(cleaned_query=query)
    try:
        # An expired cache refreshes with several DB queries; on the loop that
        # would stall every concurrent request.
        modalities = await asyncio.to_thread(_get_available_modalities)
        answer = await asyncio.wait_for(
            run_agent(
                QUERY_ANALYSIS.slug,
                analysis_prompt(query, now, modalities),
                user=key.user_id,
                key=key,
                record=Record.COST,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(f"Query analysis timed out for: {query[:50]}...")
        record_metric("search", "query_analysis", status="timeout", labels={"query_length": len(query)})
        return result
    except Exception as e:
        # LLMError or any provider's transient error: search goes on unanalysed.
        logger.error(f"Query analysis failed: {e!r}")
        record_metric("search", "query_analysis", status="error", labels={"error": type(e).__name__})
        return result

    data = answer.data
    assert data is not None  # an agent with a result schema returns data or raises
    result.modalities = set(data["modalities"]) & modalities
    result.sources = data["sources"]
    result.cleaned_query = data["cleaned_query"]
    result.query_variants = data["query_variants"]
    result.recalled_content = data["recalled_content"]
    result.start_date, result.end_date = date_range(data["start_date"], data["end_date"])
    result.success = True
    logger.debug(
        f"Query analysis: '{query[:40]}...' -> "
        f"modalities={result.modalities}, "
        f"cleaned='{result.cleaned_query[:30]}...', "
        f"recalled={result.recalled_content}, "
        f"dates={result.start_date}..{result.end_date}"
    )

    # Cache the result (evicts oldest entries first - LRU since we move on access)
    async with _get_cache_lock():
        if len(_analysis_cache) >= _CACHE_MAX_SIZE:
            # Remove oldest half (front of dict = least recently used)
            for stale in list(_analysis_cache)[: _CACHE_MAX_SIZE // 2]:
                del _analysis_cache[stale]
        _analysis_cache[cache_key] = result
    return result
