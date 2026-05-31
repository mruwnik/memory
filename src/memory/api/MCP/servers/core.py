"""
Core MCP subserver for knowledge base search and observations.
"""

import base64
import logging
import textwrap
from datetime import datetime, timezone
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from pydantic import BaseModel
from sqlalchemy import Text, exists, func, or_, select
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import selectinload

from memory.api.MCP.access import (
    build_user_access_filter_from_dict,
    get_mcp_current_user,
    get_project_roles_by_user_id,
    log_item_access,
    log_search_access,
)
from memory.api.MCP.visibility import has_items, require_scopes, visible_when
from memory.common.access_control import (
    AccessFilter,
    apply_access_filter_to_query,
    get_accessible_source_item_by_filename,
    user_can_access,
)
from memory.common.scopes import (
    SCOPE_OBSERVE,
    SCOPE_OBSERVE_WRITE,
    SCOPE_READ,
)
from memory.api.auth import lookup_api_key
from memory.api.search.filters import (
    FILTER_REGISTRY,
    apply_registry_filters_sql,
    reject_unknown_filter_keys,
)
from memory.api.search.search import search as search_base
from memory.api.search.types import MCPSearchFilters, SearchConfig, SearchFilters
from memory.common import extract, paths, settings
from memory.common.celery_app import SYNC_OBSERVATION
from memory.common.celery_app import app as celery_app
from memory.common.collections import ALL_COLLECTIONS, OBSERVATION_COLLECTIONS
from memory.common.db.connection import make_session
from memory.common.db.models import (
    AgentObservation,
    SourceItem,
    UserSession,
)
from memory.common.db.models.source_item import source_item_people
from memory.common.db.models.journal import JournalEntry, build_journal_access_filter
from memory.common.formatters import observation


logger = logging.getLogger(__name__)


def get_current_user_access_filter() -> AccessFilter | None:
    """
    Get access filter for the current MCP user.

    Returns:
        AccessFilter with user's project access conditions,
        or None if the principal is effectively superadmin.

    Scope source: the *access token's* scopes, which are the resolved
    set already produced by ``SimpleOAuthProvider.verify_token`` /
    ``load_access_token``:

      - For UserSession tokens: ``user.scopes ∪ {read, write}`` —
        admin users keep their ``*``, so admin-via-session still
        bypasses the filter.
      - For APIKey tokens: ``api_key.scopes`` if the override is set,
        else ``user.scopes``. An admin who mints a ``[\"read\"]``-scoped
        integration key therefore gets a non-None AccessFilter for
        that key — the ``APIKey.scopes`` override is honoured at the
        data layer (not just at the visibility / OAuth-gate layer), so
        a leaked least-privilege integration key cannot inherit the
        underlying user's full data access.
    """
    access_token = get_access_token()
    if access_token is None:
        # No auth - return empty filter (no access)
        logger.warning("get_current_user_access_filter: no access token")
        return AccessFilter(conditions=[])

    token_scopes = list(access_token.scopes or [])

    with make_session() as session:
        # Try as session token first
        user_session = session.get(UserSession, access_token.token)
        if user_session and user_session.user:
            return build_user_access_filter_from_dict(
                {"id": user_session.user.id, "scopes": token_scopes}
            )

        # Try as API key
        api_key_record = lookup_api_key(access_token.token, session)
        if api_key_record and api_key_record.user:
            return build_user_access_filter_from_dict(
                {"id": api_key_record.user.id, "scopes": token_scopes}
            )

    # Couldn't identify user - return empty filter (no access)
    logger.warning("get_current_user_access_filter: couldn't identify user from token")
    return AccessFilter(conditions=[])


# Filter definitions: (name, description, applicable_modalities)
# applicable_modalities is None for "all modalities"
SEARCH_FILTERS: list[tuple[str, str, str | None]] = [
    ("tags", "list of tags to filter by", None),
    ("source_ids", "list of source ids to filter by", None),
    ("min_size", "minimum content size in bytes", None),
    ("max_size", "maximum content size in bytes", None),
    ("min_created_at", "minimum created date, ISO format", None),
    ("max_created_at", "maximum created date, ISO format", None),
    ("min_sent_at", "minimum email sent date, ISO format", "mail"),
    ("max_sent_at", "maximum email sent date, ISO format", "mail"),
    ("min_published", "minimum publication date, ISO format", "blog, forum"),
    ("max_published", "maximum publication date, ISO format", "blog, forum"),
    ("folder_path", "Google Drive folder path filter", "doc"),
    ("sender", "exact match on email sender address", "mail"),
    ("domain", "exact match on website domain", "blog"),
    ("author", "exact match on author name", None),
    ("recipients", "list of email recipients to match", "mail"),
    ("authors", "list of authors to match", None),
]


