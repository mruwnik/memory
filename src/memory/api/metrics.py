"""
Metrics API endpoints for querying profiling data.

Provides endpoints to query task timing, MCP call timing,
system metrics, and aggregated summaries.
"""

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, over, text

from memory.api.auth import get_current_user
from memory.common.db.connection import make_session
from memory.common.db.models import MetricEvent, User

# Metrics endpoints require authentication as they may expose timing data
# with user identifiers in labels.
router = APIRouter(prefix="/api/metrics", tags=["metrics"])


class MetricSummary(BaseModel):
    """Summary statistics for a metric."""

    name: str
    count: int
    avg_duration_ms: float | None
    min_duration_ms: float | None
    max_duration_ms: float | None
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None
    success_count: int
    failure_count: int


class MetricEvent_Response(BaseModel):
    """Individual metric event."""

    id: int
    timestamp: datetime
    metric_type: str
    name: str
    duration_ms: float | None
    status: str | None
    labels: dict
    value: float | None


class MetricsSummaryResponse(BaseModel):
    """Response for summary endpoints."""

    metric_type: str
    period_hours: int
    metrics: list[MetricSummary]


class SystemMetricsResponse(BaseModel):
    """Response for system metrics."""

    latest: dict[str, float]
    history: list[MetricEvent_Response]


@router.get("/summary")
def get_metrics_summary(
    metric_type: str | None = Query(None, description="Filter by type (task, mcp_call, system)"),
    hours: int = Query(24, ge=1, le=720, description="Hours of data to summarize"),
    name: str | None = Query(None, description="Filter by specific metric name"),
    _user: User = Depends(get_current_user),
) -> dict:
    """
    Get aggregated metrics summary.

    Returns count, average duration, and percentiles for each metric.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    with make_session() as session:
        query = session.query(
            MetricEvent.metric_type,
            MetricEvent.name,
            MetricEvent.status,
            func.count().label("count"),
            func.avg(MetricEvent.duration_ms).label("avg_duration"),
            func.min(MetricEvent.duration_ms).label("min_duration"),
            func.max(MetricEvent.duration_ms).label("max_duration"),
        ).filter(MetricEvent.timestamp >= since)

        if metric_type:
            query = query.filter(MetricEvent.metric_type == metric_type)
        if name:
            query = query.filter(MetricEvent.name == name)

        query = query.group_by(
            MetricEvent.metric_type, MetricEvent.name, MetricEvent.status
        )

        results = query.all()

        # Aggregate by name (combining statuses)
        by_name: dict[str, dict] = {}
        for row in results:
            key = f"{row.metric_type}:{row.name}"
            if key not in by_name:
                by_name[key] = {
                    "metric_type": row.metric_type,
                    "name": row.name,
                    "count": 0,
                    "success_count": 0,
                    "failure_count": 0,
                    "avg_duration_ms": None,
                    "min_duration_ms": None,
                    "max_duration_ms": None,
                    "durations": [],
                }
            entry = by_name[key]
            entry["count"] += row.count
            if row.status == "success":
                entry["success_count"] += row.count
            elif row.status == "failure":
                entry["failure_count"] += row.count

            if row.avg_duration:
                entry["durations"].append((row.count, row.avg_duration))
            if row.min_duration is not None:
                if entry["min_duration_ms"] is None or row.min_duration < entry["min_duration_ms"]:
                    entry["min_duration_ms"] = row.min_duration
            if row.max_duration is not None:
                if entry["max_duration_ms"] is None or row.max_duration > entry["max_duration_ms"]:
                    entry["max_duration_ms"] = row.max_duration

        # Calculate weighted average duration
        for entry in by_name.values():
            if entry["durations"]:
                total_weight = sum(count for count, _ in entry["durations"])
                if total_weight > 0:
                    entry["avg_duration_ms"] = sum(
                        count * dur for count, dur in entry["durations"]
                    ) / total_weight
            del entry["durations"]

        return {
            "period_hours": hours,
            "since": since.isoformat(),
            "metrics": list(by_name.values()),
        }


@router.get("/tasks")
def get_task_metrics(
    hours: int = Query(24, ge=1, le=720, description="Hours of data"),
    name: str | None = Query(None, description="Filter by task name"),
    limit: int = Query(100, ge=1, le=1000, description="Max events to return"),
    _user: User = Depends(get_current_user),
) -> dict:
    """
    Get Celery task metrics.

    Returns recent task executions with timing and status.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    with make_session() as session:
        query = (
            session.query(MetricEvent)
            .filter(MetricEvent.metric_type == "task")
            .filter(MetricEvent.timestamp >= since)
            .order_by(MetricEvent.timestamp.desc())
        )

        if name:
            query = query.filter(MetricEvent.name == name)

        events = query.limit(limit).all()

        return {
            "period_hours": hours,
            "count": len(events),
            "events": [
                {
                    "id": e.id,
                    "timestamp": e.timestamp.isoformat(),
                    "name": e.name,
                    "duration_ms": e.duration_ms,
                    "status": e.status,
                    "labels": e.labels,
                }
                for e in events
            ],
        }


