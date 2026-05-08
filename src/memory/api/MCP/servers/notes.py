"""
MCP subserver for notes management.
"""

import logging

from fastmcp import FastMCP

from memory.api.MCP.access import get_mcp_current_user
from memory.api.MCP.visibility import require_scopes, visible_when
from memory.common import paths, settings
from memory.common.access_control import (
    apply_access_filter_to_query,
    build_access_filter,
    get_user_project_roles,
)
from memory.common.celery_app import SYNC_NOTE
from memory.common.celery_app import app as celery_app
from memory.common.db.connection import make_session
from memory.common.db.models import Note
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
        try:
            filename = paths.to_db_filename(filename, base_dir=settings.NOTES_STORAGE_DIR)
        except ValueError as e:
            raise ValueError(f"Invalid filename: {e}")

    user = get_mcp_current_user()
    creator_id = user.id if user else None

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
                "creator_id": creator_id,
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
    # Validate and normalise the requested path prefix (prevents traversal)
    try:
        paths.validate_path_within_directory(settings.NOTES_STORAGE_DIR, path)
    except ValueError as e:
        raise ValueError(f"Invalid path: {e}")

    # Note.filename is FILE_STORAGE_DIR-relative and always under
    # NOTES_STORAGE_DIR. Derive the prefix from settings so we don't bake in
    # the literal "notes/" — NOTES_STORAGE_DIR is env-overridable.
    notes_prefix = paths.to_db_filename(settings.NOTES_STORAGE_DIR)
    path_prefix = path.lstrip("/").rstrip("/")
    # Trailing slash is required: without it, path="proj" would match
    # notes/projects.md as well as notes/projects/*. We only want
    # directory-prefix matches.
    db_prefix = (
        f"{notes_prefix}/{path_prefix}/" if path_prefix else f"{notes_prefix}/"
    )

    user = get_mcp_current_user()
    if user is None:
        return []

    with make_session() as session:
        query = session.query(Note.filename).filter(Note.filename.isnot(None))

        # Notes are private artefacts — no public-bypass even if a Note ever
        # ends up with sensitivity="public". Project access + creator override
        # + person override all still apply, via the central access filter so
        # this stays in lock-step with search.
        access_filter = build_access_filter(
            user,
            get_user_project_roles(session, user),  # type: ignore[arg-type]
            include_public=False,
        )
        query = apply_access_filter_to_query(query, access_filter)

        # Restrict to filenames inside the requested notes subtree.
        # autoescape=True is required: without it, SQLAlchemy's startswith()
        # passes % and _ through unescaped (it just appends "%" to the pattern),
        # which would let path="%/secret" enumerate beyond the requested
        # subtree.
        query = query.filter(
            Note.filename.startswith(db_prefix, autoescape=True)
        )

        rows = query.all()

    return [f"/{row.filename}" for row in rows]
