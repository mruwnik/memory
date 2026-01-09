"""
Metrics collection and profiling for the memory system.

Provides a universal @profile decorator that can be applied to any function
to record execution timing, status, and optionally function parameters.

Usage:
    @profile("task", log_params=["account_id"])
    def sync_account(account_id: int, since: str):
        ...

    @profile("mcp_call")
    async def search_knowledge_base(query: str):
        ...

    @profile("search", log_params=True)
    def execute_search(query: str, filters: dict):
        ...
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import queue
import threading
import time
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Sequence, TypeVar

logger = logging.getLogger(__name__)

# Type variable for preserving function signatures
F = TypeVar("F", bound=Callable[..., Any])

# Maximum size for logged parameter values (to avoid huge JSONB entries)
MAX_PARAM_VALUE_LENGTH = 500

# Background writer for non-blocking metric recording
# Max 10000 metrics in queue; beyond that, oldest are dropped
_metric_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=10000)
_writer_thread: threading.Thread | None = None
_writer_started = False  # Fast flag to avoid is_alive() check on every metric
_writer_lock = threading.Lock()  # Protects _writer_started and _writer_thread
_shutdown_event = threading.Event()


def truncate_value(value: Any, max_length: int = MAX_PARAM_VALUE_LENGTH) -> Any:
    """Truncate large values for storage in labels."""
    if value is None:
        return None

    try:
        str_value = str(value)
        if len(str_value) > max_length:
            return str_value[:max_length] + "..."
        return value
    except Exception:
        return "<unserializable>"


def extract_params(
    func: Callable,
    args: tuple,
    kwargs: dict,
    log_params: Sequence[str] | bool,
) -> dict[str, Any]:
    """Extract function parameters based on log_params specification."""
    if log_params is False:
        return {}

    try:
        sig = inspect.signature(func)
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        all_params = dict(bound.arguments)
    except Exception:
        # If we can't bind the signature, return empty
        return {}

    if log_params is True:
        # Log all params
        return {k: truncate_value(v) for k, v in all_params.items()}
    else:
        # Log only specified params
        return {
            k: truncate_value(v) for k, v in all_params.items() if k in log_params
        }


def serialize_labels(labels: dict[str, Any]) -> dict[str, Any]:
    """Ensure labels are JSON-serializable."""
    result = {}
    for k, v in labels.items():
        try:
            # Test if it's JSON serializable
            json.dumps(v)
            result[k] = v
        except (TypeError, ValueError):
            result[k] = str(v)
    return result


def write_metric(
    metric_type: str,
    name: str,
    duration_ms: float | None = None,
    status: str | None = None,
    labels: dict[str, Any] | None = None,
    value: float | None = None,
) -> None:
    """Queue a metric for background writing."""
    metric = {
        "metric_type": metric_type,
        "name": name,
        "duration_ms": duration_ms,
        "status": status,
        "labels": serialize_labels(labels or {}),
        "value": value,
        "timestamp": datetime.now(timezone.utc),
    }
    try:
        _metric_queue.put_nowait(metric)
    except queue.Full:
        logger.warning("Metric queue full, dropping metric: %s", name)


def _flush_metrics_batch(
    metrics: list[dict[str, Any]],
    session_factory,
    MetricEvent,
) -> bool:
    """Flush a batch of metrics to the database. Returns True on success."""
    session = session_factory()
    try:
        for m in metrics:
            event = MetricEvent(
                timestamp=m["timestamp"],
                metric_type=m["metric_type"],
                name=m["name"],
                duration_ms=m["duration_ms"],
                status=m["status"],
                labels=m["labels"],
                value=m["value"],
            )
            session.add(event)
        session.commit()
        return True
    except Exception as e:
        logger.error("Failed to write metrics batch: %s", e)
        session.rollback()
        return False
    finally:
        session.close()


def _background_writer() -> None:
    """Background thread that writes metrics to the database."""
    from memory.common.db.connection import get_session_factory
    from memory.common.db.models import MetricEvent

    batch: list[dict[str, Any]] = []
    batch_size = 50
    flush_interval = 5.0  # seconds
    last_flush = time.time()

    # Get session factory once, reuse for all flushes
    session_factory = get_session_factory()

    while not _shutdown_event.is_set():
        try:
            # Try to get a metric with timeout
            try:
                metric = _metric_queue.get(timeout=1.0)
                batch.append(metric)
            except queue.Empty:
                pass

            # Check flush conditions
            now = time.time()
            should_flush = (
                len(batch) >= batch_size or (batch and now - last_flush >= flush_interval)
            )
            if not should_flush or not batch:
                continue

            # Flush the batch
            if _flush_metrics_batch(batch, session_factory, MetricEvent):
                batch = []
                last_flush = now
                continue

            # Flush failed - keep batch for retry, but limit size to avoid memory growth
            if len(batch) > batch_size * 3:
                batch = batch[-batch_size:]

        except Exception as e:
            logger.error("Error in metric writer thread: %s", e)

    # Final flush on shutdown
    if batch:
        _flush_metrics_batch(batch, session_factory, MetricEvent)


def _start_metrics_writer_locked() -> None:
    """Start the background metrics writer thread. Caller must hold _writer_lock."""
    global _writer_thread, _writer_started
    if _writer_thread is None or not _writer_thread.is_alive():
        _shutdown_event.clear()
        _writer_thread = threading.Thread(target=_background_writer, daemon=True)
        _writer_thread.start()
        _writer_started = True
        logger.info("Started metrics background writer thread")


def start_metrics_writer() -> None:
    """Start the background metrics writer thread."""
    with _writer_lock:
        _start_metrics_writer_locked()


def stop_metrics_writer(timeout: float = 5.0) -> None:
    """Stop the background metrics writer thread."""
    global _writer_thread, _writer_started
    with _writer_lock:
        if _writer_thread and _writer_thread.is_alive():
            _shutdown_event.set()
            _writer_thread.join(timeout=timeout)
            _writer_thread = None
            _writer_started = False
            logger.info("Stopped metrics background writer thread")


def record_metric(
    metric_type: str,
    name: str,
    duration_ms: float | None = None,
    status: str | None = None,
    labels: dict[str, Any] | None = None,
    value: float | None = None,
) -> None:
    """
    Record a metric event.

    This is the low-level function for recording metrics. For most use cases,
    prefer the @profile decorator.

    Args:
        metric_type: Category (task, mcp_call, system, function, etc.)
        name: Name of the metric (function name, task name, etc.)
        duration_ms: Execution duration in milliseconds
        status: Status string (success, failure, error, etc.)
        labels: Additional context as key-value pairs
        value: Numeric value for gauge metrics (CPU %, memory, etc.)
    """
    # Ensure writer is running. Use double-checked locking to prevent race where
    # multiple threads see _writer_started=False and all try to start the writer.
    if not _writer_started:
        with _writer_lock:
            if not _writer_started:  # Double-check under lock
                _start_metrics_writer_locked()
    write_metric(metric_type, name, duration_ms, status, labels, value)


def record_gauge(
    name: str,
    value: float,
    labels: dict[str, Any] | None = None,
) -> None:
    """
    Record a gauge metric (point-in-time value).

    Convenience function for system metrics like CPU %, memory usage, etc.

    Args:
        name: Metric name (e.g., "cpu_percent", "memory_rss_mb")
        value: The gauge value
        labels: Additional context
    """
    record_metric(
        metric_type="system",
        name=name,
        value=value,
        labels=labels,
    )


def profile(
    metric_type: str = "function",
    name: str | None = None,
    log_params: Sequence[str] | bool = False,
    extra_labels: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    """
    Universal profiling decorator for timing any function.

    Records execution time, status (success/failure), and optionally
    function parameters to the metrics database.

    Args:
        metric_type: Category label (task, mcp_call, search, etc.)
        name: Override the function name in metrics (default: function.__name__)
        log_params:
            - False: don't log params (default)
            - True: log all params
            - ["param1", "param2"]: log only specified params
        extra_labels: Static labels to include with every metric

    Returns:
        Decorated function that records metrics on each call.

    Examples:
        @profile("task", log_params=["account_id"])
        def sync_account(account_id: int, since: str):
            ...

        @profile("mcp_call")
        async def search_knowledge_base(query: str):
            ...

        @profile("search", log_params=True)
        def execute_search(query: str, filters: dict):
            ...
    """

    def decorator(func: F) -> F:
        metric_name = name or func.__name__
        is_async = asyncio.iscoroutinefunction(func)

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            status = "success"
            try:
                return func(*args, **kwargs)
            except Exception:
                status = "failure"
                raise
            finally:
                duration_ms = (time.perf_counter() - start) * 1000
                labels = dict(extra_labels) if extra_labels else {}
                labels.update(extract_params(func, args, kwargs, log_params))
                record_metric(
                    metric_type=metric_type,
                    name=metric_name,
                    duration_ms=duration_ms,
                    status=status,
                    labels=labels,
                )

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            status = "success"
            try:
                return await func(*args, **kwargs)
            except Exception:
                status = "failure"
                raise
            finally:
                duration_ms = (time.perf_counter() - start) * 1000
                labels = dict(extra_labels) if extra_labels else {}
                labels.update(extract_params(func, args, kwargs, log_params))
                record_metric(
                    metric_type=metric_type,
                    name=metric_name,
                    duration_ms=duration_ms,
                    status=status,
                    labels=labels,
                )

        return async_wrapper if is_async else sync_wrapper  # type: ignore

    return decorator
