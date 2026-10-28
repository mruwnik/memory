"""
MCP subserver for notes management.
"""

import logging

from fastmcp import FastMCP

from memory.api.MCP.access import get_mcp_current_user
from memory.api.MCP.visibility import require_scopes, visible_when
from memory.common import paths, settings
from memory.common.access_control import (
    UserLike,
    apply_access_filter_to_query,
    build_access_filter,
    get_user_project_roles,
    has_admin_scope,
    user_can_edit,
)
from memory.common.celery_app import SYNC_NOTE
from memory.common.celery_app import app as celery_app
from memory.common.db.connection import make_session
from memory.common.db.models import Note
from memory.common.scopes import SCOPE_NOTES, SCOPE_NOTES_WRITE

logger = logging.getLogger(__name__)

notes_mcp = FastMCP("memory-notes")


def visible_notes(session, user: UserLike):
    # Notes are private artefacts: no public bypass even if a Note ever ends up
    # with sensitivity="public". Same central filter as search.
    access_filter = build_access_filter(
        user,
        get_user_project_roles(session, user),
        include_public=False,
    )
    return apply_access_filter_to_query(session.query(Note), access_filter)


@notes_mcp.tool()
@visible_when(require_scopes(SCOPE_NOTES_WRITE))
async def upsert(
    subject: str,
    content: str,
    filename: str | None = None,
    note_type: str | None = None,
    confidences: dict[str, float] | None = None,
    tags: list[str] | None = None,
    create_only: bool = False,
) -> dict:
    """
    Create or update a note when user asks to save or record something.
    Use when user explicitly requests noting information for future reference.
    If a note with the same filename exists, it will be updated; only its creator or an admin can update it.

    Args:
        subject: What the note is about (used for organization)
        content: Note content as a markdown string
        filename: Optional markdown path relative to notes folder (e.g., "project/ideas.md"); no hidden (dot-prefixed) segments
        note_type: Optional categorization of the note
        confidences: Dict of scores (0.0-1.0), e.g. {"observation_accuracy": 0.9}
        tags: Organization tags for filtering and discovery
        create_only: Fail instead of updating when a note with this filename exists
    """
    confidences = confidences or {}
    tags = tags or []
    logger.info("MCP: upserting note: %s", subject)
    try:
        if filename:
            # An absolute path is taken as is, never re-rooted under the notes
            # folder: /app/memory_files/notes/x.md must not become notes/app/memory_files/notes/x.md.
            base_dir = None if filename.startswith("/") else settings.NOTES_STORAGE_DIR
            filename = paths.to_db_filename(filename, base_dir=base_dir)
        filename = Note.db_filename(filename, subject)
    except ValueError as e:
        raise ValueError(f"Invalid filename: {e}")

    user = get_mcp_current_user()
    if user is None:
        raise PermissionError("Not authenticated")

    # A note the caller can't see answers as if the path were free, so its
    # existence doesn't leak; the task then refuses to overwrite it.
    with make_session() as session:
        existing = visible_notes(session, user).filter(Note.filename == filename).first()
        taken = existing is not None
        editable = taken and user_can_edit(user, existing)
    if taken and create_only:
        raise ValueError(f"A note already exists at {filename}")
    if taken and not editable:
        raise PermissionError(f"Only the note's creator or an admin can edit {filename}")

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
                "creator_id": user.id,
                "admin": has_admin_scope(user),
                "create_only": create_only,
            },
        )
    except Exception:
        logger.exception("Error creating note")
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
        query = visible_notes(session, user).filter(Note.filename.isnot(None))

        # Restrict to filenames inside the requested notes subtree.
        # autoescape=True is required: without it, SQLAlchemy's startswith()
        # passes % and _ through unescaped (it just appends "%" to the pattern),
        # which would let path="%/secret" enumerate beyond the requested
        # subtree.
        query = query.filter(
            Note.filename.startswith(db_prefix, autoescape=True),
            ~Note.filename.contains("/.", autoescape=True),
        )

        filenames = [row.filename for row in query.with_entities(Note.filename)]

    return [f"/{filename}" for filename in filenames]
