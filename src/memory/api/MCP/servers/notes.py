"""
MCP subserver for notes management.
"""

import logging

from fastmcp import FastMCP

from memory.api.MCP.visibility import require_scopes, visible_when
from memory.common import paths, settings
from memory.common.celery_app import SYNC_NOTE
from memory.common.celery_app import app as celery_app
from memory.common.scopes import SCOPE_NOTES, SCOPE_NOTES_WRITE

logger = logging.getLogger(__name__)

notes_mcp = FastMCP("memory-notes")


@notes_mcp.tool()
@visible_when(require_scopes(SCOPE_NOTES_WRITE))
async def upsert(
    subject: str,
    content: str,
    filename: str | None = None,
    note_type: str | None = None,
    confidences: dict[str, float] | None = None,
    tags: list[str] | None = None,
) -> dict:
    """
    Create or update a note when user asks to save or record something.
    Use when user explicitly requests noting information for future reference.
    If a note with the same filename exists, it will be updated.

    Args:
        subject: What the note is about (used for organization)
        content: Note content as a markdown string
        filename: Optional path relative to notes folder (e.g., "project/ideas.md")
        note_type: Optional categorization of the note
        confidences: Dict of scores (0.0-1.0), e.g. {"observation_accuracy": 0.9}
        tags: Organization tags for filtering and discovery
    """
    confidences = confidences or {}
    tags = tags or []
    logger.info("MCP: upserting note: %s", subject)
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


@notes_mcp.tool()
@visible_when(require_scopes(SCOPE_NOTES))
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