def _get_available_modalities() -> list[str]:
    """Query database to find which modalities have indexed items."""
    searchable = set(ALL_COLLECTIONS.keys()) - OBSERVATION_COLLECTIONS
    try:
        with make_session() as session:
            from sqlalchemy import func as sql_func

            result = (
                session.query(SourceItem.modality)
                .filter(
                    SourceItem.embed_status == "STORED",
                    SourceItem.modality.in_(searchable),
                )
                .group_by(SourceItem.modality)
                .having(sql_func.count(SourceItem.id) > 0)
                .all()
            )
            return sorted([row[0] for row in result])
    except Exception as e:
        logger.warning(f"Failed to query available modalities: {e}")
        return sorted(searchable)


def _build_filters_section() -> str:
    """Build the filters documentation section.

    Uses 8 spaces for bullet indent because .format() substitution
    happens after textwrap.dedent processes the template.
    """
    bullet_indent = " " * 8
    lines = ["filters: Optional dictionary with:"]
    for name, desc, modalities in SEARCH_FILTERS:
        scope = f"({modalities} only)" if modalities else "(all modalities)"
        lines.append(f"{bullet_indent}- {name}: {desc} {scope}")
    return "\n".join(lines)


def _build_search_description() -> str:
    """Build dynamic description for search_knowledge_base tool."""
    modalities = _get_available_modalities()
    modalities_str = ", ".join(modalities) if modalities else "(none available)"

    return textwrap.dedent(
        """
        Search user's stored content including emails, documents, articles, books.
        Use to find specific information the user has saved or received.
        Combine with search_observations for complete user context.
        Use the `get_metadata_schemas` tool to get the metadata schema for each collection.

        If you know what kind of data you're looking for, filter by modality for better results.

        Args:
            query: Natural language search query - be descriptive about what you're looking for
            modalities: Filter by type: {modalities_str} (empty = all)
            limit: Maximum number of results to return (default 20, max 100)
            previews: Whether to include content in results (up to MAX_PREVIEW_LENGTH characters)
            use_scores: Whether to score results with an LLM before returning - better but slower
            {filters_section}

        Returns: List of search results with id, score, chunks, content, filename
        Higher scores (>0.7) indicate strong matches."""
    ).format(filters_section=_build_filters_section(), modalities_str=modalities_str)


core_mcp = FastMCP("memory-core")


def filter_observation_source_ids(
    tags: list[str] | None = None, observation_types: list[str] | None = None
):
    if not tags and not observation_types:
        return None

    with make_session() as session:
        items_query = session.query(AgentObservation.id)

        if tags:
            items_query = items_query.filter(
                AgentObservation.tags.op("&&")(sql_cast(tags, ARRAY(Text))),
            )
        if observation_types:
            items_query = items_query.filter(
                AgentObservation.observation_type.in_(observation_types)
            )
        source_ids = [item.id for item in items_query.all()]

    return source_ids


def filter_source_ids(modalities: set[str], filters: SearchFilters) -> list[int] | None:
    if source_ids := filters.get("source_ids"):
        return source_ids

    tags = filters.get("tags")
    min_size = filters.get("min_size")
    max_size = filters.get("max_size")
    if not (tags or min_size or max_size):
        return None

    with make_session() as session:
        items_query = session.query(SourceItem.id)

        if tags:
            items_query = items_query.filter(
                SourceItem.tags.op("&&")(sql_cast(tags, ARRAY(Text))),
            )
        if min_size is not None:
            items_query = items_query.filter(SourceItem.size >= min_size)
        if max_size is not None:
            items_query = items_query.filter(SourceItem.size <= max_size)
        if modalities:
            items_query = items_query.filter(SourceItem.modality.in_(modalities))
        source_ids = [item.id for item in items_query.all()]

    return source_ids


