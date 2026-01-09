"""
Metrics collection and maintenance tasks.

Provides:
- System metrics collection (CPU, memory, disk)
- Old metrics cleanup (30-day retention)
- Materialized view refresh for aggregations
"""

import logging
from datetime import datetime, timedelta, timezone

import psutil
from sqlalchemy import delete, select, text

from memory.common.celery_app import (
    app,
    COLLECT_SYSTEM_METRICS,
    CLEANUP_OLD_METRICS,
    REFRESH_METRIC_SUMMARIES,
)
from memory.common.db.connection import make_session
from memory.common.db.models import MetricEvent
from memory.common.metrics import record_gauge

logger = logging.getLogger(__name__)


def collect_open_files(process) -> int | None:
    """Attempt to get open file count, returning None if access denied."""
    try:
        return len(process.open_files())
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        return None


@app.task(name=COLLECT_SYSTEM_METRICS)
def collect_system_metrics() -> dict:
    """
    Collect system and process metrics.

    Collects:
    - Process-level: CPU %, memory (RSS/VMS), open files, threads
    - System-wide: CPU %, memory %, disk usage

    Should be scheduled to run every 60 seconds.
    """
    metrics_collected = 0

    try:
        # Process-level metrics
        process = psutil.Process()

        # CPU percent (requires interval for accurate reading)
        cpu_percent = process.cpu_percent(interval=0.1)
        record_gauge("process.cpu_percent", cpu_percent)
        metrics_collected += 1

        # Memory
        mem_info = process.memory_info()
        record_gauge("process.memory_rss_mb", mem_info.rss / 1024 / 1024)
        record_gauge("process.memory_vms_mb", mem_info.vms / 1024 / 1024)
        metrics_collected += 2

        # File descriptors / handles (may fail due to permissions)
        open_files = collect_open_files(process)
        if open_files is not None:
            record_gauge("process.open_files", open_files)
            metrics_collected += 1

        # Threads
        record_gauge("process.num_threads", process.num_threads())
        metrics_collected += 1

    except Exception as e:
        logger.error(f"Error collecting process metrics: {e}")

    try:
        # System-wide metrics
        cpu_percent = psutil.cpu_percent(interval=0.1)
        record_gauge("system.cpu_percent", cpu_percent)
        metrics_collected += 1

        mem = psutil.virtual_memory()
        record_gauge("system.memory_percent", mem.percent)
        record_gauge("system.memory_available_mb", mem.available / 1024 / 1024)
        metrics_collected += 2

        # Disk usage for root partition
        disk = psutil.disk_usage("/")
        record_gauge("system.disk_usage_percent", disk.percent)
        record_gauge("system.disk_free_gb", disk.free / 1024 / 1024 / 1024)
        metrics_collected += 2

    except Exception as e:
        logger.error(f"Error collecting system metrics: {e}")

    return {"status": "success", "metrics_collected": metrics_collected}


@app.task(name=CLEANUP_OLD_METRICS)
def cleanup_old_metrics(retention_days: int = 30) -> dict:
    """
    Delete metric events older than retention_days.

    Args:
        retention_days: Number of days to retain metrics (default: 30)

    Returns:
        Dict with count of deleted records
    """
    logger.info(f"Cleaning up metrics older than {retention_days} days")

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    deleted = 0

    with make_session() as session:
        # Delete in batches using subquery to avoid long-running transactions
        # SQLAlchemy's .limit().delete() doesn't work correctly on all backends
        batch_size = 10000
        while True:
            # Get IDs to delete in this batch
            subquery = (
                select(MetricEvent.id)
                .where(MetricEvent.timestamp < cutoff)
                .limit(batch_size)
            )
            # Delete those specific IDs
            stmt = delete(MetricEvent).where(MetricEvent.id.in_(subquery))
            result = session.execute(stmt)
            session.commit()

            batch_deleted = result.rowcount
            if batch_deleted == 0:
                break

            deleted += batch_deleted
            logger.info(f"Deleted {deleted} old metric events so far...")

    logger.info(f"Deleted {deleted} metric events older than {retention_days} days")
    return {"deleted": deleted, "retention_days": retention_days}


@app.task(name=REFRESH_METRIC_SUMMARIES)
def refresh_metric_summaries() -> dict:
    """
    Refresh the metric_summaries materialized view.

    This should be run periodically (e.g., hourly) to update aggregations.
    The CONCURRENTLY option allows queries to continue during refresh.
    """
    logger.info("Refreshing metric_summaries materialized view")

    with make_session() as session:
        try:
            # Use CONCURRENTLY to allow reads during refresh
            # Requires unique index: idx_metric_summaries_unique
            session.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY metric_summaries"))
            session.commit()
            status = "success"
        except Exception as e:
            logger.error(f"Error refreshing metric_summaries concurrently: {e}")
            # Fall back to blocking refresh if concurrent fails
            # (e.g., if unique index missing or first-time population)
            try:
                session.rollback()
                session.execute(text("REFRESH MATERIALIZED VIEW metric_summaries"))
                session.commit()
                status = "success_non_concurrent"
            except Exception as e2:
                logger.error(f"Non-concurrent refresh also failed: {e2}")
                status = "error"

    logger.info(f"Materialized view refresh: {status}")
    return {"status": status}
