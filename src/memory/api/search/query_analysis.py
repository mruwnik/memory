"""
LLM-based query analysis for intelligent search preprocessing.

Uses a fast LLM (Haiku) to analyze natural language queries and extract:
- Modalities: content types to search (forum, book, comic, etc.)
- Source hints: author names, domains, or specific sources
- Cleaned query: the actual search terms with meta-language removed
- Query variants: alternative phrasings to search
- Recalled content: specific titles/essays the LLM recalls that match the query

This runs in parallel with HyDE for maximum efficiency.
"""

import asyncio
import json
import logging
import re
import textwrap
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from sqlalchemy import func, text
from sqlalchemy.exc import SQLAlchemyError

from memory.common import settings
from memory.common.db.connection import make_session
from memory.common.db.models import SourceItem
from memory.common.llms import create_provider, LLMSettings, Message

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

            _modality_cache = {}
            for modality, count in results:
                if modality and count > 0:
                    _modality_cache[modality] = ModalityInfo(
                        name=modality,
                        count=count,
                        domains=modality_domains.get(modality, []),
                        source_count=source_counts.get(modality),
                    )

            _cache_timestamp = time.time()
            logger.debug(f"Refreshed modality cache: {list(_modality_cache.keys())}")

    except SQLAlchemyError as e:
        logger.warning(f"Database error refreshing modality cache: {e}")


def _get_available_modalities() -> dict[str, ModalityInfo]:
    """Get modalities with content, refreshing cache if needed."""
    global _cache_timestamp

    if time.time() - _cache_timestamp > _CACHE_TTL_SECONDS or not _modality_cache:
        _refresh_modality_cache()

    return _modality_cache


def _build_prompt() -> str:
    """Build the query analysis prompt with actual available modalities."""
    modalities = _get_available_modalities()

    if not modalities:
        modality_section = "  (no content indexed yet)"
    else:
        lines = []
        for info in modalities.values():
            lines.append(f"  - {info.name}: {info.description}")
        modality_section = "\n".join(lines)

    modality_names = list(modalities.keys()) if modalities else []

    return (
        textwrap.dedent("""
        Analyze this search query and extract structured information.

        The user is searching a personal knowledge base containing:
        {modality_section}

        Return a JSON object:
        {{
          "modalities": [],  // From: {modality_names} (empty = search all)
          "sources": [],  // Specific sources/authors mentioned
          "cleaned_query": "",  // Query with meta-language removed
          "query_variants": [],  // 1-3 alternative phrasings
          "recalled_content": []  // Specific titles/essays/concepts you recall that match
        }}

        Guidelines:
        - Only restrict modalities when VERY confident about content type
        - When unsure, return empty modalities to search all
        - Remove meta-language like "there was something about", "I remember reading"
        - For recalled_content: if you recognize the topic, suggest specific titles/essays
          that might be relevant (e.g., "predetermined conclusions" -> "The Bottom Line")

        Return ONLY valid JSON.
    """)
        .strip()
        .format(
            modality_section=modality_section,
            modality_names=modality_names,
        )
    )


@dataclass
class QueryAnalysis:
    """Result of LLM-based query analysis."""

    modalities: set[str] = field(default_factory=set)
    sources: list[str] = field(default_factory=list)
    cleaned_query: str = ""
    query_variants: list[str] = field(default_factory=list)
    recalled_content: list[str] = field(default_factory=list)  # Titles/essays LLM recalls
    success: bool = False


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
    model: Optional[str] = None,
    timeout: float = 3.0,
) -> QueryAnalysis:
    """
    Analyze a search query using an LLM to extract search parameters.

    Args:
        query: The user's natural language search query
        model: LLM model to use (defaults to SUMMARIZER_MODEL, ideally Haiku)
        timeout: Maximum time to wait for LLM response

    Returns:
        QueryAnalysis with extracted modalities, sources, cleaned query, and variants
    """
    # Check cache first
    cache_key = query.lower().strip()
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

        messages = [Message.user(text=f"Query: {query}")]

        llm_settings = LLMSettings(
            temperature=0.1,  # Low temperature for consistent structured output
            max_tokens=300,
        )

        response = await asyncio.wait_for(
            provider.agenerate(
                messages=messages,
                system_prompt=_build_prompt(),
                settings=llm_settings,
            ),
            timeout=timeout,
        )

        if response:
            # Parse JSON response
            response = response.strip()
            # Handle markdown code blocks
            if response.startswith("```"):
                response = response.split("```")[1]
                if response.startswith("json"):
                    response = response[4:]
                response = response.strip()

            try:
                data = json.loads(response)

                result.modalities = set(data.get("modalities", []))
                result.sources = data.get("sources", [])
                result.cleaned_query = data.get("cleaned_query", query)
                result.query_variants = data.get("query_variants", [])
                result.recalled_content = data.get("recalled_content", [])
                result.success = True

                logger.debug(
                    f"Query analysis: '{query[:40]}...' -> "
                    f"modalities={result.modalities}, "
                    f"cleaned='{result.cleaned_query[:30]}...', "
                    f"recalled={result.recalled_content}"
                )

            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse query analysis JSON: {e}")
                result.cleaned_query = query

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
    except Exception as e:
        logger.error(f"Query analysis failed: {e}")

    return result
