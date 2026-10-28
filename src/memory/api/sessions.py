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
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func

from sqlalchemy.orm import Session as DBSession

from memory.api.auth import require_rest_access, resolve_user_filter
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

    event_timestamp = parse_iso_datetime(event.timestamp) or datetime.now(timezone.utc)

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


@router.post("/ingest/batch", response_model=BatchIngestResponse)
async def ingest_session_events_batch(
    request: BatchIngestRequest,
    user: User = Depends(require_rest_access),
) -> BatchIngestResponse:
    """
    Ingest multiple session events in a single request.

    Idempotent - duplicate events (by UUID) are skipped.
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


def attr_tokens(key: str):
    return func.coalesce(func.sum(TelemetryEvent.attributes[key].as_integer()), 0)


def per_call_percentile(fraction: float):
    return func.percentile_cont(fraction).within_group(TelemetryEvent.value)


@router.get("/stats/tool-usage", response_model=ToolUsageResponse)
def get_tool_usage_stats(
    from_time: datetime | None = Query(None, alias="from", description="Start time"),
    to_time: datetime | None = Query(None, alias="to", description="End time"),
    user_id: int | None = Query(None, description="Filter by user ID (admin only, omit for all users)"),
    user: User = Depends(require_rest_access),
    db: DBSession = Depends(get_session),
) -> ToolUsageResponse:
    """
    Get aggregated token usage statistics by tool, from tool.token.usage telemetry events.

    Admins (users with '*' or 'admin' scope) can:
    - Omit user_id to see tool usage across all users
    - Specify user_id to filter to a specific user's sessions
    """
    resolved_user_id = resolve_user_filter(user_id, user, db)
    to_time = to_time or datetime.now(timezone.utc)
    from_time = from_time or to_time - timedelta(days=7)

    filters = [
        TelemetryEvent.name == "tool.token.usage",
        TelemetryEvent.timestamp >= from_time,
        TelemetryEvent.timestamp <= to_time,
    ]
    if resolved_user_id is not None:
        filters.append(TelemetryEvent.user_id == resolved_user_id)

    tool_name = func.coalesce(TelemetryEvent.tool_name, "unknown")
    rows = (
        db.query(
            tool_name.label("tool_name"),
            func.count().label("call_count"),
            attr_tokens("input_tokens").label("input_tokens"),
            attr_tokens("output_tokens").label("output_tokens"),
            attr_tokens("cache_read_input_tokens").label("cache_read_tokens"),
            attr_tokens("cache_creation_input_tokens").label("cache_creation_tokens"),
            per_call_percentile(0.5).label("median"),
            per_call_percentile(0.75).label("p75"),
            per_call_percentile(0.9).label("p90"),
            per_call_percentile(0.99).label("p99"),
            func.min(TelemetryEvent.value).label("min"),
            func.max(TelemetryEvent.value).label("max"),
        )
        .filter(*filters)
        .group_by(tool_name)
        .all()
    )
    session_count = (
        db.query(func.count(func.distinct(TelemetryEvent.session_id))).filter(*filters).scalar()
    )

    def stats(r) -> ToolUsageStats:
        tokens = (r.input_tokens, r.output_tokens, r.cache_read_tokens, r.cache_creation_tokens)
        return ToolUsageStats(
            tool_name=r.tool_name,
            call_count=r.call_count,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            cache_read_tokens=r.cache_read_tokens,
            cache_creation_tokens=r.cache_creation_tokens,
            total_tokens=sum(tokens),
            per_call=ToolCallStats(
                median=r.median, p75=r.p75, p90=r.p90, p99=r.p99, min=int(r.min or 0), max=int(r.max or 0)
            ),
        )

    return ToolUsageResponse(
        from_time=from_time.isoformat(),
        to_time=to_time.isoformat(),
        session_count=session_count or 0,
        tools=sorted(map(stats, rows), key=lambda t: t.total_tokens, reverse=True),
    )
