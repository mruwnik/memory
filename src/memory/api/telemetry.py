"""
Telemetry API endpoints for usage tracking.

Provides endpoints for:
- Ingesting OpenTelemetry data
- Querying raw events from Postgres
- Querying aggregated metrics with flexible grouping
"""

import re
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import exists, func
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import joinedload

from memory.api.auth import get_current_user, request_api_key, resolve_user_filter
from memory.common import settings
from memory.common.access_control import has_admin_scope
from memory.common.db.connection import get_session
from memory.common.db.models import APIKey, Session, TelemetryEvent, User
from memory.common.telemetry import (
    parse_otlp_json,
    write_events_to_db,
)

router = APIRouter(prefix="/telemetry", tags=["telemetry"])

# Maximum events extracted from a single OTLP payload. Even within the
# byte cap an OTLP "metrics" payload can fan out to thousands of
# datapoints; bound the in-memory list before it's handed off to a
# background task so a burst of valid-but-fat payloads can't pile up
# write_events_to_db invocations on the asyncio loop.
_MAX_TELEMETRY_EVENTS_PER_REQUEST = 10000

MAX_SESSION_LABEL_CHARS = 40
FIRST_CLAUSE = re.compile(r"[.;:!?](?:\s|$)")
LEADING_PUNCTUATION = re.compile(r"^[\W_]+")
MAX_SESSIONS = 200


def time_window(
    from_time: datetime | None, to_time: datetime | None, default: timedelta
) -> tuple[datetime, datetime]:
    to_time = to_time or datetime.now(timezone.utc)
    return from_time or to_time - default, to_time


class IngestResponse(BaseModel):
    """Response from the ingest endpoint."""

    status: str
    events_received: int
    events_stored: int


@router.post("/ingest", response_model=IngestResponse)
@router.post("/v1/metrics", response_model=IngestResponse)
@router.post("/v1/logs", response_model=IngestResponse)
@router.post("/v1/traces", response_model=IngestResponse)
async def ingest_telemetry(
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> IngestResponse:
    """
    Accept OpenTelemetry data.

    Supports OTLP/HTTP JSON format. Configure your telemetry source with:
    ```
    export OTEL_EXPORTER_OTLP_ENDPOINT=https://your-server/telemetry/ingest
    export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer <your-token>"
    ```

    Authentication: Requires Bearer token (same auth as other endpoints).

    Bounded by ``settings.MAX_TELEMETRY_PAYLOAD_BYTES`` (default 5 MiB)
    so a misbehaving collector — or an authenticated attacker — can't
    POST gigabytes of OTLP and OOM the API process while the body is
    buffered and parsed before any rate limit fires.
    """
    max_bytes = settings.MAX_TELEMETRY_PAYLOAD_BYTES
    # Trust the declared Content-Length when present; bail before reading.
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            declared_int = int(declared)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")
        if declared_int > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Telemetry payload exceeds {max_bytes} bytes; "
                    "split into smaller batches or raise MAX_TELEMETRY_PAYLOAD_BYTES."
                ),
            )

    # Even with a small/missing Content-Length, defend against chunked or
    # lying clients by reading in chunks and bailing if the cumulative
    # size crosses the cap.
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        received += len(chunk)
        if received > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Telemetry payload exceeds {max_bytes} bytes; "
                    "split into smaller batches or raise MAX_TELEMETRY_PAYLOAD_BYTES."
                ),
            )
        chunks.append(chunk)
    body = b"".join(chunks)

    if not body:
        return IngestResponse(status="accepted", events_received=0, events_stored=0)

    # Parse OTLP JSON
    events = parse_otlp_json(body)

    if not events:
        return IngestResponse(status="accepted", events_received=0, events_stored=0)

    # Cap event count per request — a small payload of nested OTLP can
    # still expand to a large datapoint list. Truncate rather than fail
    # so a chatty collector doesn't lose the whole batch.
    if len(events) > _MAX_TELEMETRY_EVENTS_PER_REQUEST:
        events = events[:_MAX_TELEMETRY_EVENTS_PER_REQUEST]

    # Process in background to avoid blocking the response
    api_key = request_api_key(request, db)
    background_tasks.add_task(
        write_events_to_db, events, user.id, api_key.id if api_key else None
    )

    return IngestResponse(
        status="accepted",
        events_received=len(events),
        events_stored=len(events),  # Optimistic - actual count may differ
    )