@core_mcp.tool(description=_build_search_description())
@visible_when(require_scopes(SCOPE_READ))
async def search(
    query: str,
    filters: MCPSearchFilters = {},
    modalities: set[str] = set(),
    limit: int = 20,
    previews: bool = False,
    use_scores: bool = False,
) -> list[dict]:
    logger.info(f"MCP search for: {query}")
    config = SearchConfig(
        limit=min(limit, 100), previews=previews, useScores=use_scores
    )

    if not modalities:
        modalities = set(ALL_COLLECTIONS.keys())
    modalities = (set(modalities) & ALL_COLLECTIONS.keys()) - OBSERVATION_COLLECTIONS

    search_filters = SearchFilters(**filters)
    search_filters["source_ids"] = filter_source_ids(modalities, search_filters)

    # Apply access control filter
    access_filter = get_current_user_access_filter()
    search_filters["access_filter"] = access_filter

    upload_data = extract.extract_text(query, skip_summary=True)
    results = await search_base(
        upload_data,
        modalities=modalities,
        filters=search_filters,
        config=config,
    )

    # Audit-log the search. The AccessLog docstring (and access_control.py:13)
    # claim "all access is logged, including superadmin access" — that's only
    # true if we actually call log_search_access. Logged best-effort: a logging
    # failure must not fail the user's search.
    user = get_mcp_current_user()
    user_id = getattr(user, "id", None) if user else None
    if user_id is not None:
        try:
            log_search_access(user_id, query, len(results))
        except Exception:
            logger.exception("log_search_access failed for user_id=%s", user_id)

    return [result.model_dump() for result in results]


class RawObservation(BaseModel):
    subject: str
    content: str
    observation_type: str = "general"
    confidences: dict[str, float] = {}
    evidence: dict | None = None
    tags: list[str] = []


@core_mcp.tool()
@visible_when(require_scopes(SCOPE_OBSERVE_WRITE))
async def observe(
    observations: list[RawObservation],
    session_id: str | None = None,
    agent_model: str = "unknown",
) -> dict:
    """
    Record observations about the user for long-term understanding.
    Use proactively when user expresses preferences, behaviors, beliefs, or contradictions.
    Be specific and detailed - observations should make sense months later.

    Example call:
    ```
    {
        "observations": [
            {
                "content": "The user is a software engineer.",
                "subject": "user",
                "observation_type": "belief",
                "confidences": {"observation_accuracy": 0.9},
                "evidence": {"quote": "I am a software engineer.", "context": "I work at Google."},
                "tags": ["programming", "work"]
            }
        ],
        "session_id": "123e4567-e89b-12d3-a456-426614174000",
        "agent_model": "gpt-4o"
    }
    ```

    RawObservation fields:
        content (required): Detailed observation text explaining what you observed
        subject (required): Consistent identifier like "programming_style", "work_habits"
        observation_type: belief, preference, behavior, contradiction, general
        confidences: Dict of scores (0.0-1.0), e.g. {"observation_accuracy": 0.9}
        evidence: Context dict with extra context, e.g. "quote" (exact words) and "context" (situation)
        tags: List of categorization tags for organization

    Args:
        observations: List of RawObservation objects (max 50 per call)
        session_id: UUID to group observations from same conversation
        agent_model: AI model making observations (for quality tracking)
    """
    MAX_OBSERVATIONS_PER_CALL = 50

    if len(observations) > MAX_OBSERVATIONS_PER_CALL:
        return {
            "error": f"Too many observations: {len(observations)} exceeds limit of {MAX_OBSERVATIONS_PER_CALL}",
            "status": "rejected",
        }

    # Resolve caller for ownership tagging. Without ``creator_id`` the
    # row defaults to NULL project_id + "basic" sensitivity, which the
    # access-control layer treats as superadmin-only — so a non-admin
    # user calling observe() then search_observations() would see zero
    # of their own writes (silent data loss), while every admin in the
    # deployment would see every observation across users.
    user = get_mcp_current_user()
    creator_id = user.id if user else None
    if creator_id is None:
        # observe() requires SCOPE_OBSERVE_WRITE which gates on an
        # authenticated principal; reaching this branch means the
        # auth context drift between visibility and tool execution
        # leaked through. Refuse rather than mint an unowned row.
        return {
            "error": "observe requires an authenticated user",
            "status": "rejected",
        }

    logger.info(f"MCP: Observing {len(observations)} observation(s)")
    tasks = [
        (
            obs,
            celery_app.send_task(
                SYNC_OBSERVATION,
                queue=f"{settings.CELERY_QUEUE_PREFIX}-notes",
                kwargs={
                    "subject": obs.subject,
                    "content": obs.content,
                    "observation_type": obs.observation_type,
                    "confidences": obs.confidences,
                    "evidence": obs.evidence,
                    "tags": obs.tags,
                    "session_id": session_id,
                    "agent_model": agent_model,
                    "creator_id": creator_id,
                },
            ),
        )
        for obs in observations
    ]

    def short_content(obs: RawObservation) -> str:
        if len(obs.content) > 50:
            return obs.content[:47] + "..."
        return obs.content

    return {
        "task_ids": {short_content(obs): task.id for obs, task in tasks},
        "status": "queued",
    }


