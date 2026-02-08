"""API endpoints for Celery task overview - beat schedule and ingestion summary."""

from datetime import datetime, timedelta, timezone

from celery.schedules import crontab
from fastapi import APIRouter
from sqlalchemy import func, case

from memory.api.auth import require_scope
from memory.common.scopes import SCOPE_ADMIN
from memory.common.celery_app import app as celery_app, build_beat_schedule
from memory.common.db.connection import make_session
from memory.common.db.models.jobs import JobStatus, PendingJob
from memory.common.db.models.metrics import MetricEvent
from memory.common.db.models.users import User

router = APIRouter(prefix="/api/celery", tags=["celery"])


def format_schedule(schedule) -> str:
    """Convert a Celery schedule object to a human-readable string."""
    if isinstance(schedule, crontab):
        # These are internal celery attrs; no public API exists for this.
        # Fall back to str(schedule) if celery changes internals.
        try:
            minute = str(schedule._orig_minute)
            hour = str(schedule._orig_hour)
            dow = str(schedule._orig_day_of_week)
        except AttributeError:
            return str(schedule)

        if hour == "*" and minute == "*":
            return "Every minute"
        if hour == "*":
            return f"Every hour at :{minute.zfill(2)}"
        if dow != "*":
            return f"{dow} at {hour}:{minute.zfill(2)}"
        if minute == "0":
            return f"Daily at {hour}:00"
        return f"Daily at {hour}:{minute.zfill(2)}"

    if isinstance(schedule, (int, float)):
        seconds = int(schedule)
        if seconds < 60:
            return f"Every {seconds}s"
        if seconds < 3600:
            mins = seconds // 60
            return f"Every {mins}m"
        hours = seconds / 3600
        if hours == int(hours):
            return f"Every {int(hours)}h"
        return f"Every {hours:.1f}h"

    # timedelta or schedule with .total_seconds()
    if hasattr(schedule, "total_seconds"):
        return format_schedule(int(schedule.total_seconds()))

    return str(schedule)


def batch_last_run_info(
    task_names: list[str],
    db,
) -> dict[str, tuple[datetime | None, str | None, float | None]]:
    """Get the most recent MetricEvent for each task name in a single query.

    Uses DISTINCT ON to pick the latest event per task name, avoiding N+1 queries.
    """
    if not task_names:
        return {}

    # Use a subquery to get the max timestamp per task name, then join back
    # to get the full row. This is portable across PostgreSQL and SQLite.
    subq = (
        db.query(
            MetricEvent.name,
            func.max(MetricEvent.timestamp).label("max_ts"),
        )
        .filter(
            MetricEvent.metric_type == "task",
            MetricEvent.name.in_(task_names),
        )
        .group_by(MetricEvent.name)
        .subquery()
    )

    rows = (
        db.query(MetricEvent)
        .join(
            subq,
            (MetricEvent.name == subq.c.name)
            & (MetricEvent.timestamp == subq.c.max_ts),
        )
        .filter(MetricEvent.metric_type == "task")
        .all()
    )

    return {
        event.name: (event.timestamp, event.status, event.duration_ms)
        for event in rows
    }


@router.get("/beat-schedule")
def get_beat_schedule(
    _user: User = require_scope(SCOPE_ADMIN),
) -> list[dict]:
    """Return the Celery beat schedule with last run info for each task.

    Uses build_beat_schedule() to get the canonical schedule definition
    rather than app.conf.beat_schedule, which is only populated in the
    worker/beat process (not the API server).
    Custom tasks registered via register_custom_beat are also included
    from app.conf.beat_schedule if present.
    """
    schedule = build_beat_schedule()
    # Merge in any custom tasks that were registered dynamically
    for key, entry in (celery_app.conf.beat_schedule or {}).items():
        if key not in schedule:
            schedule[key] = entry
    results = []

    with make_session() as db:
        task_names = [entry.get("task", "") for entry in schedule.values()]
        last_runs = batch_last_run_info(task_names, db)

        for key, entry in schedule.items():
            task_name = entry.get("task", "")
            sched = entry.get("schedule")

            last_run, last_status, last_duration = last_runs.get(
                task_name, (None, None, None)
            )

            results.append(
                {
                    "key": key,
                    "name": key.replace("-", " ").title(),
                    "task": task_name,
                    "schedule_display": format_schedule(sched) if sched else "Unknown",
                    "last_run": last_run.isoformat() if last_run else None,
                    "last_status": last_status,
                    "last_duration_ms": last_duration,
                }
            )

    # Sort: failed first, then by name
    results.sort(key=lambda r: (r["last_status"] != "error", r["name"].lower()))
    return results


@router.get("/ingestion-summary")
def get_ingestion_summary(
    _user: User = require_scope(SCOPE_ADMIN),
) -> dict:
    """Return ingestion job summary grouped by type and recent failures."""
    with make_session() as db:
        # Group by (job_type, status) with counts
        rows = (
            db.query(
                PendingJob.job_type,
                PendingJob.status,
                func.count(PendingJob.id),
            )
            .group_by(PendingJob.job_type, PendingJob.status)
            .all()
        )

        by_type: dict[str, dict[str, int]] = {}
        for job_type, status, count in rows:
            if job_type not in by_type:
                by_type[job_type] = {
                    "pending": 0,
                    "processing": 0,
                    "complete": 0,
                    "failed": 0,
                }
            by_type[job_type][status] = count

        type_list = [
            {
                "job_type": jt,
                "pending": counts["pending"],
                "processing": counts["processing"],
                "complete": counts["complete"],
                "failed": counts["failed"],
                "total": sum(counts.values()),
            }
            for jt, counts in sorted(by_type.items())
        ]

        # Recent failures (last 10)
        recent_failures = (
            db.query(PendingJob)
            .filter(PendingJob.status == JobStatus.FAILED.value)
            .order_by(PendingJob.updated_at.desc())
            .limit(10)
            .all()
        )
        failures_list = [
            {
                "id": j.id,
                "job_type": j.job_type,
                "error_message": j.error_message,
                "updated_at": j.updated_at.isoformat() if j.updated_at else None,
            }
            for j in recent_failures
        ]

        # Task metrics from MetricEvent in last 24h
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        metric_rows = (
            db.query(
                func.count(MetricEvent.id).label("total"),
                func.sum(
                    case((MetricEvent.status == "success", 1), else_=0)
                ).label("success"),
                func.sum(
                    case((MetricEvent.status == "error", 1), else_=0)
                ).label("failure"),
                func.avg(MetricEvent.duration_ms).label("avg_duration"),
            )
            .filter(
                MetricEvent.metric_type == "task",
                MetricEvent.timestamp >= cutoff,
            )
            .first()
        )

        task_metrics = {
            "total": metric_rows.total if metric_rows else 0,
            "success": int(metric_rows.success or 0) if metric_rows else 0,
            "failure": int(metric_rows.failure or 0) if metric_rows else 0,
            "avg_duration_ms": (
                round(metric_rows.avg_duration, 1)
                if metric_rows and metric_rows.avg_duration
                else None
            ),
        }

    return {
        "by_type": type_list,
        "recent_failures": failures_list,
        "task_metrics": task_metrics,
    }
