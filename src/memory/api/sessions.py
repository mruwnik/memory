"""
API endpoints for coding session management.

Provides endpoints for:
- Ingesting session data from tool hooks (e.g., Claude Code)
- Querying projects and sessions
- Retrieving session transcripts
"""

import fcntl
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from sqlalchemy.orm import Session as DBSession

from memory.api.auth import get_current_user, resolve_user_filter
from memory.common import settings
from memory.common.dates import parse_iso_datetime
from memory.common.db.connection import get_session, make_session
from memory.common.db.models import CodingProject, Session, TelemetryEvent, User

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


class CodingProjectResponse(BaseModel):
    """CodingProject information."""

    id: int
    directory: str
    name: str | None
    source: str | None
    created_at: str | None
    last_accessed_at: str | None
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
    started_at: str | None
    ended_at: str | None
    transcript_path: str | None


class TranscriptResponse(BaseModel):
    """Session transcript with pagination."""

    session_id: str
    total_events: int
    offset: int
    limit: int
    events: list[dict[str, Any]]


class ToolCallStats(BaseModel):
    """Per-call statistics for token usage."""

    median: float
    p75: float
    p90: float
    p99: float
    min: int
    max: int


class ToolUsageStats(BaseModel):
    """Token usage statistics for a single tool."""

    tool_name: str
    call_count: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    total_tokens: int
    per_call: ToolCallStats | None = None


class ToolUsageResponse(BaseModel):
    """Aggregated tool usage statistics."""

    from_time: str
    to_time: str
    session_count: int
    tools: list[ToolUsageStats]


class CodingProjectListResponse(BaseModel):
    """List of projects."""

    total: int
    projects: list[CodingProjectResponse]


class SessionListResponse(BaseModel):
    """List of sessions."""

    total: int
    sessions: list[SessionResponse]


def safe_loads(file: Path, start=0, end=None):
    items = []
    bad_lines = 0
    for i, line in enumerate(file.read_text().splitlines()):
        if i < start or not line.strip():
            continue
        # `end` is exclusive (read_transcript passes offset+limit). Using `>`
        # here was off-by-one and returned one extra row per request.
        if end is not None and i >= end:
            return items

        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            bad_lines += 1
    if bad_lines:
        # Surface corruption rather than silently returning a short list — a
        # transcript with N malformed JSONL lines used to drop those rows
        # without any indication, hiding ingest bugs.
        logger.warning(
            "transcript %s: %d unparseable JSON line(s) skipped",
            file,
            bad_lines,
        )
    return items


@dataclass
class ToolCall:
    """Single tool invocation with token attribution.

    When an assistant message invokes N tools, the API-reported usage
    counts are not broken down per tool — we attribute them by even
    division with remainder going to the first tools (deterministic and
    keeps the sum exact).
    """

    tool_name: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_creation_tokens
        )


def extract_tool_calls_from_message(message: dict[str, Any]) -> list[ToolCall]:
    """Extract one ``ToolCall`` per ``tool_use`` block in an assistant message.

    Token totals reported by the API for the whole assistant turn are
    split evenly across the tools; the divmod remainder is distributed
    to the first tools so the per-call sum equals the API total exactly
    (no silent rounding loss).

    NOTE: When a single assistant message invokes multiple tools, the
    API does not tell us which tool consumed which tokens. Per-tool
    numbers are therefore an approximation — fine for aggregate metrics,
    but individual costs should be treated as estimates when N > 1.
    """
    content = message.get("content") or []
    usage = message.get("usage") or {}
    if not content or not usage:
        return []

    tool_names = [
        block.get("name", "unknown")
        for block in content
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]
    if not tool_names:
        return []

    n = len(tool_names)
    in_b, in_r = divmod(usage.get("input_tokens", 0), n)
    out_b, out_r = divmod(usage.get("output_tokens", 0), n)
    cr_b, cr_r = divmod(usage.get("cache_read_input_tokens", 0), n)
    cc_b, cc_r = divmod(usage.get("cache_creation_input_tokens", 0), n)

    return [
        ToolCall(
            tool_name=name,
            input_tokens=in_b + (1 if i < in_r else 0),
            output_tokens=out_b + (1 if i < out_r else 0),
            cache_read_tokens=cr_b + (1 if i < cr_r else 0),
            cache_creation_tokens=cc_b + (1 if i < cc_r else 0),
        )
        for i, name in enumerate(tool_names)
    ]