@core_mcp.tool()
@visible_when(require_scopes(SCOPE_OBSERVE), has_items(AgentObservation))
async def search_observations(
    query: str,
    subject: str = "",
    tags: list[str] | None = None,
    observation_types: list[str] | None = None,
    min_confidences: dict[str, float] = {},
    limit: int = 20,
) -> list[dict]:
    """
    Search recorded observations about the user.
    Use before responding to understand user preferences, patterns, and past insights.
    Search by meaning - the query matches both content and context.

    Args:
        query: Natural language search query describing what you're looking for
        subject: Filter by exact subject identifier (empty = search all subjects)
        tags: Filter by tags (must have at least one matching tag)
        observation_types: Filter by: belief, preference, behavior, contradiction, general
        min_confidences: Minimum confidence thresholds, e.g. {"observation_accuracy": 0.8}
        limit: Maximum number of results to return (default 20, max 100)

    Returns: List with content, tags, created_at, metadata
    Results sorted by relevance to your query.
    """
    logger.info("MCP: Searching observations for %s", query)
    config = SearchConfig(limit=min(limit, 100))
    semantic_text = observation.generate_semantic_text(
        subject=subject or "",
        observation_type="".join(observation_types or []),
        content=query,
        evidence=None,
    )
    temporal = observation.generate_temporal_text(
        subject=subject or "",
        content=query,
        created_at=datetime.now(timezone.utc),
    )
    # Apply access control filter for observations
    access_filter = get_current_user_access_filter()

    results = await search_base(
        [
            extract.DataChunk(data=[query]),
            extract.DataChunk(data=[semantic_text]),
            extract.DataChunk(data=[temporal]),
        ],
        modalities={"semantic", "temporal"},
        filters=SearchFilters(
            subject=subject,
            min_confidences=min_confidences,
            tags=tags or [],
            observation_types=observation_types,
            source_ids=filter_observation_source_ids(tags=tags),
            access_filter=access_filter,
        ),
        config=config,
    )

    # Audit-log the observation search. Best-effort.
    user = get_mcp_current_user()
    user_id = getattr(user, "id", None) if user else None
    if user_id is not None:
        try:
            log_search_access(user_id, query, len(results))
        except Exception:
            logger.exception(
                "log_search_access failed for user_id=%s in search_observations",
                user_id,
            )

    return [
        {
            "content": r.content,
            "tags": r.tags,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "metadata": r.metadata,
        }
        for r in results
    ]


# Cap on inline file fetches. A base64'd response much larger than this
# overruns the MCP transport and drops the client session, so reject oversize
# files with a clear error instead of silently killing the connection.
MAX_FETCH_FILE_BYTES = 10 * 1024 * 1024


