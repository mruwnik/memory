"""
API endpoints for coding session management.

Provides endpoints for:
- Ingesting session data from tool hooks (e.g., Claude Code)
- Querying projects and sessions
- Retrieving session transcripts
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import joinedload

from memory.api.auth import get_current_user
from memory.common import settings
from memory.common.db.connection import make_session
from memory.common.db.models import Project, Session, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])


class SessionEvent(BaseModel):
    """A single event/message from a session transcript."""

    uuid: str
    parent_uuid: str | None = None
    timestamp: str
    type: str
    user_type: str | None = None
    message: dict[str, Any] | None = None
    is_meta: bool = False
    is_sidechain: bool = False
    cwd: str | None = None
    session_id: str | None = None
    version: str | None = None
    git_branch: str | None = None


class SessionIngestRequest(BaseModel):
    """Request to ingest a session event."""

    session_id: str = Field(..., description="Session UUID")
    cwd: str | None = Field(None, description="Working directory")
    source: str | None = Field(None, description="Source identifier (hostname, IP)")
    parent_session_id: str | None = Field(
        None, description="Parent session UUID for subagents"
    )
    event: SessionEvent = Field(..., description="The session event to ingest")


class SessionIngestResponse(BaseModel):
    """Response from session ingest."""

    status: str
    session_id: str


class BatchIngestRequest(BaseModel):
    """Request to ingest multiple session events."""

    session_id: str = Field(..., description="Session UUID")
    cwd: str | None = Field(None, description="Working directory")
    source: str | None = Field(None, description="Source identifier (hostname, IP)")
    parent_session_id: str | None = Field(
        None, description="Parent session UUID for subagents"
    )
    events: list[SessionEvent] = Field(..., description="Events to ingest")


class BatchIngestResponse(BaseModel):
    """Response from batch ingest."""

    status: str
    session_id: str
    accepted: int
    duplicates: int


class ProjectResponse(BaseModel):
    """Project information."""

    id: int
    directory: str
    name: str | None
    source: str | None
    created_at: str
    last_accessed_at: str
    session_count: int


class SessionResponse(BaseModel):
    """Session information."""

    session_id: str
    project_id: int | None
    project_directory: str | None
    parent_session_id: str | None
    git_branch: str | None
    tool_version: str | None
    source: str | None
    started_at: str
    ended_at: str | None
    transcript_path: str | None


class TranscriptResponse(BaseModel):
    """Session transcript with pagination."""

    session_id: str
    total_events: int
    offset: int
    limit: int
    events: list[dict[str, Any]]


class ProjectListResponse(BaseModel):
    """List of projects."""

    total: int
    projects: list[ProjectResponse]


class SessionListResponse(BaseModel):
    """List of sessions."""

    total: int
    sessions: list[SessionResponse]


def safe_loads(file: Path, start=0, end=None):
    items = []
    for i, line in enumerate(file.read_text().splitlines()):
        if i < start or not line.strip():
            continue
        if end is not None and i > end:
            return items

        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return items


def get_or_create_project(
    db_session, user_id: int, directory: str, source: str | None = None
) -> Project:
    """Get or create a project for the given directory."""
    project = (
        db_session.query(Project)
        .filter(
            Project.user_id == user_id,
            Project.directory == directory,
        )
        .first()
    )

    if not project:
        project = Project(
            user_id=user_id,
            directory=directory,
            source=source,
        )
        db_session.add(project)
        db_session.flush()
    else:
        project.last_accessed_at = datetime.now(timezone.utc)
        if source and not project.source:
            project.source = source

    return project


def get_or_create_session(
    db_session,
    user_id: int,
    session_uuid: UUID,
    project: Project | None = None,
    parent_session_uuid: str | None = None,
    git_branch: str | None = None,
    tool_version: str | None = None,
    source: str | None = None,
) -> Session:
    """Get or create a session."""
    session = db_session.query(Session).filter(Session.id == session_uuid).first()

    if session:
        return session

    parent_session_id = None
    if parent_session_uuid:
        try:
            parent_session_id = UUID(parent_session_uuid)
        except ValueError:
            pass

    # Determine transcript path (relative to SESSIONS_STORAGE_DIR)
    transcript_path = f"{user_id}/{session_uuid}.jsonl"

    session = Session(
        id=session_uuid,
        user_id=user_id,
        project_id=project.id if project else None,
        parent_session_id=parent_session_id,
        git_branch=git_branch,
        tool_version=tool_version,
        source=source,
        transcript_path=transcript_path,
    )
    db_session.add(session)
    db_session.flush()

    return session


def append_events_to_transcript(session: Session, events: list[SessionEvent]) -> int:
    """Append an event to the session's JSONL transcript file.

    Returns True if event was appended, False if it was a duplicate.
    """
    if not session.transcript_path:
        return False

    transcript_file = settings.SESSIONS_STORAGE_DIR / session.transcript_path
    transcript_file.parent.mkdir(parents=True, exist_ok=True)

    # Check for duplicate UUID
    event_ids = {e.uuid for e in events}
    existing_uuids = set()
    if transcript_file.exists() and event_ids:
        existing_uuids = {i.get("uuid") for i in safe_loads(transcript_file)}

    items = [
        json.dumps(event.model_dump(), default=str)
        for event in events
        if event.uuid not in existing_uuids
    ]
    # Append to file
    with open(transcript_file, "a") as f:
        f.write("\n".join(items) + "\n")

    return len(items)


def read_transcript(
    transcript_path: str, offset: int = 0, limit: int = 100
) -> list[dict[str, Any]]:
    """Read events from a transcript file with pagination."""
    transcript_file = settings.SESSIONS_STORAGE_DIR / transcript_path
    if not transcript_file.exists():
        return []

    return safe_loads(transcript_file, offset, offset + limit)


def save_events(user_id, session_id, parent_id, cwd, source, events) -> int:
    try:
        session_uuid = UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"Invalid session UUID: {session_id}"
        )

    with make_session() as db_session:
        project = None
        if cwd:
            project = get_or_create_project(db_session, user_id, cwd, source)

        # Use first event for session metadata
        first_event = events[0] if events else None
        session = get_or_create_session(
            db_session,
            user_id,
            session_uuid,
            project=project,
            parent_session_uuid=parent_id,
            git_branch=first_event.git_branch if first_event else None,
            tool_version=first_event.version if first_event else None,
            source=source,
        )

        accepted = append_events_to_transcript(session, events)
        db_session.commit()

    return accepted


@router.post("/ingest", response_model=SessionIngestResponse)
async def ingest_session_event(
    request: SessionIngestRequest,
    user: User = Depends(get_current_user),
) -> SessionIngestResponse:
    """
    Ingest a single session event.

    This endpoint is idempotent - calling it multiple times with the same
    event will not create duplicates (based on event UUID).

    Called by tool hooks (e.g., Claude Code SessionEnd) to record session activity.
    """
    save_events(
        user.id,
        request.session_id,
        request.parent_session_id,
        request.cwd,
        request.source,
        [request.event],
    )
    return SessionIngestResponse(
        status="accepted",
        session_id=request.session_id,
    )


@router.post("/ingest/batch", response_model=BatchIngestResponse)
async def ingest_session_events_batch(
    request: BatchIngestRequest,
    user: User = Depends(get_current_user),
) -> BatchIngestResponse:
    """
    Ingest multiple session events in a single request.

    Idempotent - duplicate events (by UUID) are skipped.
    More efficient than calling /ingest for each event.
    """
    accepted = save_events(
        user.id,
        request.session_id,
        request.parent_session_id,
        request.cwd,
        request.source,
        request.events,
    )

    return BatchIngestResponse(
        status="accepted",
        session_id=request.session_id,
        accepted=accepted,
        duplicates=len(request.events) - accepted,
    )


@router.get("/projects", response_model=ProjectListResponse)
def list_projects(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
) -> ProjectListResponse:
    """List all projects for the current user."""
    with make_session() as db_session:
        query = (
            db_session.query(Project)
            .filter(Project.user_id == user.id)
            .order_by(Project.last_accessed_at.desc())
        )

        total = query.count()
        projects = query.offset(offset).limit(limit).all()

        return ProjectListResponse(
            total=total,
            projects=[
                ProjectResponse(
                    id=p.id,
                    directory=p.directory,
                    name=p.name,
                    source=p.source,
                    created_at=p.created_at.isoformat() if p.created_at else None,
                    last_accessed_at=(
                        p.last_accessed_at.isoformat() if p.last_accessed_at else None
                    ),
                    session_count=len(p.sessions) if p.sessions else 0,
                )
                for p in projects
            ],
        )


@router.get("/", response_model=SessionListResponse)
def list_sessions(
    project_id: int | None = Query(None, description="Filter by project ID"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
) -> SessionListResponse:
    """List sessions, optionally filtered by project."""
    with make_session() as db_session:
        query = (
            db_session.query(Session)
            .options(joinedload(Session.project))
            .filter(Session.user_id == user.id)
        )

        if project_id is not None:
            query = query.filter(Session.project_id == project_id)

        query = query.order_by(Session.started_at.desc())

        total = query.count()
        sessions = query.offset(offset).limit(limit).all()

        return SessionListResponse(
            total=total,
            sessions=[
                SessionResponse(
                    session_id=str(s.id),
                    project_id=s.project_id,
                    project_directory=s.project.directory if s.project else None,
                    parent_session_id=str(s.parent_session_id)
                    if s.parent_session_id
                    else None,
                    git_branch=s.git_branch,
                    tool_version=s.tool_version,
                    source=s.source,
                    started_at=s.started_at.isoformat() if s.started_at else None,
                    ended_at=s.ended_at.isoformat() if s.ended_at else None,
                    transcript_path=s.transcript_path,
                )
                for s in sessions
            ],
        )


@router.get("/{session_id}", response_model=TranscriptResponse)
def get_session_transcript(
    session_id: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
) -> TranscriptResponse:
    """
    Get session transcript with pagination.

    Returns events ordered by their position in the transcript.
    """
    try:
        session_uuid = UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session UUID")

    with make_session() as db_session:
        session = (
            db_session.query(Session)
            .filter(
                Session.id == session_uuid,
                Session.user_id == user.id,
            )
            .first()
        )

        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        if not session.transcript_path:
            return TranscriptResponse(
                session_id=str(session.id),
                total_events=0,
                offset=offset,
                limit=limit,
                events=[],
            )

        events = read_transcript(session.transcript_path, offset, limit)

        return TranscriptResponse(
            session_id=str(session.id),
            total_events=len(events),
            offset=offset,
            limit=limit,
            events=events,
        )
