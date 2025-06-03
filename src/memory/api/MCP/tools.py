"""
MCP tools for the epistemic sparring partner system.
"""

import logging
import pathlib
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel
from sqlalchemy import Text, func
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import ARRAY

from memory.api.search.search import SearchFilters, search
from memory.common import extract, settings
from memory.common.collections import ALL_COLLECTIONS, OBSERVATION_COLLECTIONS
from memory.common.db.connection import make_session
from memory.common.db.models import AgentObservation, SourceItem
from memory.common.formatters import observation
from memory.common.celery_app import app as celery_app, SYNC_OBSERVATION, SYNC_NOTE

logger = logging.getLogger(__name__)

# Create MCP server instance
mcp = FastMCP("memory", stateless_http=True)


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


def filter_source_ids(
    modalities: set[str],
    tags: list[str] | None = None,
):
    if not tags:
        return None

    with make_session() as session:
        items_query = session.query(SourceItem.id)

        if tags:
            # Use PostgreSQL array overlap operator with proper array casting
            items_query = items_query.filter(
                SourceItem.tags.op("&&")(sql_cast(tags, ARRAY(Text))),
            )
        if modalities:
            items_query = items_query.filter(SourceItem.modality.in_(modalities))
        source_ids = [item.id for item in items_query.all()]

    return source_ids


@mcp.tool()
async def get_current_time() -> dict:
    """Get the current time in UTC."""
    return {"current_time": datetime.now(timezone.utc).isoformat()}


@mcp.tool()
async def get_all_tags() -> list[str]:
    """
    Get all unique tags used across the entire knowledge base.
    Returns sorted list of tags from both observations and content.
    """
    with make_session() as session:
        tags_query = session.query(func.unnest(SourceItem.tags)).distinct()
        return sorted({row[0] for row in tags_query if row[0] is not None})


@mcp.tool()
async def get_all_subjects() -> list[str]:
    """
    Get all unique subjects from observations about the user.
    Returns sorted list of subject identifiers used in observations.
    """
    with make_session() as session:
        return sorted(
            r.subject for r in session.query(AgentObservation.subject).distinct()
        )


@mcp.tool()
async def get_all_observation_types() -> list[str]:
    """
    Get all observation types that have been used.
    Standard types are belief, preference, behavior, contradiction, general, but there can be more.
    """
    with make_session() as session:
        return sorted(
            {
                r.observation_type
                for r in session.query(AgentObservation.observation_type).distinct()
                if r.observation_type is not None
            }
        )


@mcp.tool()
async def search_knowledge_base(
    query: str,
    previews: bool = False,
    modalities: set[str] = set(),
    tags: list[str] = [],
    limit: int = 10,
) -> list[dict]:
    """
    Search user's stored content including emails, documents, articles, books.
    Use to find specific information the user has saved or received.
    Combine with search_observations for complete user context.

    Args:
        query: Natural language search query - be descriptive about what you're looking for
        previews: Include actual content in results - when false only a snippet is returned
        modalities: Filter by type: email, blog, book, forum, photo, comic, webpage (empty = all)
        tags: Filter by tags - content must have at least one matching tag
        limit: Max results (1-100)

    Returns: List of search results with id, score, chunks, content, filename
    Higher scores (>0.7) indicate strong matches.
    """
    logger.info(f"MCP search for: {query}")

    if not modalities:
        modalities = set(ALL_COLLECTIONS.keys())
    modalities = set(modalities) & ALL_COLLECTIONS.keys() - OBSERVATION_COLLECTIONS

    upload_data = extract.extract_text(query)
    results = await search(
        upload_data,
        previews=previews,
        modalities=modalities,
        limit=limit,
        min_text_score=0.4,
        min_multimodal_score=0.25,
        filters=SearchFilters(
            tags=tags,
            source_ids=filter_source_ids(tags=tags, modalities=modalities),
        ),
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
    limit: int = 10,
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
        limit: Max results (1-100)

    Returns: List with content, tags, created_at, metadata
    Results sorted by relevance to your query.
    """
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
        previews=True,
        modalities={"semantic", "temporal"},
        limit=limit,
        filters=SearchFilters(
            subject=subject,
            min_confidences=min_confidences,
            tags=tags,
            observation_types=observation_types,
            source_ids=filter_observation_source_ids(tags=tags),
        ),
        timeout=2,
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
    root = settings.NOTES_STORAGE_DIR / path.lstrip("/")
    return [
        f"/notes/{f.relative_to(settings.NOTES_STORAGE_DIR)}"
        for f in root.rglob("*.md")
        if f.is_file()
    ]


@mcp.tool()
def fetch_file(filename: str):
    """
    Read file content from user's storage.
    Use when you need to access specific content of a file that's been referenced.

    Args:
        filename: Path to file (e.g., "/notes/project.md", "/documents/report.pdf")
        Path should start with "/" and use forward slashes

    Returns: Raw bytes content (decode as UTF-8 for text files)
    Raises FileNotFoundError if file doesn't exist.
    """
    path = settings.FILE_STORAGE_DIR / filename.lstrip("/")
    if not path.exists():
        raise FileNotFoundError(f"File not found: {filename}")

    return path.read_bytes()