@core_mcp.tool()
@visible_when(require_scopes(SCOPE_READ))
def fetch_file(filename: str) -> dict:
    """
    Read file content with automatic type detection.
    Returns dict with content, mime_type, is_text, file_size.
    Text content as string, binary as base64.
    """
    try:
        path = paths.validate_path_within_directory(
            settings.FILE_STORAGE_DIR, filename.strip(), require_exists=True
        )
    except ValueError as e:
        raise ValueError(f"Invalid path: {e}")

    logger.debug(f"Fetching file: {path}")
    if not path.exists():
        raise FileNotFoundError(f"File not found: {filename}")

    # Ownership check: look up the SourceItem that owns this file and verify access.
    # Without this, any SCOPE_READ user could read any file in FILE_STORAGE_DIR.
    try:
        relative = paths.to_db_filename(path)
    except ValueError:
        raise FileNotFoundError(f"File not found: {filename}")

    user = get_mcp_current_user()
    if user is None:
        raise PermissionError(f"Access denied: {filename}")

    with make_session() as session:
        try:
            get_accessible_source_item_by_filename(session, user, relative)
        except FileNotFoundError:
            raise FileNotFoundError(f"File not found: {filename}")
        except PermissionError:
            raise PermissionError(f"Access denied: {filename}")

    mime_type = extract.get_mime_type(path)

    # Text files: return raw content without chunking to preserve formatting
    if extract.is_text_file(path):
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            text = path.read_text(errors="replace")
        return {
            "content": [{"type": "text", "mime_type": mime_type, "data": text}]
        }

    # Non-text files: return the actual file bytes. Serving the stored file is
    # this tool's job; extracting or rasterizing content (the domain of
    # extract.extract_data_chunks) belongs to the embedding pipeline and would
    # hand back a derived representation rather than the file the caller asked
    # for.
    raw = path.read_bytes()
    if len(raw) > MAX_FETCH_FILE_BYTES:
        raise ValueError(
            f"File too large to fetch inline: {len(raw)} bytes "
            f"(limit {MAX_FETCH_FILE_BYTES})."
        )
    return {
        "content": [
            {
                "type": "image" if mime_type.startswith("image/") else "blob",
                "mime_type": mime_type,
                "data": base64.b64encode(raw).decode("ascii"),
            }
        ]
    }


# --- Enumeration tools for systematic investigations ---


@core_mcp.tool()
@visible_when(require_scopes(SCOPE_READ))
async def fetch(
    id: int | None = None,
    ids: list[int] | None = None,
    include_content: bool = True,
    include_journal: bool = False,
) -> dict | list[dict]:
    """
    Get full details of source item(s) by ID.
    Use after search to drill down into specific results.

    Args:
        id: Single source item ID (from search results)
        ids: Multiple source item IDs for bulk fetch (max 200)
        include_content: Whether to include full content (default True)
        include_journal: Whether to include journal entries (default False)

    Returns: Full item details (single dict if id, list of dicts if ids).

    Access control behaviour differs by mode:
        - Single id: raises ValueError if the item is not found or access is denied.
        - Bulk ids: silently omits inaccessible/missing items and returns a shorter list.
    """
    if id is not None and ids is not None:
        raise ValueError("Cannot provide both 'id' and 'ids' — use one or the other")
    if id is None and ids is None:
        raise ValueError("Must provide either 'id' or 'ids'")

    fetch_ids = list(dict.fromkeys(ids if ids is not None else [id]))  # dedup, preserving order
    if len(fetch_ids) > 200:
        raise ValueError(f"Cannot fetch more than 200 items at once (got {len(fetch_ids)})")

    # Get access filter and user info BEFORE opening session to avoid nested session issues
    access_filter = get_current_user_access_filter()
    user = get_mcp_current_user()
    user_id: int | None = getattr(user, "id", None) if user else None

    # Fetch project_roles before opening main session (creates its own session)
    project_roles: dict[int, str] | None = None
    if access_filter is not None and user_id is not None:
        project_roles = get_project_roles_by_user_id(user_id)

    with make_session() as session:
        # Eager load 'people' relationship since as_payload() accesses it
        items = (
            session.query(SourceItem)
            .options(selectinload(SourceItem.people))
            .filter(
                SourceItem.id.in_(fetch_ids),
                SourceItem.embed_status == "STORED",
            )
            .all()
        )

        # Apply access control
        if access_filter is not None:
            if user is None or user_id is None:
                items = []
            else:
                items = [i for i in items if user_can_access(user, i, project_roles)]

        # Bulk fetch journal entries in one query if requested
        journal_by_item: dict[int, list[dict]] = {}
        if include_journal and items:
            item_ids = [i.id for i in items]
            journal_query = (
                session.query(JournalEntry)
                .filter(
                    JournalEntry.target_type == "source_item",
                    JournalEntry.target_id.in_(item_ids),
                )
            )
            if user is not None:
                journal_filter = build_journal_access_filter(user, user_id)
                if journal_filter is not True:
                    journal_query = journal_query.filter(journal_filter)
            for entry in journal_query.order_by(JournalEntry.created_at.asc()).all():
                journal_by_item.setdefault(entry.target_id, []).append(entry.as_payload())

        # Build result dicts indexed by item ID for order preservation
        results_by_id: dict[int, dict] = {}
        for item in items:
            result: dict[str, Any] = {
                "id": item.id,
                "modality": item.modality,
                "title": item.title,
                "mime_type": item.mime_type,
                "filename": item.filename,
                "size": item.size,
                "tags": item.tags,
                "inserted_at": item.inserted_at.isoformat() if item.inserted_at else None,
                "metadata": item.as_payload(),
            }
            if include_content:
                result["content"] = item.content
            if include_journal:
                result["journal_entries"] = journal_by_item.get(item.id, [])
            results_by_id[item.id] = result

        # Single-id mode: return dict (backward compatible)
        if id is not None and ids is None:
            if not results_by_id:
                raise ValueError(f"Item {id} not found or not yet indexed")
            # Audit-log the access. Best-effort: a logging failure must not
            # fail the user's fetch.
            if user_id is not None:
                try:
                    log_item_access(user_id, id)
                except Exception:
                    logger.exception(
                        "log_item_access failed for user_id=%s item_id=%s",
                        user_id,
                        id,
                    )
            return results_by_id[id]

        # Audit-log every item the caller actually got (post-access-check).
        if user_id is not None:
            for accessed_id in results_by_id:
                try:
                    log_item_access(user_id, accessed_id)
                except Exception:
                    logger.exception(
                        "log_item_access failed for user_id=%s item_id=%s "
                        "in bulk fetch",
                        user_id,
                        accessed_id,
                    )

        # Return results in the same order as the input ids
        return [results_by_id[i] for i in fetch_ids if i in results_by_id]