@router.get("/mcp")
def get_mcp_metrics(
    hours: int = Query(24, ge=1, le=720, description="Hours of data"),
    name: str | None = Query(None, description="Filter by tool name"),
    limit: int = Query(100, ge=1, le=1000, description="Max events to return"),
    _user: User = Depends(get_current_user),
) -> dict:
    """
    Get MCP tool call metrics.

    Returns recent MCP tool invocations with timing and status.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    with make_session() as session:
        query = (
            session.query(MetricEvent)
            .filter(MetricEvent.metric_type == "mcp_call")
            .filter(MetricEvent.timestamp >= since)
            .order_by(MetricEvent.timestamp.desc())
        )

        if name:
            query = query.filter(MetricEvent.name == name)

        events = query.limit(limit).all()

        return {
            "period_hours": hours,
            "count": len(events),
            "events": [
                {
                    "id": e.id,
                    "timestamp": e.timestamp.isoformat(),
                    "name": e.name,
                    "duration_ms": e.duration_ms,
                    "status": e.status,
                    "labels": e.labels,
                }
                for e in events
            ],
        }


@router.get("/system")
def get_system_metrics(
    hours: int = Query(1, ge=1, le=168, description="Hours of data"),
    name: str | None = Query(None, description="Filter by metric name"),
    _user: User = Depends(get_current_user),
) -> dict:
    """
    Get system/process metrics (CPU, memory, disk).

    Returns latest values and recent history.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    with make_session() as session:
        query = (
            session.query(MetricEvent)
            .filter(MetricEvent.metric_type == "system")
            .filter(MetricEvent.timestamp >= since)
            .order_by(MetricEvent.timestamp.desc())
        )

        if name:
            query = query.filter(MetricEvent.name == name)

        events = query.limit(1000).all()

        # Get latest value for each metric
        latest: dict[str, float] = {}
        for e in events:
            if e.name not in latest and e.value is not None:
                latest[e.name] = e.value

        return {
            "period_hours": hours,
            "latest": latest,
            "history": [
                {
                    "timestamp": e.timestamp.isoformat(),
                    "name": e.name,
                    "value": e.value,
                }
                for e in events
            ],
        }


@router.get("/raw")
def get_raw_metrics(
    metric_type: str | None = Query(None, description="Filter by type"),
    name: str | None = Query(None, description="Filter by name"),
    hours: int = Query(1, ge=1, le=24, description="Hours of data"),
    limit: int = Query(100, ge=1, le=1000, description="Max events"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    _user: User = Depends(get_current_user),
) -> dict:
    """
    Get raw metric events with pagination.

    For detailed analysis and debugging.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    with make_session() as session:
        # Build base filters
        filters = [MetricEvent.timestamp >= since]
        if metric_type:
            filters.append(MetricEvent.metric_type == metric_type)
        if name:
            filters.append(MetricEvent.name == name)

        # Use window function to get total count in a single query
        # This avoids the N+1 problem of separate count() + offset/limit
        total_count = func.count().over().label("total_count")
        query = (
            session.query(MetricEvent, total_count)
            .filter(*filters)
            .order_by(MetricEvent.timestamp.desc())
            .offset(offset)
            .limit(limit)
        )

        results = query.all()
        total = results[0].total_count if results else 0
        events = [r[0] for r in results]

        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "events": [
                {
                    "id": e.id,
                    "timestamp": e.timestamp.isoformat(),
                    "metric_type": e.metric_type,
                    "name": e.name,
                    "duration_ms": e.duration_ms,
                    "status": e.status,
                    "labels": e.labels,
                    "value": e.value,
                }
                for e in events
            ],
        }
