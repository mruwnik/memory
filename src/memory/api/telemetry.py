"""
Telemetry API endpoints for usage tracking.

Provides endpoints for:
- Ingesting OpenTelemetry data
- Querying raw events from Postgres
- Querying aggregated metrics with flexible grouping
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func

from memory.api.auth import get_current_user
from memory.common.db.connection import get_session, make_session
from memory.common.db.models import TelemetryEvent, User
from sqlalchemy.orm import Session as DBSession


def has_admin_scope(user: User) -> bool:
    """Check if user has admin scope (can view all users' data)."""
    user_scopes = user.scopes or []
    return "*" in user_scopes or "admin" in user_scopes


def resolve_user_filter(
    user_id: int | None, current_user: User, db: DBSession
) -> int | None:
    """
    Resolve user_id filter for admin queries.

    Returns:
        - None if admin requests all users (user_id="all" passed as None with special handling)
        - Specific user_id if admin requests specific user
        - current_user.id if non-admin (ignores user_id param)

    Raises:
        HTTPException 404 if requested user doesn't exist
        HTTPException 403 if non-admin tries to view other user's data
    """
    if not has_admin_scope(current_user):
        # Non-admins can only see their own data
        return current_user.id

    if user_id is None:
        # Admin with no filter - return all users
        return None

    # Admin requesting specific user - verify they exist
    target_user = db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    return user_id
from memory.common.telemetry import (
    parse_otlp_json,
    write_events_to_db,
)

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


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
) -> IngestResponse:
    """
    Accept OpenTelemetry data.

    Supports OTLP/HTTP JSON format. Configure your telemetry source with:
    ```
    export OTEL_EXPORTER_OTLP_ENDPOINT=https://your-server/telemetry/ingest
    export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer <your-token>"
    ```

    Authentication: Requires Bearer token (same auth as other endpoints).
    """
    body = await request.body()
    if not body:
        return IngestResponse(status="accepted", events_received=0, events_stored=0)

    # Parse OTLP JSON
    events = parse_otlp_json(body)

    if not events:
        return IngestResponse(status="accepted", events_received=0, events_stored=0)

    # Process in background to avoid blocking the response
    background_tasks.add_task(write_events_to_db, events, user.id)

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

    # Default to last 24 hours if no time range specified
    if from_time is None and to_time is None:
        to_time = datetime.now(timezone.utc)
        from_time = to_time - timedelta(hours=24)
    elif from_time is None:
        assert to_time is not None  # to_time must exist if we're in this branch
        from_time = to_time - timedelta(hours=24)
    elif to_time is None:
        to_time = datetime.now(timezone.utc)

    with make_session() as session:
        query = (
            session.query(TelemetryEvent)
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
                    "attributes": e.attributes,
                    "body": e.body[:500] if e.body else None,
                }
                for e in events
            ],
        }


# Valid group-by columns
VALID_GROUP_BY_COLUMNS = {"source", "tool_name", "session_id", "event_type", "name", "user_id"}


@router.get("/metrics")
def get_aggregated_metrics(
    metric: str = Query(..., description="Metric name (e.g., token.usage, cost.usage)"),
    granularity: int = Query(60, ge=1, le=1440, description="Time bucket size in minutes"),
    from_time: datetime | None = Query(None, alias="from", description="Start time (ISO format)"),
    to_time: datetime | None = Query(None, alias="to", description="End time (ISO format)"),
    source: str | None = Query(None, description="Filter by source"),
    group_by: list[str] = Query(
        default=["source", "tool_name"],
        description="Fields to group by: source, tool_name, session_id, event_type, name, user_id, or attributes.<key>",
    ),
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

    # Default to last 7 days if no time range specified
    if from_time is None and to_time is None:
        to_time = datetime.now(timezone.utc)
        from_time = to_time - timedelta(days=7)
    elif from_time is None:
        assert to_time is not None  # to_time must exist if we're in this branch
        from_time = to_time - timedelta(days=7)
    elif to_time is None:
        to_time = datetime.now(timezone.utc)

    with make_session() as session:
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
            session.query(*select_columns)
            .filter(TelemetryEvent.name == metric)
            .filter(TelemetryEvent.timestamp >= from_time)
            .filter(TelemetryEvent.timestamp <= to_time)
        )

        # Apply user filter
        if resolved_user_id is not None:
            query = query.filter(TelemetryEvent.user_id == resolved_user_id)

        if source:
            query = query.filter(TelemetryEvent.source == source)

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