def apply_access_control_to_query(query, access_filter: AccessFilter | None, session):
    """Backwards-compatible wrapper around :func:`apply_access_filter_to_query`.

    The ``session`` argument is unused — the canonical helper builds an
    ``EXISTS`` subquery for the person-override condition rather than running
    a separate ``SELECT``.  Kept here for callers that already pass a session
    so they don't have to change.
    """
    del session  # unused; the canonical helper doesn't need it
    return apply_access_filter_to_query(query, access_filter)


# Special filter keys that item enumeration handles directly (not via the
# shared FILTER_REGISTRY). created_at is scoped to SourceItem.inserted_at here
# — distinct from BM25's Chunk.created_at, see SPECIAL_FILTER_KEYS in
# search.filters. Everything else (registry keys) is folded on declaratively.
ITEM_SPECIAL_FILTER_KEYS = frozenset(
    {"source_ids", "min_created_at", "max_created_at", "person_id"}
)

# Keys accepted by item enumeration: the declarative registry plus the special
# keys handled inline below. observation_types/min_confidences are observation-
# search concepts (AgentObservation columns / ConfidenceScore), handled by
# filter_observation_source_ids on its own query — they have no meaning for a
# SourceItem enumeration, so they are NOT accepted here and reject loudly rather
# than silently returning an unfiltered count. Any other non-empty key is
# likewise rejected so list_items and count_items can never disagree about a
# filter one of them forgot.
ITEM_ALLOWED_FILTER_KEYS = set(FILTER_REGISTRY) | ITEM_SPECIAL_FILTER_KEYS


def apply_item_filters(query, modalities: set[str], filters: MCPSearchFilters):
    """Apply modality + MCPSearchFilters constraints to a SourceItem query.

    Shared by list_items and count_items so the two can never drift. Registry
    filters (mail/blog/doc metadata) are folded on via
    :func:`apply_registry_filters_sql`, which joins each joined-inheritance
    subclass table on ``SourceItem.id``. A query that combines filters from two
    different subclasses can never match a row (an item is exactly one
    subclass); that is the correct outcome.

    Any provided filter key not in :data:`ITEM_ALLOWED_FILTER_KEYS` raises
    ValueError instead of being silently dropped.
    """
    reject_unknown_filter_keys(filters, allowed=ITEM_ALLOWED_FILTER_KEYS)

    if modalities:
        query = query.filter(SourceItem.modality.in_(modalities))

    # Special keys scoped to SourceItem itself.
    if source_ids := filters.get("source_ids"):
        query = query.filter(SourceItem.id.in_(source_ids))
    if min_created_at := filters.get("min_created_at"):
        query = query.filter(SourceItem.inserted_at >= min_created_at)
    if max_created_at := filters.get("max_created_at"):
        query = query.filter(SourceItem.inserted_at <= max_created_at)

    # Declarative content-metadata filters (tags/size/mail/blog/doc).
    query = apply_registry_filters_sql(query, filters)

    if (person_id := filters.get("person_id")) is not None:
        person_associated = exists(
            select(source_item_people.c.source_item_id)
            .where(source_item_people.c.source_item_id == SourceItem.id)
            .where(source_item_people.c.person_id == person_id)
        )
        no_people = ~exists(
            select(source_item_people.c.source_item_id).where(
                source_item_people.c.source_item_id == SourceItem.id
            )
        )
        query = query.filter(or_(no_people, person_associated))

    return query


