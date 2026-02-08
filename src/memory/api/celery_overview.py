"""API endpoints for Celery task overview - beat schedule and ingestion summary."""

from datetime import datetime, timedelta, timezone

from celery.schedules import crontab
from fastapi import APIRouter
from sqlalchemy import func, case

from memory.api.auth import require_scope
from memory.common.scopes import SCOPE_ADMIN
from memory.common.celery_app import app as celery_app, build_beat_schedule
from memory.common.db.connection import make_session
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
    """Get the most recent MetricEvent for each task name in a single query."""
    if not task_names:
        return {}

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
    results.sort(key=lambda r: (r["last_status"] != "failure", r["name"].lower()))
    return results


@router.get("/task-activity")
def get_task_activity(
    _user: User = require_scope(SCOPE_ADMIN),
    hours: int = 24,
) -> dict:
    """Return task execution activity from MetricEvent in the last N hours."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    with make_session() as db:
        # Per-task breakdown
        rows = (
            db.query(
                MetricEvent.name,
                func.count(MetricEvent.id).label("total"),
                func.sum(
                    case((MetricEvent.status == "success", 1), else_=0)
                ).label("success"),
                func.sum(
                    case((MetricEvent.status == "failure", 1), else_=0)
                ).label("failure"),
                func.avg(MetricEvent.duration_ms).label("avg_duration"),
            )
            .filter(
                MetricEvent.metric_type == "task",
                MetricEvent.timestamp >= cutoff,
            )
            .group_by(MetricEvent.name)
            .all()
        )

        by_task = [
            {
                "task": row.name,
                "total": row.total,
                "success": int(row.success or 0),
                "failure": int(row.failure or 0),
                "avg_duration_ms": round(row.avg_duration, 1) if row.avg_duration else None,
            }
            for row in rows
        ]
        # Sort: failures first, then by total descending
        by_task.sort(key=lambda r: (-r["failure"], -r["total"]))

        # Totals
        tasks_with_duration = [r for r in by_task if r["avg_duration_ms"] is not None]
        weighted_total = sum(r["total"] for r in tasks_with_duration)
        totals = {
            "total": sum(r["total"] for r in by_task),
            "success": sum(r["success"] for r in by_task),
            "failure": sum(r["failure"] for r in by_task),
            "avg_duration_ms": (
                round(
                    sum(r["avg_duration_ms"] * r["total"] for r in tasks_with_duration)
                    / weighted_total,
                    1,
                )
                if tasks_with_duration and weighted_total > 0
                else None
            ),
        }

        # Recent failures from MetricEvent (last 10 task failures within the time window)
        recent_failures = (
            db.query(MetricEvent)
            .filter(
                MetricEvent.metric_type == "task",
                MetricEvent.status == "failure",
                MetricEvent.timestamp >= cutoff,
            )
            .order_by(MetricEvent.timestamp.desc())
            .limit(10)
            .all()
        )
        failures_list = [
            {
                "task": e.name,
                "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                "duration_ms": e.duration_ms,
                "labels": e.labels,
                "error": (e.labels or {}).get("error"),
            }
            for e in recent_failures
        ]

    return {
        "hours": hours,
        "by_task": by_task,
        "totals": totals,
        "recent_failures": failures_list,
    }