@router.get("/raw")
def get_raw_events(
    event_type: str | None = Query(None, description="Filter by type (metric or log)"),
    name: str | None = Query(None, description="Filter by event name"),
    session_id: str | None = Query(None, description="Filter by session ID"),
    source: str | None = Query(None, description="Filter by source (e.g., model name)"),
    from_time: datetime | None = Query(None, alias="from", description="Start time (ISO format)"),
    to_time: datetime | None = Query(None, alias="to", description="End time (ISO format)"),
    api_key_id: int | None = Query(None, description="Filter by the API key that sent the events"),
    limit: int = Query(100, ge=1, le=1000, description="Max events to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    user_id: int | None = Query(None, description="Filter by user ID (admin only, omit for all users)"),
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict:
    """
    Query raw telemetry events for debugging and detailed analysis.

    By default returns events for the authenticated user only.
    Admins (users with '*' or 'admin' scope) can:
    - Omit user_id to see all users' events
    - Specify user_id to filter to a specific user
    """
    resolved_user_id = resolve_user_filter(user_id, user, db)
    from_time, to_time = time_window(from_time, to_time, timedelta(hours=24))

    query = (
        db.query(TelemetryEvent)
        .filter(TelemetryEvent.timestamp >= from_time)
        .filter(TelemetryEvent.timestamp <= to_time)
    )

    # Apply user filter
    if resolved_user_id is not None:
        query = query.filter(TelemetryEvent.user_id == resolved_user_id)

    if event_type:
        query = query.filter(TelemetryEvent.event_type == event_type)
    if name:
        query = query.filter(TelemetryEvent.name == name)
    if session_id:
        query = query.filter(TelemetryEvent.session_id == session_id)
    if source:
        query = query.filter(TelemetryEvent.source == source)
    if api_key_id is not None:
        query = query.filter(TelemetryEvent.api_key_id == api_key_id)

    # Get total count
    total = query.count()

    # Get paginated results
    events = (
        query.order_by(TelemetryEvent.timestamp.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "from": from_time.isoformat(),
        "to": to_time.isoformat(),
        "events": [
            {
                "id": e.id,
                "timestamp": e.timestamp.isoformat(),
                "event_type": e.event_type,
                "name": e.name,
                "value": e.value,
                "session_id": e.session_id,
                "source": e.source,
                "tool_name": e.tool_name,
                "api_key_id": e.api_key_id,
                "attributes": e.attributes,
                "body": e.body[:500] if e.body else None,
            }
            for e in events
        ],
    }


# Valid group-by columns
VALID_GROUP_BY_COLUMNS = {
    "source",
    "tool_name",
    "session_id",
    "event_type",
    "name",
    "user_id",
    "api_key_id",
}


@router.get("/metrics")
def get_aggregated_metrics(
    metric: str = Query(..., description="Metric name (e.g., token.usage, cost.usage)"),
    granularity: int = Query(60, ge=1, le=1440, description="Time bucket size in minutes"),
    from_time: datetime | None = Query(None, alias="from", description="Start time (ISO format)"),
    to_time: datetime | None = Query(None, alias="to", description="End time (ISO format)"),
    source: str | None = Query(None, description="Filter by source"),
    group_by: list[str] = Query(
        default=["source", "tool_name"],
        description="Fields to group by: source, tool_name, session_id, event_type, name, user_id, api_key_id, or attributes.<key>",
    ),
    api_key_id: int | None = Query(None, description="Filter by the API key that sent the events"),
    user_id: int | None = Query(None, description="Filter by user ID (admin only, omit for all users)"),
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict:
    """
    Query aggregated metrics over time.

    Returns time series data suitable for charting.
    Aggregates are computed from raw events on-the-fly.

    The group_by parameter supports both column names (source, tool_name, session_id,
    event_type, name, user_id) and JSONB attribute keys using the format "attributes.<key>".

    Admins (users with '*' or 'admin' scope) can:
    - Omit user_id to see aggregated metrics across all users
    - Specify user_id to filter to a specific user
    - Use 'user_id' in group_by to see per-user breakdowns
    """
    resolved_user_id = resolve_user_filter(user_id, user, db)
    from_time, to_time = time_window(from_time, to_time, timedelta(days=7))

    # Build time bucket using date_trunc with interval
    # PostgreSQL date_trunc supports: microseconds, milliseconds, second, minute, hour, day, week, month, quarter, year
    if granularity >= 1440:
        trunc_interval = "day"
    elif granularity >= 60:
        trunc_interval = "hour"
    else:
        trunc_interval = "minute"

    trunc_func = func.date_trunc(trunc_interval, TelemetryEvent.timestamp)

    # Build group-by columns
    group_columns = [trunc_func]
    select_columns = [
        trunc_func.label("bucket"),
        func.count().label("count"),
        func.sum(TelemetryEvent.value).label("sum_value"),
        func.min(TelemetryEvent.value).label("min_value"),
        func.max(TelemetryEvent.value).label("max_value"),
    ]

    # Track which fields we're grouping by for the response
    group_by_fields = []

    for field in group_by:
        if field in VALID_GROUP_BY_COLUMNS:
            col = getattr(TelemetryEvent, field)
            select_columns.append(col)
            group_columns.append(col)
            group_by_fields.append(field)
        elif field.startswith("attributes."):
            # Extract JSONB key
            attr_key = field[11:]  # Remove "attributes." prefix
            json_col = TelemetryEvent.attributes[attr_key].astext.label(f"attr_{attr_key}")
            select_columns.append(json_col)
            group_columns.append(TelemetryEvent.attributes[attr_key].astext)
            group_by_fields.append(field)

    query = (
        db.query(*select_columns)
        .filter(TelemetryEvent.name == metric)
        .filter(TelemetryEvent.timestamp >= from_time)
        .filter(TelemetryEvent.timestamp <= to_time)
    )

    # Apply user filter
    if resolved_user_id is not None:
        query = query.filter(TelemetryEvent.user_id == resolved_user_id)

    if source:
        query = query.filter(TelemetryEvent.source == source)
    if api_key_id is not None:
        query = query.filter(TelemetryEvent.api_key_id == api_key_id)

    results = (
        query.group_by(*group_columns)
        .order_by(trunc_func)
        .all()
    )

    # Build response data
    data = []
    for r in results:
        row = {
            "timestamp": r.bucket.isoformat() if r.bucket else None,
            "count": r.count,
            "sum": r.sum_value,
            "min": r.min_value,
            "max": r.max_value,
        }
        # Add group-by fields
        for i, field in enumerate(group_by_fields):
            # The group-by columns start at index 5 in the result tuple
            val = r[5 + i]
            if field.startswith("attributes."):
                row[field] = val
            else:
                row[field] = val
        data.append(row)

    return {
        "metric": metric,
        "granularity_minutes": granularity,
        "from": from_time.isoformat(),
        "to": to_time.isoformat(),
        "group_by": group_by_fields,
        "data": data,
    }


class TelemetryUser(BaseModel):
    """User with telemetry data."""

    id: int
    name: str
    email: str


@router.get("/users", response_model=list[TelemetryUser])
def get_users_with_telemetry(
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> list[TelemetryUser]:
    """
    Get list of users who have telemetry data.

    Only available to admin users. Returns users who have at least one
    telemetry event recorded.
    """
    if not has_admin_scope(user):
        # Non-admins only see themselves if they have telemetry
        has_data = db.query(TelemetryEvent).filter(TelemetryEvent.user_id == user.id).first()
        if has_data:
            return [TelemetryUser(id=user.id, name=user.name or "", email=user.email or "")]
        return []

    # Get users who have at least one telemetry event (single query with EXISTS)
    users = (
        db.query(User)
        .filter(
            exists()
            .where(TelemetryEvent.user_id == User.id)
        )
        .all()
    )

    return [
        TelemetryUser(id=u.id, name=u.name or "", email=u.email or "")
        for u in users
    ]


def session_label(
    session_id: str,
    summary: str | None,
    project_dir: str | None,
    started_at: datetime | None,
) -> str:
    """A short bar label: the summary's first clause, else project and date, else the id prefix."""
    first_line = LEADING_PUNCTUATION.sub("", summary or "").split("\n", 1)[0]
    clause = FIRST_CLAUSE.split(first_line, maxsplit=1)[0].strip()
    if clause and len(clause) <= MAX_SESSION_LABEL_CHARS:
        return clause
    if clause:
        cut = clause[: MAX_SESSION_LABEL_CHARS + 1].rsplit(" ", 1)[0]
        return cut[:MAX_SESSION_LABEL_CHARS] + "…"
    if project_dir:
        name = PurePosixPath(project_dir).name
        return f"{name} · {started_at.date().isoformat()}" if started_at else name
    return session_id[:8]


def parse_uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None


class SessionUsage(BaseModel):
    session_id: str
    label: str
    summary: str | None
    project: str | None
    total_tokens: float
    total_cost: float
    event_count: int
    first_seen: str
    last_seen: str


class SessionUsageResponse(BaseModel):
    from_time: str
    to_time: str
    sessions: list[SessionUsage]


@router.get("/sessions", response_model=SessionUsageResponse)
def get_session_usage(
    from_time: datetime | None = Query(None, alias="from", description="Start time (ISO format)"),
    to_time: datetime | None = Query(None, alias="to", description="End time (ISO format)"),
    api_key_id: int | None = Query(None, description="Filter by the API key that sent the events"),
    user_id: int | None = Query(None, description="Filter by user ID (admin only, omit for all users)"),
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> SessionUsageResponse:
    """
    Token and cost totals per Claude Code session, largest first.

    Each session is named from its stored summary or project. Names come only
    from sessions the caller owns, matching the sessions API, so an admin
    viewing other users' telemetry sees their session ids but not their summaries.
    """
    resolved_user_id = resolve_user_filter(user_id, user, db)
    from_time, to_time = time_window(from_time, to_time, timedelta(days=7))

    is_tokens = TelemetryEvent.name == "token.usage"
    is_cost = TelemetryEvent.name == "cost.usage"
    tokens = func.coalesce(func.sum(TelemetryEvent.value).filter(is_tokens), 0)
    query = (
        db.query(
            TelemetryEvent.session_id,
            tokens.label("tokens"),
            func.coalesce(func.sum(TelemetryEvent.value).filter(is_cost), 0).label("cost"),
            func.count().filter(is_tokens).label("events"),
            func.min(TelemetryEvent.timestamp).label("first_seen"),
            func.max(TelemetryEvent.timestamp).label("last_seen"),
        )
        .filter(TelemetryEvent.name.in_(["token.usage", "cost.usage"]))
        .filter(TelemetryEvent.session_id.isnot(None))
        .filter(TelemetryEvent.timestamp >= from_time)
        .filter(TelemetryEvent.timestamp <= to_time)
    )
    if resolved_user_id is not None:
        query = query.filter(TelemetryEvent.user_id == resolved_user_id)
    if api_key_id is not None:
        query = query.filter(TelemetryEvent.api_key_id == api_key_id)
    rows = (
        query.group_by(TelemetryEvent.session_id)
        .order_by(tokens.desc())
        .limit(MAX_SESSIONS)
        .all()
    )

    ids = {uuid for r in rows if (uuid := parse_uuid(r.session_id))}
    owned = {
        str(s.id): s
        for s in db.query(Session)
        .options(joinedload(Session.coding_project))
        .filter(Session.id.in_(ids), Session.user_id == user.id)
    }

    def usage(row) -> SessionUsage:
        session = owned.get(row.session_id)
        summary = session.summary if session else None
        project = (
            session.coding_project.directory if session and session.coding_project else None
        )
        started_at = session.started_at if session else None
        return SessionUsage(
            session_id=row.session_id,
            label=session_label(row.session_id, summary, project, started_at),
            summary=summary,
            project=project,
            total_tokens=row.tokens,
            total_cost=row.cost,
            event_count=row.events,
            first_seen=row.first_seen.isoformat(),
            last_seen=row.last_seen.isoformat(),
        )

    return SessionUsageResponse(
        from_time=from_time.isoformat(),
        to_time=to_time.isoformat(),
        sessions=[usage(r) for r in rows],
    )


class TelemetryAPIKey(BaseModel):
    id: int
    name: str | None
    key_type: str
    user_id: int
    revoked: bool


@router.get("/api-keys", response_model=list[TelemetryAPIKey])
def get_api_keys_with_telemetry(
    user_id: int | None = Query(None, description="Filter by key owner (admin only, omit for all users)"),
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> list[TelemetryAPIKey]:
    """API keys that have sent at least one telemetry event, for the key filter."""
    resolved_user_id = resolve_user_filter(user_id, user, db)
    query = db.query(APIKey).filter(exists().where(TelemetryEvent.api_key_id == APIKey.id))
    if resolved_user_id is not None:
        query = query.filter(APIKey.user_id == resolved_user_id)
    return [
        TelemetryAPIKey(
            id=k.id, name=k.name, key_type=k.key_type, user_id=k.user_id, revoked=k.revoked
        )
        for k in query.order_by(APIKey.id)
    ]