def parse_event_timestamp(timestamp_str: str | None) -> datetime | None:
    """Thin wrapper around :func:`memory.common.dates.parse_iso_datetime`
    kept for callers that imported the local name. Returns None on
    missing/invalid input.
    """
    return parse_iso_datetime(timestamp_str)


def count_transcript_events(transcript_path: str) -> int:
    """Count total events in a transcript file."""
    transcript_file = settings.SESSIONS_STORAGE_DIR / transcript_path
    if not transcript_file.exists():
        return 0

    count = 0
    with open(transcript_file) as f:
        for line in f:
            if line.strip():
                count += 1
    return count


def get_or_create_project(
    db_session, user_id: int, directory: str, source: str | None = None
) -> CodingProject:
    """Get or create a project for the given directory."""
    project = (
        db_session.query(CodingProject)
        .filter(
            CodingProject.user_id == user_id,
            CodingProject.directory == directory,
        )
        .first()
    )

    if not project:
        project = CodingProject(
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
    project: CodingProject | None = None,
    parent_session_uuid: str | None = None,
    git_branch: str | None = None,
    tool_version: str | None = None,
    source: str | None = None,
) -> Session:
    """Get or create a session."""
    session = db_session.query(Session).filter(Session.id == session_uuid).first()

    if session:
        # Verify ownership before allowing event injection.
        # A user who knows another user's session UUID could otherwise corrupt
        # their transcript by sending events for that session.
        if session.user_id != user_id:
            raise HTTPException(
                status_code=403,
                detail="Not authorized for this session",
            )
        return session

    parent_session_id = None
    if parent_session_uuid:
        try:
            parent_session_id = UUID(parent_session_uuid)
        except ValueError:
            pass

    # Determine transcript path (relative to SESSIONS_STORAGE_DIR)
    project_id = project and project.id
    transcript_path = f"{project_id}/{session_uuid}.jsonl"

    session = Session(
        id=session_uuid,
        user_id=user_id,
        coding_project_id=project_id,
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
    """Append events to the session's JSONL transcript file.

    Uses file locking to prevent concurrent write races.

    Returns the number of events appended (excluding duplicates).

    Note: Uses fcntl for file locking which is Unix-only. This is intentional
    as the application runs in Linux Docker containers. If Windows support is
    needed, consider using the 'filelock' library instead.
    """
    if not session.transcript_path:
        return 0

    transcript_file = settings.SESSIONS_STORAGE_DIR / session.transcript_path
    transcript_file.parent.mkdir(parents=True, exist_ok=True)

    event_ids = {e.uuid for e in events}

    # Use file locking to prevent concurrent writes
    # Open with 'a+' to create if not exists, then lock
    with open(transcript_file, "a+") as f:
        # Acquire exclusive lock (blocks until available)
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            # Read existing UUIDs while holding lock
            f.seek(0)
            existing_uuids = set()
            if event_ids:
                for line in f:
                    if line.strip():
                        try:
                            data = json.loads(line)
                            if uuid := data.get("uuid"):
                                existing_uuids.add(uuid)
                        except json.JSONDecodeError:
                            pass

            # Filter out duplicates and write new events
            items = [
                json.dumps(event.model_dump(), default=str)
                for event in events
                if event.uuid not in existing_uuids
            ]

            if items:
                # Seek to end and append
                f.seek(0, 2)  # Seek to end
                f.write("\n".join(items) + "\n")
                f.flush()

            return len(items)
        finally:
            # Release lock
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def read_transcript(
    transcript_path: str, offset: int = 0, limit: int = 100
) -> list[dict[str, Any]]:
    """Read events from a transcript file with pagination."""
    transcript_file = settings.SESSIONS_STORAGE_DIR / transcript_path
    if not transcript_file.exists():
        return []

    return safe_loads(transcript_file, offset, offset + limit)


def extract_tool_usage_telemetry(
    event: SessionEvent,
    user_id: int,
    session_id: str,
) -> list[TelemetryEvent]:
    """
    Extract tool usage telemetry from an assistant event with tool_use blocks.

    Returns a list of TelemetryEvent records (one per tool call) to be saved.
    """
    if event.type != "assistant":
        return []

    tool_calls = extract_tool_calls_from_message(event.message or {})
    if not tool_calls:
        return []

    event_timestamp = parse_event_timestamp(event.timestamp) or datetime.now(timezone.utc)

    return [
        TelemetryEvent(
            timestamp=event_timestamp,
            user_id=user_id,
            event_type="metric",
            name="tool.token.usage",
            value=float(call.total_tokens),
            session_id=session_id,
            tool_name=call.tool_name,
            attributes={
                "input_tokens": call.input_tokens,
                "output_tokens": call.output_tokens,
                "cache_read_input_tokens": call.cache_read_tokens,
                "cache_creation_input_tokens": call.cache_creation_tokens,
            },
        )
        for call in tool_calls
    ]


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

        # Extract and save tool usage telemetry
        for event in events:
            telemetry_events = extract_tool_usage_telemetry(event, user_id, session_id)
            for te in telemetry_events:
                db_session.add(te)

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


@router.get("/projects", response_model=CodingProjectListResponse)
def list_projects(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
) -> CodingProjectListResponse:
    """List all projects for the current user."""
    with make_session() as db_session:
        query = (
            db_session.query(CodingProject)
            .filter(CodingProject.user_id == user.id)
            .order_by(CodingProject.last_accessed_at.desc())
        )

        total = query.count()
        projects = query.offset(offset).limit(limit).all()

        return CodingProjectListResponse(
            total=total,
            projects=[
                CodingProjectResponse(
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
            .options(joinedload(Session.coding_project))
            .filter(Session.user_id == user.id)
        )

        if project_id is not None:
            query = query.filter(Session.coding_project_id == project_id)

        query = query.order_by(Session.started_at.desc())

        total = query.count()
        sessions = query.offset(offset).limit(limit).all()

        return SessionListResponse(
            total=total,
            sessions=[
                SessionResponse(
                    session_id=str(s.id),
                    project_id=s.coding_project_id,
                    project_directory=s.coding_project.directory if s.coding_project else None,
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
        total = count_transcript_events(session.transcript_path)

        return TranscriptResponse(
            session_id=str(session.id),
            total_events=total,
            offset=offset,
            limit=limit,
            events=events,
        )


def extract_tool_usage_from_transcript(
    transcript_path: str,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
) -> dict[str, dict]:
    """Extract tool usage from a session transcript.

    Returns dict mapping tool_name -> {
        call_count, input_tokens, output_tokens, ...,
        per_call_totals: list[int]  # total tokens per individual call
    }

    Token attribution is the same as ``extract_tool_usage_telemetry``
    (divmod with remainder distribution). Previously this path used
    plain integer division so per-tool totals silently rounded down,
    producing different numbers than the telemetry path for the same
    underlying message.
    """
    transcript_file = settings.SESSIONS_STORAGE_DIR / transcript_path
    if not transcript_file.exists():
        return {}

    tool_stats: dict[str, dict] = {}

    for event in safe_loads(transcript_file):
        # Only process assistant messages with tool_use
        if event.get("type") != "assistant":
            continue

        # Filter by timestamp if provided. Unparseable timestamps fall
        # through to inclusion (matches prior behaviour: don't drop
        # events because of a flaky clock).
        if from_time or to_time:
            event_time = parse_event_timestamp(event.get("timestamp"))
            if event_time is not None:
                if from_time and event_time < from_time:
                    continue
                if to_time and event_time > to_time:
                    continue

        for call in extract_tool_calls_from_message(event.get("message") or {}):
            stats = tool_stats.setdefault(
                call.tool_name,
                {
                    "call_count": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_creation_tokens": 0,
                    "per_call_totals": [],
                },
            )
            stats["call_count"] += 1
            stats["input_tokens"] += call.input_tokens
            stats["output_tokens"] += call.output_tokens
            stats["cache_read_tokens"] += call.cache_read_tokens
            stats["cache_creation_tokens"] += call.cache_creation_tokens
            stats["per_call_totals"].append(call.total_tokens)

    return tool_stats


def calculate_percentile(sorted_values: list[int], percentile: float) -> float:
    """Calculate percentile from sorted values using linear interpolation."""
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    if n == 1:
        return float(sorted_values[0])

    # Use linear interpolation (same as numpy's default)
    k = (n - 1) * percentile / 100
    f = int(k)
    c = f + 1 if f + 1 < n else f
    return sorted_values[f] + (k - f) * (sorted_values[c] - sorted_values[f])


def compute_call_stats(per_call_totals: list[int]) -> ToolCallStats | None:
    """Compute per-call statistics from a list of token totals."""
    if not per_call_totals:
        return None

    sorted_vals = sorted(per_call_totals)
    return ToolCallStats(
        median=calculate_percentile(sorted_vals, 50),
        p75=calculate_percentile(sorted_vals, 75),
        p90=calculate_percentile(sorted_vals, 90),
        p99=calculate_percentile(sorted_vals, 99),
        min=sorted_vals[0],
        max=sorted_vals[-1],
    )


def get_tool_usage_stats_from_transcripts(
    from_time: datetime,
    to_time: datetime,
    resolved_user_id: int | None,
    db: DBSession,
) -> ToolUsageResponse:
    """
    Fallback: Get tool usage by parsing transcript files.

    Used during the transition period before telemetry backfill is complete.
    """
    from sqlalchemy import or_

    # Get sessions that overlap with the time range
    query = db.query(Session).filter(
        Session.started_at <= to_time,
        or_(
            Session.ended_at.is_(None),
            Session.ended_at >= from_time,
        ),
    )

    if resolved_user_id is not None:
        query = query.filter(Session.user_id == resolved_user_id)

    sessions = query.all()

    # Aggregate tool usage across all sessions
    aggregated: dict[str, dict] = {}

    for session in sessions:
        if not session.transcript_path:
            continue

        session_stats = extract_tool_usage_from_transcript(
            session.transcript_path, from_time, to_time
        )

        for tool_name, stats in session_stats.items():
            if tool_name not in aggregated:
                aggregated[tool_name] = {
                    "call_count": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_creation_tokens": 0,
                    "per_call_totals": [],
                }

            aggregated[tool_name]["call_count"] += stats["call_count"]
            aggregated[tool_name]["input_tokens"] += stats["input_tokens"]
            aggregated[tool_name]["output_tokens"] += stats["output_tokens"]
            aggregated[tool_name]["cache_read_tokens"] += stats["cache_read_tokens"]
            aggregated[tool_name]["cache_creation_tokens"] += stats["cache_creation_tokens"]
            aggregated[tool_name]["per_call_totals"].extend(stats.get("per_call_totals", []))

    # Convert to response format
    tools = [
        ToolUsageStats(
            tool_name=name,
            call_count=stats["call_count"],
            input_tokens=stats["input_tokens"],
            output_tokens=stats["output_tokens"],
            cache_read_tokens=stats["cache_read_tokens"],
            cache_creation_tokens=stats["cache_creation_tokens"],
            total_tokens=(
                stats["input_tokens"]
                + stats["output_tokens"]
                + stats["cache_read_tokens"]
                + stats["cache_creation_tokens"]
            ),
            per_call=compute_call_stats(stats["per_call_totals"]),
        )
        for name, stats in aggregated.items()
    ]

    tools.sort(key=lambda t: t.total_tokens, reverse=True)

    return ToolUsageResponse(
        from_time=from_time.isoformat(),
        to_time=to_time.isoformat(),
        session_count=len(sessions),
        tools=tools,
    )


@router.get("/stats/tool-usage", response_model=ToolUsageResponse)
def get_tool_usage_stats(
    from_time: datetime | None = Query(None, alias="from", description="Start time"),
    to_time: datetime | None = Query(None, alias="to", description="End time"),
    user_id: int | None = Query(None, description="Filter by user ID (admin only, omit for all users)"),
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> ToolUsageResponse:
    """
    Get aggregated token usage statistics by tool.

    Queries the telemetry_events table for tool.token.usage events.

    Admins (users with '*' or 'admin' scope) can:
    - Omit user_id to see tool usage across all users
    - Specify user_id to filter to a specific user's sessions
    """
    resolved_user_id = resolve_user_filter(user_id, user, db)

    # Default to last 7 days
    if to_time is None:
        to_time = datetime.now(timezone.utc)
    if from_time is None:
        from_time = to_time - timedelta(days=7)

    # Query tool.token.usage events (one row per tool call)
    # value = total tokens, attributes has breakdown
    query = db.query(TelemetryEvent).filter(
        TelemetryEvent.name == "tool.token.usage",
        TelemetryEvent.timestamp >= from_time,
        TelemetryEvent.timestamp <= to_time,
    )

    # Apply user filter
    if resolved_user_id is not None:
        query = query.filter(TelemetryEvent.user_id == resolved_user_id)

    events = query.all()

    # Aggregate by tool
    aggregated: dict[str, dict] = {}
    for event in events:
        tool_name = event.tool_name or "unknown"
        if tool_name not in aggregated:
            aggregated[tool_name] = {
                "call_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "per_call_totals": [],
            }

        attrs = event.attributes or {}
        aggregated[tool_name]["call_count"] += 1
        aggregated[tool_name]["input_tokens"] += attrs.get("input_tokens", 0)
        aggregated[tool_name]["output_tokens"] += attrs.get("output_tokens", 0)
        aggregated[tool_name]["cache_read_tokens"] += attrs.get("cache_read_input_tokens", 0)
        aggregated[tool_name]["cache_creation_tokens"] += attrs.get("cache_creation_input_tokens", 0)
        aggregated[tool_name]["per_call_totals"].append(int(event.value or 0))

    # Get session count for the time range
    session_count_query = db.query(func.count(func.distinct(TelemetryEvent.session_id))).filter(
        TelemetryEvent.name == "tool.token.usage",
        TelemetryEvent.timestamp >= from_time,
        TelemetryEvent.timestamp <= to_time,
    )
    if resolved_user_id is not None:
        session_count_query = session_count_query.filter(TelemetryEvent.user_id == resolved_user_id)
    session_count = session_count_query.scalar() or 0

    # If no tool.token.usage events yet, fall back to parsing transcripts
    # This handles the transition period before backfill
    if not aggregated:
        return get_tool_usage_stats_from_transcripts(from_time, to_time, resolved_user_id, db)

    # Convert to response format
    tools = [
        ToolUsageStats(
            tool_name=name,
            call_count=stats["call_count"],
            input_tokens=stats["input_tokens"],
            output_tokens=stats["output_tokens"],
            cache_read_tokens=stats["cache_read_tokens"],
            cache_creation_tokens=stats["cache_creation_tokens"],
            total_tokens=(
                stats["input_tokens"]
                + stats["output_tokens"]
                + stats["cache_read_tokens"]
                + stats["cache_creation_tokens"]
            ),
            per_call=compute_call_stats(stats["per_call_totals"]),
        )
        for name, stats in aggregated.items()
    ]

    # Sort by total tokens descending
    tools.sort(key=lambda t: t.total_tokens, reverse=True)

    return ToolUsageResponse(
        from_time=from_time.isoformat(),
        to_time=to_time.isoformat(),
        session_count=session_count,
        tools=tools,
    )
