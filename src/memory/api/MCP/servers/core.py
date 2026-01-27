"""
Core MCP subserver for knowledge base search, observations, and notes.
"""

import base64
import logging
import textwrap
from datetime import datetime, timezone

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from PIL import Image
from pydantic import BaseModel
from sqlalchemy import Text
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import ARRAY

from memory.api.MCP.access import build_user_access_filter_from_dict
from memory.api.MCP.visibility import has_items, require_scopes, visible_when
from memory.common.access_control import AccessFilter, user_can_access
from memory.api.search.search import search as search_base
from memory.api.search.types import MCPSearchFilters, SearchConfig, SearchFilters
from memory.common import extract, paths, settings
from memory.common.celery_app import SYNC_NOTE, SYNC_OBSERVATION
from memory.common.celery_app import app as celery_app
from memory.common.collections import ALL_COLLECTIONS, OBSERVATION_COLLECTIONS
from memory.common.db.connection import make_session
from memory.common.db.models import (
    AgentObservation,
    BlogPost,
    EmailAttachment,
    GoogleDoc,
    MailMessage,
    SourceItem,
)
from memory.common.formatters import observation

logger = logging.getLogger(__name__)


def get_current_user_access_filter() -> AccessFilter | None:
    """
    Get access filter for the current MCP user.

    Returns:
        AccessFilter with user's project access conditions,
        or None if user is superadmin (no filtering needed).
    """
    access_token = get_access_token()
    if access_token is None:
        # No auth - return empty filter (no access)
        logger.warning("get_current_user_access_filter: no access token")
        return AccessFilter(conditions=[])

    # Build user dict from token - we need id and USER's scopes (not API key scopes)
    # for access control. API key scopes control which tools are available,
    # but USER scopes determine admin status and data access.
    with make_session() as session:
        from memory.common.db.models import UserSession

        # Try as session token first
        user_session = session.get(UserSession, access_token.token)
        if user_session and user_session.user:
            user = user_session.user
            user_dict = {
                "id": user.id,
                "scopes": list(user.scopes) if user.scopes else [],
            }
            return build_user_access_filter_from_dict(user_dict)

        # Try as API key
        from memory.api.auth import lookup_api_key

        api_key_record = lookup_api_key(access_token.token, session)
        if api_key_record and api_key_record.user:
            user = api_key_record.user
            user_dict = {
                "id": user.id,
                "scopes": list(user.scopes) if user.scopes else [],
            }
            return build_user_access_filter_from_dict(user_dict)

    # Couldn't identify user - return empty filter (no access)
    logger.warning("get_current_user_access_filter: couldn't identify user from token")
    return AccessFilter(conditions=[])


def get_current_user_id() -> int | None:
    """Get the current user's ID from the access token."""
    access_token = get_access_token()
    if access_token is None:
        return None

    with make_session() as session:
        from memory.common.db.models import UserSession

        # Try as session token first
        user_session = session.get(UserSession, access_token.token)
        if user_session and user_session.user:
            return user_session.user.id

        # Try as API key
        from memory.api.auth import lookup_api_key

        api_key_record = lookup_api_key(access_token.token, session)
        if api_key_record and api_key_record.user:
            return api_key_record.user.id

    return None


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

    return textwrap.dedent("""
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
        Higher scores (>0.7) indicate strong matches.""").format(
        filters_section=_build_filters_section(), modalities_str=modalities_str
    )


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
@visible_when(require_scopes("read"))
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

    return [result.model_dump() for result in results]


class RawObservation(BaseModel):
    subject: str
    content: str
    observation_type: str = "general"
    confidences: dict[str, float] = {}
    evidence: dict | None = None
    tags: list[str] = []


