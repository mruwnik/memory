"""
Core MCP subserver for knowledge base search and observations.
"""

import base64
import json
import logging
import textwrap
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastmcp import FastMCP
from pydantic import BaseModel
from sqlalchemy import Text, exists, func, or_, select
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import selectinload

from memory.agents.subscription_input import new_tag, preamble, untrusted
from memory.api.MCP.access import (
    build_mcp_user_access_filter,
    fetch_accessible_source_items,
    get_mcp_current_user,
    log_item_access,
    log_search_access,
)
from memory.api.MCP.visibility import READ_ONLY, has_items, require_scopes, visible_when
from memory.common.access_control import (
    AccessFilter,
    apply_access_filter_to_query,
    get_accessible_source_item_by_filename,
)
from memory.common.scopes import (
    SCOPE_OBSERVE,
    SCOPE_OBSERVE_WRITE,
    SCOPE_READ,
)
from memory.api.search.filters import (
    FILTER_REGISTRY,
    account_match_sql,
    beeper_match_sql,
    apply_registry_filters_sql,
    reject_unknown_filter_keys,
)
from memory.api.search.item_browse import (
    CONNECTIONS,
    EMBED_STATUSES,
    check_embed_statuses,
    connection_clause,
    connection_counts,
    connection_for,
    text_match_clause,
)
from memory.api.search.scope_filters import apply_scope_filters
from memory.api.search.search import search as search_base
from memory.api.search.types import MCPSearchFilters, SearchConfig, SearchFilters
from memory.common import extract, paths, settings
from memory.common.celery_app import SYNC_OBSERVATION
from memory.common.celery_app import app as celery_app
from memory.common.collections import (
    ALL_COLLECTIONS,
    OBSERVATION_COLLECTIONS,
    SESSION_COLLECTIONS,
)
from memory.common.db.connection import make_session
from memory.common.db.models import AgentObservation, SourceItem
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

    Scope source: the access token's scopes (``credential_scopes``), so an
    admin's narrowly scoped API key gets a filter rather than admin access.
    """
    with make_session() as session:
        user = get_mcp_current_user(session)
        if user is None:
            logger.warning("get_current_user_access_filter: no authenticated user")
        return build_mcp_user_access_filter(session, user)


# Filter definitions: (name, description, applicable_modalities)
# applicable_modalities is None for "all modalities"
SEARCH_FILTERS: list[tuple[str, str, str | None]] = [
    ("tags", "list of tags to filter by", None),
    ("exclude_tags", "list of tags; items carrying any of them are left out", None),
    ("source_ids", "list of source ids to filter by", None),
    ("min_size", "minimum content size in bytes", None),
    ("max_size", "maximum content size in bytes", None),
    (
        "min_created_at",
        "earliest time the item was stored in memory, ISO format "
        "(since/until, where offered, bound when the content happened)",
        None,
    ),
    ("max_created_at", "latest time the item was stored in memory, ISO format", None),
    ("min_sent_at", "minimum email sent date, ISO format", "mail"),
    ("max_sent_at", "maximum email sent date, ISO format", "mail"),
    ("min_published", "minimum publication date, ISO format", "blog, forum, comic"),
    ("max_published", "maximum publication date, ISO format", "blog, forum, comic"),
    ("folder_path", "Google Drive folder path filter", "doc"),
    ("folder", "exact match on email folder name, e.g. INBOX", "mail"),
    (
        "sender",
        "case-insensitive substring match on email sender address "
        "(vector search needs the full address)",
        "mail",
    ),
    ("domain", "exact match on website domain", "blog"),
    ("author", "exact match on an author name (blog, comic, forum, GitHub, book)", None),
    (
        "recipients",
        "match email recipients by address substring, case-insensitive "
        "(vector search needs the full address)",
        "mail",
    ),
    (
        "account",
        "filter to mail ingested by the email account with this address "
        "(groups all aliases the account handled, incoming and sent)",
        "mail",
    ),
    ("authors", "list of author names; items by any of them match", None),
    ("network", "chat network, e.g. whatsapp, signal, slack (case-insensitive)", "beeper"),
    ("chat", "chat title or Beeper chat id (case-insensitive)", "beeper"),
]


# Observations and session transcripts have dedicated tools
# (search_observations, claude_session_search) with their own access
# semantics — generic search never touches them.
SEARCHABLE_COLLECTIONS = (
    set(ALL_COLLECTIONS) - OBSERVATION_COLLECTIONS - SESSION_COLLECTIONS
)


def _get_available_modalities() -> list[str]:
    """Query database to find which modalities have indexed items."""
    try:
        with make_session() as session:
            from sqlalchemy import func as sql_func

            result = (
                session.query(SourceItem.modality)
                .filter(
                    SourceItem.embed_status == "STORED",
                    SourceItem.modality.in_(SEARCHABLE_COLLECTIONS),
                )
                .group_by(SourceItem.modality)
                .having(sql_func.count(SourceItem.id) > 0)
                .all()
            )
            return sorted([row[0] for row in result])
    except Exception as e:
        logger.warning(f"Failed to query available modalities: {e}")
        return sorted(SEARCHABLE_COLLECTIONS)


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


# Substituted after dedent, so continuation lines carry the Args indent (4).
SCOPE_SECTION = "\n".join(
    " " * 4 * (i > 0) + line
    for i, line in enumerate(
        [
            "since / until: ISO date or datetime bounding when the content happened;",
            "    a date-only until includes that whole day (UTC)",
            "    (mail sent, message posted, issue opened, post published; else when stored)",
            "channel: Discord or Slack channel name (with or without #) or id, or Beeper chat title or id",
            "author: Discord/Slack/Beeper user name or id, GitHub login, blog/comic/forum author,",
            "    commit author, or part of a mail sender (case-insensitive)",
            "repo: GitHub repository as owner/name (issues, PRs, commits)",
        ]
    )
)

# Substituted after dedent, like SCOPE_SECTION.
BROWSE_SECTION = "\n".join(
    " " * 4 * (i > 0) + line
    for i, line in enumerate(
        [
            "item_types: Filter by item type, e.g. mail_message, discord_message, github_pr (empty = all)",
            "embed_statuses: Indexing statuses to include, from "
            + ", ".join(sorted(EMBED_STATUSES)),
            "    (default STORED: indexed items only; empty = every status)",
            "contains: Case-insensitive text matched against the filename and each type's",
            "    title, subject and author or sender name (not the body text)",
            "connection_id: Only items from this connection (as count_items by_connection",
            "    lists it); item_types must all come from one kind of connection:",
            "    " + ", ".join(sorted(CONNECTIONS)),
        ]
    )
)


def _build_list_items_description() -> str:
    """Build dynamic description for the list_items tool.

    Reuses :func:`_build_filters_section` (driven by SEARCH_FILTERS) so the
    accepted content-metadata filters are documented identically to
    search_knowledge_base instead of being hand-maintained. Mirrors how
    _build_search_description injects the same section.
    """
    return textwrap.dedent(
        """
        List items without semantic search - for systematic enumeration.
        Use for reviewing all items matching criteria, not finding best matches.

        Args:
            modalities: Filter by type: mail, blog, book, forum, photo, comic, etc. (empty = all)
            {browse_section}
            {filters_section}
            The observation-only min_confidences filter is rejected here - use
            core_search_observations for it.
            {scope_section}
            limit: Max results per page (default 50, max 200)
            offset: Skip first N results for pagination
            sort_by: Sort field - "inserted_at", "size", or "id" (default: inserted_at)
            sort_order: "asc" or "desc" (default: desc)
            include_metadata: Include each item's full metadata (default True)
            include_total: Count every match for total (default True); false skips
                that count, which is slow on large collections, and total is null

        Returns: {{untrusted, items: [...], total: int | null, has_more: bool}}. Each item has
            id, modality, type, embed_status, title, mime_type, filename, size, tags,
            inserted_at, preview and metadata. untrusted names the tag of the blocks
            that hold text people wrote; tags and metadata are JSON in a block whose
            field ends in .json."""
    ).format(
        browse_section=BROWSE_SECTION,
        filters_section=_build_filters_section(),
        scope_section=SCOPE_SECTION,
    )


def _build_count_items_description() -> str:
    """Build dynamic description for the count_items tool.

    Reuses :func:`_build_filters_section` so the documented filters always match
    list_items and search_knowledge_base (the three share the same filter set).
    """
    return textwrap.dedent(
        """
        Count items matching criteria without retrieving them.
        Use to understand scope before systematic review.

        Args:
            modalities: Filter by type (empty = all)
            {browse_section}
            {filters_section}
            The observation-only min_confidences filter is rejected here - use
            core_search_observations for it. count_items and
            list_items apply the identical filter set, so their totals always agree.
            {scope_section}

        Returns: {{total: int, by_modality: {{mail: 100, ...}}, by_type: {{mail_message: 100, ...}},
            by_connection}}. by_connection is null unless item_types all come from one
            kind of connection; then it lists {{id, name, count}} per connection, largest
            first, and an id from it is a valid connection_id."""
    ).format(
        browse_section=BROWSE_SECTION,
        filters_section=_build_filters_section(),
        scope_section=SCOPE_SECTION,
    )


def _build_search_description() -> str:
    """Build dynamic description for search_knowledge_base tool."""
    modalities = _get_available_modalities()
    modalities_str = ", ".join(modalities) if modalities else "(none available)"

    return textwrap.dedent(
        """
        Search user's stored content including emails, documents, articles, books.
        Use to find specific information the user has saved or received.
        Observations about the user are not included; use core_search_observations for those.
        meta_get_metadata_schemas gives the metadata schema for each collection.

        If you know what kind of data you're looking for, filter by modality for better results.

        Args:
            query: Natural language search query - be descriptive about what you're looking for
            modalities: Filter by type: {modalities_str} (empty = all)
            limit: Maximum number of results to return (default 20, max 100)
            previews: Return up to {preview_length} characters of each item's content instead of {non_preview_length}
            use_scores: Whether to score results with an LLM before returning - better but slower
            use_bm25, use_hyde, use_reranking, use_query_analysis: Set false to skip a search enhancement. meta_get_search_defaults lists the ones available to you; true cannot enable one the server turned off or that has no key of yours to pay for it. Its semantic_search_unavailable is true while the server has no embeddings key, and search then matches keywords only
            {filters_section}

        Returns: {{untrusted, results}}. untrusted names the tag of the blocks that
            hold text people wrote; chunks, tags and metadata are JSON in a block
            whose field ends in .json. Each result has id, chunks (matching excerpts),
            size, mime_type, content, filename, tags, metadata, created_at (when
            stored) and search_score (relative relevance; higher is more relevant).
            content is the item's text cut to the length above or, when the text is
            longer and the item has a summary, its summary."""
    ).format(
        filters_section=_build_filters_section(),
        modalities_str=modalities_str,
        preview_length=settings.MAX_PREVIEW_LENGTH,
        non_preview_length=settings.MAX_NON_PREVIEW_LENGTH,
    )


core_mcp = FastMCP("memory-core")

# Item fields memory sets itself. Every other field may hold text whoever sent,
# authored or named the item wrote, metadata included.
SYSTEM_FIELDS = frozenset(
    {"id", "modality", "type", "embed_status", "mime_type", "size", "inserted_at", "created_at", "search_score"}
)


def wrapped_item(tag: str, item: dict) -> dict:
    """The item with each free field in one `tag` block: text as is, lists and
    dicts as JSON in a block whose field ends in .json."""

    def wrap(field: str, value: Any) -> Any:
        if field in SYSTEM_FIELDS or value in (None, "", [], {}):
            return value
        if isinstance(value, (list, dict)):
            text = json.dumps(value, ensure_ascii=False, default=str)
            return untrusted(tag, item["id"], f"{field}.json", text)
        return untrusted(tag, item["id"], field, str(value))

    return {field: wrap(field, value) for field, value in item.items()}


def filter_observation_source_ids(
    tags: list[str] | None = None,
    observation_types: list[str] | None = None,
    subject: str | None = None,
) -> list[int] | None:
    """Ids of the observations with any of ``tags``, one of ``observation_types``
    and exactly ``subject``, or None when none of them is set.

    These are AgentObservation columns, which neither the chunk payload nor the
    shared search filters carry, so they reach both search arms as source ids.
    """
    conditions = [
        *([AgentObservation.tags.op("&&")(sql_cast(tags, ARRAY(Text)))] if tags else []),
        *([AgentObservation.observation_type.in_(observation_types)] if observation_types else []),
        *([AgentObservation.subject == subject] if subject else []),
    ]
    if not conditions:
        return None

    with make_session() as session:
        return list(session.scalars(select(AgentObservation.id).where(*conditions)))


def max_created_at_condition(max_created_at: str):
    # A bare date means the whole of that day, not its first instant.
    try:
        day = date.fromisoformat(max_created_at)
    except ValueError:
        return SourceItem.inserted_at <= max_created_at
    return SourceItem.inserted_at < day + timedelta(days=1)


def created_at_conditions(filters: MCPSearchFilters) -> list:
    min_created_at = filters.get("min_created_at")
    max_created_at = filters.get("max_created_at")
    return [
        *([SourceItem.inserted_at >= min_created_at] if min_created_at else []),
        *([max_created_at_condition(max_created_at)] if max_created_at else []),
    ]


def filter_source_ids(
    modalities: set[str], filters: SearchFilters, access_filter: AccessFilter | None
) -> list[int] | None:
    """Ids of the visible items matching the tag, size and created_at filters,
    or None when none of them is set.

    The Qdrant payload has no created_at key, so those bounds reach the vector
    arm only through these ids.
    """
    tags = filters.get("tags")
    min_size = filters.get("min_size")
    max_size = filters.get("max_size")
    created_at = created_at_conditions(filters)
    if not tags and min_size is None and max_size is None and not created_at:
        return None

    with make_session() as session:
        items_query = session.query(SourceItem.id).filter(*created_at)

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
        items_query = apply_access_filter_to_query(items_query, access_filter)
        return [item.id for item in items_query.all()]


@core_mcp.tool(description=_build_search_description(), annotations=READ_ONLY)
@visible_when(require_scopes(SCOPE_READ))
async def search(
    query: str,
    filters: MCPSearchFilters = {},
    modalities: set[str] = set(),
    limit: int = 20,
    previews: bool = False,
    use_scores: bool = False,
    use_bm25: bool | None = None,
    use_hyde: bool | None = None,
    use_reranking: bool | None = None,
    use_query_analysis: bool | None = None,
) -> dict:
    logger.info(f"MCP search for: {query}")
    config = SearchConfig(
        limit=min(limit, 100),
        previews=previews,
        useScores=use_scores,
        useBm25=use_bm25,
        useHyde=use_hyde,
        useReranking=use_reranking,
        useQueryAnalysis=use_query_analysis,
    )

    if not modalities:
        modalities = set(ALL_COLLECTIONS.keys())
    modalities = set(modalities) & SEARCHABLE_COLLECTIONS

    access_filter = get_current_user_access_filter()
    search_filters = SearchFilters(**filters)
    search_filters["source_ids"] = filter_source_ids(
        modalities, search_filters, access_filter
    )
    search_filters["access_filter"] = access_filter

    user = get_mcp_current_user()
    user_id = getattr(user, "id", None) if user else None
    upload_data = extract.extract_text(query, skip_summary=True)
    # An empty id list reads as "no restriction" downstream.
    results = (
        []
        if search_filters["source_ids"] == []
        else await search_base(
            upload_data,
            modalities=modalities,
            filters=search_filters,
            config=config,
            searcher_id=user_id,
        )
    )

    # Audit-log the search. The AccessLog docstring (and access_control.py:13)
    # claim "all access is logged, including superadmin access" — that's only
    # true if we actually call log_search_access. Logged best-effort: a logging
    # failure must not fail the user's search.
    if user_id is not None:
        try:
            log_search_access(user_id, query, len(results))
        except Exception:
            logger.exception("log_search_access failed for user_id=%s", user_id)

    tag = new_tag()
    return {
        "untrusted": preamble(tag),
        "results": [wrapped_item(tag, result.model_dump()) for result in results],
    }


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
    Record observations about the user (preferences, behaviors, beliefs, contradictions) for long-term understanding.
    Observations should be specific enough to make sense months later.

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


@core_mcp.tool(annotations=READ_ONLY)
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
    Search recorded observations about the user by meaning; the query matches both content and context.

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
    # The LLM steps read every chunk as one query, and the temporal chunk's
    # "Time: HH:MM on <weekday>" would be resolved into a date range.
    config = SearchConfig(limit=min(limit, 100), useHyde=False, useQueryAnalysis=False)
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
    source_ids = filter_observation_source_ids(
        tags=tags, observation_types=observation_types, subject=subject
    )
    # An empty id list reads as "no restriction" downstream.
    results = (
        []
        if source_ids == []
        else await search_base(
            [
                extract.DataChunk(data=[query]),
                extract.DataChunk(data=[semantic_text]),
                extract.DataChunk(data=[temporal]),
            ],
            modalities={"semantic", "temporal"},
            filters=SearchFilters(
                min_confidences=min_confidences,
                source_ids=source_ids,
                access_filter=access_filter,
            ),
            config=config,
        )
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


@core_mcp.tool(annotations=READ_ONLY)
@visible_when(require_scopes(SCOPE_READ))
def fetch_file(filename: str) -> dict:
    """
    Read a stored file.

    Args:
        filename: Path relative to file storage, as given in the filename field of search and fetch results

    Returns: {"content": [{"type": "text" | "image" | "blob", "mime_type": str, "data": str}]}.
    Text files come back as raw text, others base64-encoded.
    Fails if the item owning the file is not accessible to you, or if a non-text
    file is larger than 10 MiB.
    """
    try:
        path = paths.validate_path_within_directory(
            settings.FILE_STORAGE_DIR, filename.strip()
        )
    except ValueError as e:
        raise ValueError(f"Invalid path: {e}")

    logger.debug(f"Fetching file: {path}")

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

    # Checked after access so a missing file says nothing to callers who can't see the item.
    if not path.exists():
        raise FileNotFoundError(
            f"{filename} is indexed but its file is missing from storage"
        )

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


@core_mcp.tool(annotations=READ_ONLY)
@visible_when(require_scopes(SCOPE_READ))
async def fetch(
    id: int | None = None,
    ids: list[int] | None = None,
    include_content: bool = True,
    include_journal: bool = False,
) -> dict:
    """
    Get full details of source item(s) by ID.
    Use after search to drill down into specific results.

    Args:
        id: Single source item ID (from search results)
        ids: Multiple source item IDs for bulk fetch (max 200)
        include_content: Whether to include full content (default True)
        include_journal: Whether to include journal entries (default False)

    Returns: For id, the item's details plus untrusted; for ids, {untrusted, items}.
    untrusted names the tag of the blocks that hold text people wrote; tags,
    metadata and journal_entries are JSON in a block whose field ends in .json.

    Access control behaviour differs by mode:
        - Single id: raises ValueError if the item is not found or access is denied.
        - Bulk ids: silently omits inaccessible/missing items and returns a shorter list.
    """
    if id is not None and ids is not None:
        raise ValueError("Cannot provide both 'id' and 'ids' — use one or the other")
    if id is None and ids is None:
        raise ValueError("Must provide either 'id' or 'ids'")

    # dedup, preserving order; the `is not None` filter is a no-op given the
    # guards above but narrows the type to list[int] for the access helper.
    fetch_ids = [i for i in dict.fromkeys(ids if ids is not None else [id]) if i is not None]
    if len(fetch_ids) > 200:
        raise ValueError(f"Cannot fetch more than 200 items at once (got {len(fetch_ids)})")

    # Token-derived access filter (honours API-key scope overrides) + user info,
    # computed before the session opens to avoid nested sessions.
    access_filter = get_current_user_access_filter()
    user = get_mcp_current_user()
    user_id: int | None = getattr(user, "id", None) if user else None

    with make_session() as session:
        items: list[SourceItem]
        if access_filter is not None and user is None:
            # Fail closed: a non-admin token passed the SCOPE_READ gate but the
            # data-layer user lookup returned None (e.g. an expired/deleted
            # UserSession). Return nothing rather than the public-by-id fallback
            # that the empty AccessFilter would otherwise allow — preserving the
            # prior explicit guard. (Admins, access_filter is None, are
            # unaffected and still resolve via the gate below.)
            items = []
        else:
            # Single canonical fetch path: fetch_accessible_source_items applies
            # the access_filter (creator / person / public / project-role) AND
            # the "hidden" tombstone via the same gate as search / list / count,
            # even on the admin (access_filter is None) path — so fetch-by-id
            # can't drift. Eager-load 'people' since as_payload() reads it.
            base_query = (
                session.query(SourceItem)
                .options(selectinload(SourceItem.people))
                .filter(SourceItem.embed_status == "STORED")
                # Session transcripts are owner-only-even-for-admins;
                # only claude_session_fetch may read them.
                .filter(SourceItem.modality.notin_(SESSION_COLLECTIONS))
            )
            items = fetch_accessible_source_items(
                session, fetch_ids, access_filter, base_query=base_query
            )

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
                result["content"] = item.readable_content
            if include_journal:
                result["journal_entries"] = journal_by_item.get(item.id, [])
            results_by_id[item.id] = result

        tag = new_tag()
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
            return {"untrusted": preamble(tag)} | wrapped_item(tag, results_by_id[id])

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

        return {
            "untrusted": preamble(tag),
            "items": [wrapped_item(tag, results_by_id[i]) for i in fetch_ids if i in results_by_id],
        }


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
# shared FILTER_REGISTRY). Everything else (registry keys) is folded on
# declaratively.
ITEM_SPECIAL_FILTER_KEYS = frozenset(
    {"source_ids", "min_created_at", "max_created_at", "person_id", "account", "network", "chat"}
)

# Keys accepted by item enumeration: the declarative registry plus the special
# keys handled inline below. min_confidences is an observation-search concept
# (ConfidenceScore) with no meaning for a SourceItem enumeration, so it is NOT
# accepted here and rejects loudly rather than silently returning an unfiltered
# count. Any other non-empty key is
# likewise rejected so list_items and count_items can never disagree about a
# filter one of them forgot.
ITEM_ALLOWED_FILTER_KEYS = set(FILTER_REGISTRY) | ITEM_SPECIAL_FILTER_KEYS


def apply_item_filters(query, modalities: set[str], filters: MCPSearchFilters):
    """Apply modality + MCPSearchFilters constraints to a SourceItem query.

    Shared by list_items and count_items so the two can never drift. Registry
    filters (mail/blog/doc metadata) are folded on via
    :func:`apply_registry_filters_sql`. A query that combines filters only
    different item types can satisfy can never match a row (an item is exactly
    one type); that is the correct outcome.

    Any provided filter key not in :data:`ITEM_ALLOWED_FILTER_KEYS` raises
    ValueError instead of being silently dropped.
    """
    reject_unknown_filter_keys(filters, allowed=ITEM_ALLOWED_FILTER_KEYS)

    # Session transcripts are owner-only-even-for-admins by contract
    # (claude_session_search/_fetch enforce ownership); the generic
    # enumeration tools never expose them — an admin's access_filter is
    # None, so without this exclusion list_items would return other
    # users' private conversations.
    query = query.filter(SourceItem.modality.notin_(SESSION_COLLECTIONS))

    if modalities:
        query = query.filter(SourceItem.modality.in_(modalities))

    # Special keys scoped to SourceItem itself.
    if source_ids := filters.get("source_ids"):
        query = query.filter(SourceItem.id.in_(source_ids))
    query = query.filter(*created_at_conditions(filters))

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

    if account := filters.get("account"):
        query = query.filter(account_match_sql(account))
    if (beeper_clause := beeper_match_sql(filters)) is not None:
        query = query.filter(beeper_clause)

    return query


def apply_browse_filters(
    query,
    item_types: set[str],
    embed_statuses: set[str],
    contains: str | None,
    connection_id: str | None,
):
    """Apply item type, indexing status, text match and connection constraints.

    Shared by list_items and count_items. Raises ValueError for an unknown
    status, or a connection_id the item types can't be narrowed by.
    """
    check_embed_statuses(embed_statuses)
    if embed_statuses:
        query = query.filter(SourceItem.embed_status.in_(embed_statuses))
    if item_types:
        query = query.filter(SourceItem.type.in_(item_types))
    if contains and contains.strip():
        query = query.filter(text_match_clause(contains.strip(), item_types))
    if connection_id:
        query = query.filter(connection_clause(item_types, connection_id))
    return query


@core_mcp.tool(description=_build_list_items_description(), annotations=READ_ONLY)
@visible_when(require_scopes(SCOPE_READ))
async def list_items(
    modalities: set[str] = set(),
    filters: MCPSearchFilters = {},
    since: str | None = None,
    until: str | None = None,
    channel: str | None = None,
    author: str | None = None,
    repo: str | None = None,
    item_types: set[str] = set(),
    embed_statuses: set[str] = {"STORED"},
    contains: str | None = None,
    connection_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "inserted_at",
    sort_order: str = "desc",
    include_metadata: bool = True,
    include_total: bool = True,
) -> dict:
    """List items without semantic search. See tool description for full parameter docs."""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    if sort_by not in ("inserted_at", "size", "id"):
        sort_by = "inserted_at"
    if sort_order not in ("asc", "desc"):
        sort_order = "desc"

    # Get access filter for current user
    access_filter = get_current_user_access_filter()

    with make_session() as session:
        query = session.query(SourceItem)
        query = apply_access_control_to_query(query, access_filter, session)
        query = apply_browse_filters(
            query, item_types, embed_statuses, contains, connection_id
        )
        query = apply_item_filters(query, modalities, filters)
        query = apply_scope_filters(
            query, since=since, until=until, channel=channel, author=author, repo=repo
        )

        total = query.count() if include_total else None

        # id breaks ties so pages never overlap or skip rows.
        sort_columns = [getattr(SourceItem, sort_by), SourceItem.id]
        if sort_order == "desc":
            sort_columns = [column.desc() for column in sort_columns]
        query = query.order_by(*sort_columns)

        # One extra row tells whether another page follows without counting.
        rows = query.offset(offset).limit(limit + 1).all()

        items = []
        for item in rows[:limit]:
            preview = item.preview_text

            item_dict = {
                "id": item.id,
                "modality": item.modality,
                "type": item.type,
                "embed_status": item.embed_status,
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

        tag = new_tag()
        return {
            "untrusted": preamble(tag),
            "items": [wrapped_item(tag, item) for item in items],
            "total": total,
            "has_more": len(rows) > limit,
        }


@core_mcp.tool(description=_build_count_items_description(), annotations=READ_ONLY)
@visible_when(require_scopes(SCOPE_READ))
async def count_items(
    modalities: set[str] = set(),
    filters: MCPSearchFilters = {},
    since: str | None = None,
    until: str | None = None,
    channel: str | None = None,
    author: str | None = None,
    repo: str | None = None,
    item_types: set[str] = set(),
    embed_statuses: set[str] = {"STORED"},
    contains: str | None = None,
    connection_id: str | None = None,
) -> dict:
    """Count items matching criteria. See tool description for full parameter docs."""
    # Get access filter for current user
    access_filter = get_current_user_access_filter()

    with make_session() as session:
        base_query = session.query(SourceItem)
        base_query = apply_access_control_to_query(base_query, access_filter, session)
        base_query = apply_browse_filters(
            base_query, item_types, embed_statuses, contains, connection_id
        )
        base_query = apply_item_filters(base_query, modalities, filters)
        base_query = apply_scope_filters(
            base_query, since=since, until=until, channel=channel, author=author, repo=repo
        )

        # Get total
        total = base_query.count()

        # Get counts by modality on the same filtered query
        by_modality_query = base_query.with_entities(
            SourceItem.modality, func.count(SourceItem.id)
        ).group_by(SourceItem.modality)

        by_modality = {row[0]: row[1] for row in by_modality_query.all()}

        by_type_query = base_query.with_entities(
            SourceItem.type, func.count(SourceItem.id)
        ).group_by(SourceItem.type)
        by_type = {row[0] or "unknown": row[1] for row in by_type_query.all()}

        connection = connection_for(item_types)
        item_ids = base_query.with_entities(SourceItem.id).scalar_subquery()
        by_connection = (
            connection_counts(session, connection, item_ids) if connection else None
        )

        return {
            "total": total,
            "by_modality": by_modality,
            "by_type": by_type,
            "by_connection": by_connection,
        }
