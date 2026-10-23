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
import json
import logging
import re
import textwrap
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from sqlalchemy import func, text
from sqlalchemy.exc import SQLAlchemyError

from memory.common import settings
from memory.common.db.connection import make_session
from memory.common.db.models import SourceItem
from memory.common.llms import create_provider, LLMSettings, Message
from memory.common.metrics import record_metric

logger = logging.getLogger(__name__)

# Threshold for listing specific sources (if fewer than N distinct domains, list them)
MAX_DOMAINS_TO_LIST = 10

# Valid SQL identifier pattern (prevents SQL injection in dynamic table names)
_VALID_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _is_valid_sql_identifier(name: str) -> bool:
    """Check if a name is a valid SQL identifier to prevent SQL injection."""
    return bool(_VALID_IDENTIFIER.match(name)) and len(name) <= 128



@dataclass
class ModalityInfo:
    """Information about a modality from the database."""

    name: str
    count: int
    domains: list[str] = field(default_factory=list)
    source_count: int | None = None  # For modalities with parent entities (e.g., books)

    @property
    def description(self) -> str:
        """Build description including domains if there are few enough."""
        if self.source_count:
            base = f"{self.source_count:,} sources ({self.count:,} sections)"
        else:
            base = f"{self.count:,} items"

        if self.domains:
            return f"{base} from: {', '.join(self.domains)}"
        return base


# Cache for database-derived information
_modality_cache: dict[str, ModalityInfo] = {}
_cache_timestamp: float = 0
_CACHE_TTL_SECONDS = 3600  # Refresh every hour


def _get_tables_with_url_column(db) -> list[str]:
    """Query database schema to find tables that have a 'url' column."""
    result = db.execute(text("""
        SELECT table_name FROM information_schema.columns
        WHERE column_name = 'url' AND table_schema = 'public'
    """))
    return [row[0] for row in result]


def _get_modality_domains(db) -> dict[str, list[str]]:
    """Get domains for each modality that has URL data."""
    tables = _get_tables_with_url_column(db)
    if not tables:
        return {}

    # Build a UNION query to get modality + url from all URL-containing tables
    # Validate table names to prevent SQL injection
    union_parts = []
    for table in tables:
        if not _is_valid_sql_identifier(table):
            logger.warning(f"Skipping invalid table name: {table!r}")
            continue
        # Double quotes for PostgreSQL identifier quoting (validated above)
        union_parts.append(
            f'SELECT s.modality, t.url FROM source_item s '
            f'JOIN "{table}" t ON s.id = t.id WHERE t.url IS NOT NULL'
        )

    if not union_parts:
        return {}

    query = " UNION ALL ".join(union_parts)

    try:
        result = db.execute(text(query))
        rows = list(result)
    except SQLAlchemyError as e:
        logger.debug(f"Database error getting modality URLs: {e}")
        return {}

    # Group URLs by modality and extract domains
    modality_domains: dict[str, set[str]] = {}
    for modality, url in rows:
        if not modality or not url:
            continue
        try:
            domain = urlparse(url).netloc
            if domain:
                domain = domain.replace("www.", "")
                if modality not in modality_domains:
                    modality_domains[modality] = set()
                modality_domains[modality].add(domain)
        except ValueError:
            continue

    # Only return domains for modalities with few enough to list
    return {
        modality: sorted(domains)
        for modality, domains in modality_domains.items()
        if len(domains) <= MAX_DOMAINS_TO_LIST
    }


def _get_source_counts(db) -> dict[str, int]:
    """Get distinct source counts for modalities with parent entities."""
    try:
        # Books: count distinct book_id from book_section
        result = db.execute(text(
            "SELECT COUNT(DISTINCT book_id) FROM book_section"
        ))
        book_count = result.scalar() or 0
        return {"book": book_count}
    except SQLAlchemyError:
        return {}


def _refresh_modality_cache() -> None:
    """Query database to find modalities with actual content."""
    global _modality_cache, _cache_timestamp

    try:
        with make_session() as db:
            # Get modality counts
            results = (
                db.query(SourceItem.modality, func.count(SourceItem.id))
                .group_by(SourceItem.modality)
                .order_by(func.count(SourceItem.id).desc())
                .all()
            )

            # Get domains for modalities with URLs (single query)
            modality_domains = _get_modality_domains(db)

            # Get source counts for modalities with parent entities
            source_counts = _get_source_counts(db)

            # Built aside and swapped in at once: concurrent searches read the
            # cache from other threads while this runs.
            cache = {
                modality: ModalityInfo(
                    name=modality,
                    count=count,
                    domains=modality_domains.get(modality, []),
                    source_count=source_counts.get(modality),
                )
                for modality, count in results
                if modality and count > 0
            }
            _modality_cache, _cache_timestamp = cache, time.time()
            logger.debug(f"Refreshed modality cache: {list(_modality_cache.keys())}")

    except SQLAlchemyError as e:
        logger.warning(f"Database error refreshing modality cache: {e}")


def _get_available_modalities() -> dict[str, ModalityInfo]:
    """Get modalities with content, refreshing cache if needed."""
    global _cache_timestamp

    if time.time() - _cache_timestamp > _CACHE_TTL_SECONDS or not _modality_cache:
        _refresh_modality_cache()

    return _modality_cache


