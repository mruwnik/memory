"""
MCP tools for the epistemic sparring partner system.
"""

import base64
import logging
import pathlib
from datetime import datetime, timezone
from PIL import Image

from pydantic import BaseModel
from sqlalchemy import Text
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import ARRAY

from memory.api.MCP.base import mcp
from memory.api.search.search import search
from memory.api.search.types import SearchFilters, SearchConfig
from memory.common import extract, settings
from memory.common.celery_app import SYNC_NOTE, SYNC_OBSERVATION
from memory.common.celery_app import app as celery_app
from memory.common.collections import ALL_COLLECTIONS, OBSERVATION_COLLECTIONS
from memory.common.db.connection import make_session
from memory.common.db.models import SourceItem, AgentObservation
from memory.common.formatters import observation

logger = logging.getLogger(__name__)


def validate_path_within_directory(
    base_dir: pathlib.Path, requested_path: str
) -> pathlib.Path:
    """Validate that a requested path resolves within the base directory.

    Prevents path traversal attacks using ../ or similar techniques.

    Args:
        base_dir: The allowed base directory
        requested_path: The user-provided path

    Returns:
        The resolved absolute path if valid

    Raises:
        ValueError: If the path would escape the base directory
    """
    resolved = (base_dir / requested_path.lstrip("/")).resolve()
    base_resolved = base_dir.resolve()

    if not str(resolved).startswith(str(base_resolved) + "/") and resolved != base_resolved:
        raise ValueError(f"Path escapes allowed directory: {requested_path}")

    return resolved


def filter_observation_source_ids(
    tags: list[str] | None = None, observation_types: list[str] | None = None
):
    if not tags and not observation_types:
        return None

    with make_session() as session:
        items_query = session.query(AgentObservation.id)

        if tags:
            # Use PostgreSQL array overlap operator with proper array casting
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
    size = filters.get("size")
    if not (tags or size):
        return None

    with make_session() as session:
        items_query = session.query(SourceItem.id)

        if tags:
            # Use PostgreSQL array overlap operator with proper array casting
            items_query = items_query.filter(
                SourceItem.tags.op("&&")(sql_cast(tags, ARRAY(Text))),
            )
        if size:
            items_query = items_query.filter(SourceItem.size == size)
        if modalities:
            items_query = items_query.filter(SourceItem.modality.in_(modalities))
        source_ids = [item.id for item in items_query.all()]

    return source_ids


@mcp.tool()
async def search_knowledge_base(
    query: str,
    filters: SearchFilters,
    config: SearchConfig = SearchConfig(),
    modalities: set[str] = set(),
) -> list[dict]:
    """
    Search user's stored content including emails, documents, articles, books.
    Use to find specific information the user has saved or received.
    Combine with search_observations for complete user context.
    Use the `get_metadata_schemas` tool to get the metadata schema for each collection, from which you can infer the keys for the filters dictionary.

    If you know what kind of data you're looking for, it's worth explicitly filtering by that modality, as this gives better results.

    Args:
        query: Natural language search query - be descriptive about what you're looking for
        modalities: Filter by type: email, blog, book, forum, photo, comic, webpage (empty = all)
        filters: a dictionary with the following keys:
            - tags: a list of tags to filter by
            - source_ids: a list of source ids to filter by
            - min_size: the minimum size of the content to filter by
            - max_size: the maximum size of the content to filter by
            - min_created_at: the minimum created_at date to filter by
            - max_created_at: the maximum created_at date to filter by
        config: a dictionary with the following keys:
            - limit: the maximum number of results to return
            - previews: whether to include the actual content in the results (up to MAX_PREVIEW_LENGTH characters)
            - useScores: whether to score the results with a LLM before returning - this results in better results but is slower

    Returns: List of search results with id, score, chunks, content, filename
    Higher scores (>0.7) indicate strong matches.
    """
    logger.info(f"MCP search for: {query}")

    if not modalities:
        modalities = set(ALL_COLLECTIONS.keys())
    # Filter to valid collections, excluding observation collections
    modalities = (set(modalities) & ALL_COLLECTIONS.keys()) - OBSERVATION_COLLECTIONS

    search_filters = SearchFilters(**filters)
    search_filters["source_ids"] = filter_source_ids(modalities, search_filters)

    upload_data = extract.extract_text(query, skip_summary=True)
    results = await search(
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


@mcp.tool()
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
            observation,
            celery_app.send_task(
                SYNC_OBSERVATION,
                queue=f"{settings.CELERY_QUEUE_PREFIX}-notes",
                kwargs={
                    "subject": observation.subject,
                    "content": observation.content,
                    "observation_type": observation.observation_type,
                    "confidences": observation.confidences,
                    "evidence": observation.evidence,
                    "tags": observation.tags,
                    "session_id": session_id,
                    "agent_model": agent_model,
                },
            ),
        )
        for observation in observations
    ]

    def short_content(obs: RawObservation) -> str:
        if len(obs.content) > 50:
            return obs.content[:47] + "..."
        return obs.content

    return {
        "task_ids": {short_content(obs): task.id for obs, task in tasks},
        "status": "queued",
    }


@mcp.tool()
async def search_observations(
    query: str,
    subject: str = "",
    tags: list[str] | None = None,
    observation_types: list[str] | None = None,
    min_confidences: dict[str, float] = {},
    config: SearchConfig = SearchConfig(),
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
        config: SearchConfig

    Returns: List with content, tags, created_at, metadata
    Results sorted by relevance to your query.
    """
    logger.info("MCP: Searching observations for %s", query)
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
    results = await search(
        [
            extract.DataChunk(data=[query]),
            extract.DataChunk(data=[semantic_text]),
            extract.DataChunk(data=[temporal]),
        ],
        modalities={"semantic", "temporal"},
        filters=SearchFilters(
            subject=subject,
            min_confidences=min_confidences,
            tags=tags,
            observation_types=observation_types,
            source_ids=filter_observation_source_ids(tags=tags),
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


@mcp.tool()
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
        path = pathlib.Path(filename)
        if not path.is_absolute():
            path = pathlib.Path(settings.NOTES_STORAGE_DIR) / path
        filename = path.relative_to(settings.NOTES_STORAGE_DIR).as_posix()

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


@mcp.tool()
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
        root = validate_path_within_directory(settings.NOTES_STORAGE_DIR, path)
    except ValueError as e:
        raise ValueError(f"Invalid path: {e}")

    return [
        f"/notes/{f.relative_to(settings.NOTES_STORAGE_DIR)}"
        for f in root.rglob("*.md")
        if f.is_file()
    ]


@mcp.tool()
def fetch_file(filename: str) -> dict:
    """
    Read file content with automatic type detection.
    Returns dict with content, mime_type, is_text, file_size.
    Text content as string, binary as base64.
    """
    try:
        path = validate_path_within_directory(
            settings.FILE_STORAGE_DIR, filename.strip()
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
        contents = data
        if isinstance(data, Image.Image):
            contents = data.tobytes()
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