@core_mcp.tool()
@visible_when(require_scopes(SCOPE_READ))
async def list_items(
    modalities: set[str] = set(),
    filters: MCPSearchFilters = {},
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "inserted_at",
    sort_order: str = "desc",
    include_metadata: bool = True,
) -> dict:
    """
    List items without semantic search - for systematic enumeration.
    Use for reviewing all items matching criteria, not finding best matches.

    Args:
        modalities: Filter by type: mail, blog, book, forum, photo, comic, etc. (empty = all)
        filters: Same content-metadata filters as search_knowledge_base (tags,
            min_size, max_size, sender, recipients, subject, sent_at, etc.).
            Observation-only filters (observation_types, min_confidences) are
            rejected here — use search_observations for those.
        limit: Max results per page (default 50, max 200)
        offset: Skip first N results for pagination
        sort_by: Sort field - "inserted_at", "size", or "id" (default: inserted_at)
        sort_order: "asc" or "desc" (default: desc)
        include_metadata: Include full as_payload() metadata (default True)

    Returns: {items: [...], total: int, has_more: bool}
    """
    limit = min(limit, 200)
    if sort_by not in ("inserted_at", "size", "id"):
        sort_by = "inserted_at"
    if sort_order not in ("asc", "desc"):
        sort_order = "desc"

    # Get access filter for current user
    access_filter = get_current_user_access_filter()

    with make_session() as session:
        query = session.query(SourceItem).filter(SourceItem.embed_status == "STORED")
        query = apply_access_control_to_query(query, access_filter, session)
        query = apply_item_filters(query, modalities, filters)

        # Get total count
        total = query.count()

        # Apply sorting
        sort_column = getattr(SourceItem, sort_by)
        if sort_order == "desc":
            sort_column = sort_column.desc()
        query = query.order_by(sort_column)

        # Apply pagination
        query = query.offset(offset).limit(limit)

        items = []
        for item in query.all():
            preview = item.preview_text

            item_dict = {
                "id": item.id,
                "modality": item.modality,
                "title": item.title,
                "mime_type": item.mime_type,
                "filename": item.filename,
                "size": item.size,
                "tags": item.tags,
                "inserted_at": item.inserted_at.isoformat()
                if item.inserted_at
                else None,
                "preview": preview,
            }

            if include_metadata:
                item_dict["metadata"] = item.as_payload()
            else:
                item_dict["metadata"] = None

            items.append(item_dict)

        return {
            "items": items,
            "total": total,
            "has_more": offset + len(items) < total,
        }


@core_mcp.tool()
@visible_when(require_scopes(SCOPE_READ))
async def count_items(
    modalities: set[str] = set(),
    filters: MCPSearchFilters = {},
) -> dict:
    """
    Count items matching criteria without retrieving them.
    Use to understand scope before systematic review.

    Args:
        modalities: Filter by type (empty = all)
        filters: Same content-metadata filters as search_knowledge_base.
            Observation-only filters (observation_types, min_confidences) are
            rejected here — use search_observations for those. count_items and
            list_items apply the identical filter set, so their totals always agree.

    Returns: {total: int, by_modality: {mail: 100, blog: 50, ...}}
    """
    # Get access filter for current user
    access_filter = get_current_user_access_filter()

    with make_session() as session:
        base_query = session.query(SourceItem).filter(
            SourceItem.embed_status == "STORED"
        )
        base_query = apply_access_control_to_query(base_query, access_filter, session)
        base_query = apply_item_filters(base_query, modalities, filters)

        # Get total
        total = base_query.count()

        # Get counts by modality on the same filtered query
        by_modality_query = base_query.with_entities(
            SourceItem.modality, func.count(SourceItem.id)
        ).group_by(SourceItem.modality)

        by_modality = {row[0]: row[1] for row in by_modality_query.all()}

        return {
            "total": total,
            "by_modality": by_modality,
        }