def _build_prompt(modalities: dict[str, ModalityInfo]) -> str:
    """Build the query analysis prompt with actual available modalities."""
    if not modalities:
        modality_section = "  (no content indexed yet)"
    else:
        lines = []
        for info in modalities.values():
            lines.append(f"  - {info.name}: {info.description}")
        modality_section = "\n".join(lines)

    return (
        textwrap.dedent("""
        Analyze this search query and extract structured information.

        The user is searching a personal knowledge base containing:
        {modality_section}

        Guidelines:
        - Only restrict modalities when VERY confident about content type
        - When unsure, return empty modalities to search all
        - Remove meta-language like "there was something about", "I remember reading"
        - For recalled_content: if you recognize the topic, suggest specific titles/essays
          that might be relevant (e.g., "predetermined conclusions" -> "The Bottom Line")
        - start_date/end_date: inclusive YYYY-MM-DD bounds on when the content was sent,
          published or happened, resolved against the current date given with the query.
          Set them only when the query names or implies a time window ("yesterday",
          "since Monday", "last week", "on 12 October", "in March", "today's meetings",
          "next sync", "upcoming"); otherwise null. Leave a side null when it is open
          ("since March" has no end_date). Weeks start on Monday. For future references
          ("next", "upcoming") start today and end a few weeks ahead. Vague recency
          ("latest", "recent") gets no range.
        - Drop time expressions from cleaned_query and query_variants once they are
          captured by the date range.
    """)
        .strip()
        .format(modality_section=modality_section)
    )


def _build_schema(modality_names: list[str]) -> dict:
    def strings(description: str) -> dict:
        return {
            "type": "array",
            "items": {"type": "string"},
            "description": description,
        }

    def optional_date(description: str) -> dict:
        return {
            "anyOf": [{"type": "string", "format": "date"}, {"type": "null"}],
            "description": description,
        }

    modalities = strings("Content types to search (empty = search all)")
    # An empty enum is an invalid schema
    if modality_names:
        modalities["items"]["enum"] = modality_names

    properties = {
        "modalities": modalities,
        "sources": strings("Specific sources/authors mentioned"),
        "cleaned_query": {
            "type": "string",
            "description": "Query with meta-language removed",
        },
        "query_variants": strings("1-3 alternative phrasings"),
        "recalled_content": strings(
            "Specific titles/essays/concepts you recall that match"
        ),
        "start_date": optional_date("First day of the time window the query refers to"),
        "end_date": optional_date("Last day of the time window the query refers to"),
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


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
    query: str,
    now: datetime,
    model: Optional[str] = None,
    timeout: float = 3.0,
) -> QueryAnalysis:
    """
    Analyze a search query using an LLM to extract search parameters.

    Args:
        query: The user's natural language search query
        now: Current time, which relative dates in the query are resolved against
        model: LLM model to use (defaults to SUMMARIZER_MODEL, ideally Haiku)
        timeout: Maximum time to wait for LLM response

    Returns:
        QueryAnalysis with extracted modalities, sources, cleaned query, and variants
    """
    # Relative dates resolve differently each day
    cache_key = f"{now.astimezone(timezone.utc):%Y-%m-%d} {query.lower().strip()}"
    async with _get_cache_lock():
        if cache_key in _analysis_cache:
            logger.debug(f"Query analysis cache hit for: {query[:50]}...")
            # Move to end for LRU behavior (Python 3.7+ dicts maintain insertion order)
            result = _analysis_cache.pop(cache_key)
            _analysis_cache[cache_key] = result
            return result

    result = QueryAnalysis(cleaned_query=query)

    try:
        provider = create_provider(model=model or settings.SUMMARIZER_MODEL)
        # An expired cache refreshes with several DB queries; on the loop that
        # would stall every concurrent request.
        modalities = await asyncio.to_thread(_get_available_modalities)

        # The clock goes in the user message so the system prompt stays cacheable
        messages = [
            Message.user(text=f"Current date/time: {format_now(now)}\nQuery: {query}")
        ]

        llm_settings = LLMSettings(
            max_tokens=1024,  # headroom for thinking tokens
            json_schema=_build_schema(sorted(modalities)),
            # Prompt + schema is ~800-1000 tokens and stable for an hour
            # (modality cache TTL); searches come in bursts.
            cache_system_prompt=True,
        )

        response = await asyncio.wait_for(
            provider.agenerate(
                messages=messages,
                system_prompt=_build_prompt(modalities),
                settings=llm_settings,
            ),
            timeout=timeout,
        )

        if response:
            # A refusal can come back as non-JSON text
            try:
                data = json.loads(response)

                result.modalities = set(data["modalities"])
                result.sources = data["sources"]
                result.cleaned_query = data["cleaned_query"]
                result.query_variants = data["query_variants"]
                result.recalled_content = data["recalled_content"]
                result.start_date, result.end_date = date_range(
                    data["start_date"], data["end_date"]
                )
                result.success = True

                logger.debug(
                    f"Query analysis: '{query[:40]}...' -> "
                    f"modalities={result.modalities}, "
                    f"cleaned='{result.cleaned_query[:30]}...', "
                    f"recalled={result.recalled_content}, "
                    f"dates={result.start_date}..{result.end_date}"
                )

            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse query analysis JSON: {e}")

        # Cache the result (evicts oldest entries first - LRU since we move on access)
        async with _get_cache_lock():
            if len(_analysis_cache) >= _CACHE_MAX_SIZE:
                # Remove oldest half (front of dict = least recently used)
                keys_to_remove = list(_analysis_cache.keys())[: _CACHE_MAX_SIZE // 2]
                for key in keys_to_remove:
                    del _analysis_cache[key]
            _analysis_cache[cache_key] = result

    except asyncio.TimeoutError:
        logger.warning(f"Query analysis timed out for: {query[:50]}...")
        record_metric("search", "query_analysis", status="timeout", labels={"query_length": len(query)})
    except Exception as e:
        logger.error(f"Query analysis failed: {e}")
        record_metric("search", "query_analysis", status="error", labels={"error": type(e).__name__})

    return result