@core_mcp.tool()
@visible_when(require_scopes("observe"))
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
        observations: List of RawObservation objects
        session_id: UUID to group observations from same conversation
        agent_model: AI model making observations (for quality tracking)
    """
    logger.info("MCP: Observing")
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
@visible_when(require_scopes("observe"), has_items(AgentObservation))
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

    return [
        {
            "content": r.content,
            "tags": r.tags,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "metadata": r.metadata,
        }
        for r in results
    ]


@core_mcp.tool()
@visible_when(require_scopes("notes"))
async def create_note(
    subject: str,
    content: str,
    filename: str | None = None,
    note_type: str | None = None,
    confidences: dict[str, float] = {},
    tags: list[str] = [],
) -> dict:
    """
    Create a note when user asks to save or record something.
    Use when user explicitly requests noting information for future reference.

    Args:
        subject: What the note is about (used for organization)
        content: Note content as a markdown string
        filename: Optional path relative to notes folder (e.g., "project/ideas.md")
        note_type: Optional categorization of the note
        confidences: Dict of scores (0.0-1.0), e.g. {"observation_accuracy": 0.9}
        tags: Organization tags for filtering and discovery
    """
    logger.info("MCP: creating note: %s", subject)
    if filename:
        # Validate the path is within the notes directory to prevent path traversal
        try:
            validated_path = paths.validate_path_within_directory(
                settings.NOTES_STORAGE_DIR, filename
            )
            filename = validated_path.relative_to(settings.NOTES_STORAGE_DIR).as_posix()
        except ValueError as e:
            raise ValueError(f"Invalid filename: {e}")

    try:
        task = celery_app.send_task(
            SYNC_NOTE,
            queue=f"{settings.CELERY_QUEUE_PREFIX}-notes",
            kwargs={
                "subject": subject,
                "content": content,
                "filename": filename,
                "note_type": note_type,
                "confidences": confidences,
                "tags": tags,
            },
        )
    except Exception as e:
        import traceback

        traceback.print_exc()
        logger.error(f"Error creating note: {e}")
        raise

    return {
        "task_id": task.id,
        "status": "queued",
    }


@core_mcp.tool()
@visible_when(require_scopes("notes"))
async def note_files(path: str = "/"):
    """
    List note files in the user's note storage.
    Use to discover existing notes before reading or to help user navigate their collection.

    Args:
        path: Directory path to search (e.g., "/", "/projects", "/meetings")
        Use "/" for root, or subdirectories to narrow scope

    Returns: List of file paths relative to notes directory
    """
    try:
        root = paths.validate_path_within_directory(
            settings.NOTES_STORAGE_DIR, path, require_exists=True
        )
    except ValueError as e:
        raise ValueError(f"Invalid path: {e}")

    return [
        f"/notes/{f.relative_to(settings.NOTES_STORAGE_DIR)}"
        for f in root.rglob("*.md")
        if f.is_file()
    ]


@core_mcp.tool()
@visible_when(require_scopes("read"))
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

    mime_type = extract.get_mime_type(path)
    chunks = extract.extract_data_chunks(mime_type, path, skip_summary=True)

    def serialize_chunk(
        chunk: extract.DataChunk, data: extract.MulitmodalChunk
    ) -> dict:
        contents: str | bytes = data  # type: ignore[assignment]
        if isinstance(data, Image.Image):
            contents = data.tobytes()  # type: ignore[union-attr]
        if isinstance(contents, bytes):
            contents = base64.b64encode(contents).decode("ascii")

        return {
            "type": "text" if isinstance(data, str) else "image",
            "mime_type": chunk.mime_type,
            "data": contents,
        }

    return {
        "content": [
            serialize_chunk(chunk, data) for chunk in chunks for data in chunk.data
        ]
    }


# --- Enumeration tools for systematic investigations ---


@core_mcp.tool()
@visible_when(require_scopes("read"))
async def get_item(id: int, include_content: bool = True) -> dict:
    """
    Get full details of a source item by ID.
    Use after search to drill down into specific results.

    Args:
        id: The source item ID (from search results)
        include_content: Whether to include full content (default True)

    Returns: Full item details including metadata, tags, and optionally content.
    """
    from memory.common.access_control import get_user_project_roles

    # Get access filter to check permissions
    access_filter = get_current_user_access_filter()

    with make_session() as session:
        item = (
            session.query(SourceItem)
            .filter(
                SourceItem.id == id,
                SourceItem.embed_status == "STORED",
            )
            .first()
        )
        if not item:
            raise ValueError(f"Item {id} not found or not yet indexed")

        # Check access control
        # access_filter is None for superadmins (they see everything)
        if access_filter is not None:
            # Need to check if user can access this item
            # Get user from current context for project roles lookup
            user_id = get_current_user_id()
            if user_id is None:
                raise ValueError(f"Item {id} not found or access denied")

            from memory.common.db.models import User

            user = session.get(User, user_id)
            if user is None:
                raise ValueError(f"Item {id} not found or access denied")

            project_roles = get_user_project_roles(session, user)
            if not user_can_access(user, item, project_roles):
                raise ValueError(f"Item {id} not found or access denied")

        result = {
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

        return result


def apply_access_control_to_query(query, access_filter: AccessFilter | None, session):
    """
    Apply access control filters to a SQLAlchemy query.

    Args:
        query: SQLAlchemy query object
        access_filter: AccessFilter from get_current_user_access_filter()
        session: Database session for user lookup

    Returns:
        Modified query with access control applied
    """
    from sqlalchemy import or_

    # Superadmin - no filtering
    if access_filter is None:
        return query

    # Build OR conditions for access
    or_conditions = []

    # Public items are visible to all authenticated users
    if access_filter.include_public:
        or_conditions.append(SourceItem.sensitivity == "public")

    # Person override: if user's person is associated with item
    if access_filter.person_id is not None:
        from memory.common.db.models.source_item import source_item_people

        # Items where user's person is in the people relationship
        person_subquery = (
            session.query(source_item_people.c.source_item_id)
            .filter(source_item_people.c.person_id == access_filter.person_id)
            .subquery()
        )
        or_conditions.append(SourceItem.id.in_(person_subquery))

    # Project-based access
    for condition in access_filter.conditions:
        # Item must be in project AND have allowed sensitivity
        project_condition = (
            (SourceItem.project_id == condition.project_id)
            & (SourceItem.sensitivity.in_(list(condition.sensitivities)))
        )
        or_conditions.append(project_condition)

    # If no conditions, user has no access - return empty result
    if not or_conditions:
        # Add impossible condition to return no results
        query = query.filter(SourceItem.id == -1)
    else:
        query = query.filter(or_(*or_conditions))

    return query


@core_mcp.tool()
@visible_when(require_scopes("read"))
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
        modalities: Filter by type: email, blog, book, forum, photo, comic, etc. (empty = all)
        filters: Same filters as search_knowledge_base (tags, min_size, max_size, etc.)
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

        # Apply access control filter
        query = apply_access_control_to_query(query, access_filter, session)

        # Filter by modalities
        if modalities:
            query = query.filter(SourceItem.modality.in_(modalities))

        # Apply filters
        if tags := filters.get("tags"):
            query = query.filter(SourceItem.tags.op("&&")(sql_cast(tags, ARRAY(Text))))
        if min_size := filters.get("min_size"):
            query = query.filter(SourceItem.size >= min_size)
        if max_size := filters.get("max_size"):
            query = query.filter(SourceItem.size <= max_size)
        if source_ids := filters.get("source_ids"):
            query = query.filter(SourceItem.id.in_(source_ids))
        if min_created_at := filters.get("min_created_at"):
            query = query.filter(SourceItem.inserted_at >= min_created_at)
        if max_created_at := filters.get("max_created_at"):
            query = query.filter(SourceItem.inserted_at <= max_created_at)

        # Metadata filters - require joining specific tables
        if folder_path := filters.get("folder_path"):
            query = query.join(GoogleDoc).filter(
                GoogleDoc.folder_path.ilike(f"%{folder_path}%")
            )
        if sender := filters.get("sender"):
            query = (
                query.join(EmailAttachment)
                .join(MailMessage, EmailAttachment.mail_message_id == MailMessage.id)
                .filter(MailMessage.sender == sender)
            )
        if domain := filters.get("domain"):
            query = query.join(BlogPost).filter(BlogPost.domain == domain)

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
            preview = None
            if item.content:
                preview = (
                    item.content[:200] + "..."
                    if len(item.content) > 200
                    else item.content
                )

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
@visible_when(require_scopes("read"))
async def count_items(
    modalities: set[str] = set(),
    filters: MCPSearchFilters = {},
) -> dict:
    """
    Count items matching criteria without retrieving them.
    Use to understand scope before systematic review.

    Args:
        modalities: Filter by type (empty = all)
        filters: Same filters as search_knowledge_base

    Returns: {total: int, by_modality: {email: 100, blog: 50, ...}}
    """
    from sqlalchemy import func as sql_func

    # Get access filter for current user
    access_filter = get_current_user_access_filter()

    with make_session() as session:
        base_query = session.query(SourceItem).filter(
            SourceItem.embed_status == "STORED"
        )

        # Apply access control filter
        base_query = apply_access_control_to_query(base_query, access_filter, session)

        # Apply filters
        if modalities:
            base_query = base_query.filter(SourceItem.modality.in_(modalities))
        if tags := filters.get("tags"):
            base_query = base_query.filter(
                SourceItem.tags.op("&&")(sql_cast(tags, ARRAY(Text)))
            )
        if min_size := filters.get("min_size"):
            base_query = base_query.filter(SourceItem.size >= min_size)
        if max_size := filters.get("max_size"):
            base_query = base_query.filter(SourceItem.size <= max_size)
        if source_ids := filters.get("source_ids"):
            base_query = base_query.filter(SourceItem.id.in_(source_ids))
        if min_created_at := filters.get("min_created_at"):
            base_query = base_query.filter(SourceItem.inserted_at >= min_created_at)
        if max_created_at := filters.get("max_created_at"):
            base_query = base_query.filter(SourceItem.inserted_at <= max_created_at)

        # Get total
        total = base_query.count()

        # Get counts by modality
        by_modality_query = base_query.with_entities(
            SourceItem.modality, sql_func.count(SourceItem.id)
        ).group_by(SourceItem.modality)

        by_modality = {row[0]: row[1] for row in by_modality_query.all()}

        return {
            "total": total,
            "by_modality": by_modality,
        }
