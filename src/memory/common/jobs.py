"""
Job tracking utilities for async operations.

Provides functions to create, update, and query pending jobs.
Includes the @tracked_task decorator for automatic PendingJob lifecycle.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, TypeVar, overload

from sqlalchemy.exc import OperationalError

from celery.exceptions import TaskPredicate

from memory.common.celery_app import app as celery_app
from memory.common.content_processing import (
    get_celery_task_name,
    safe_task_execution,
)
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import PendingJob, JobStatus, JobType

logger = logging.getLogger(__name__)

_R = TypeVar("_R")


def create_job(
    session: DBSession,
    job_type: str | JobType,
    params: dict[str, Any] | None = None,
    external_id: str | None = None,
    user_id: int | None = None,
) -> PendingJob:
    """
    Create a new pending job record.

    Args:
        session: Database session
        job_type: Type of job (from JobType enum or string)
        params: Job parameters for debugging/retry
        external_id: Client-provided idempotency key
        user_id: User who initiated the job

    Returns:
        Created PendingJob instance (not yet committed)
    """
    if isinstance(job_type, JobType):
        job_type = job_type.value

    job = PendingJob(
        job_type=job_type,
        external_id=external_id,
        params=params or {},
        user_id=user_id,
        status=JobStatus.PENDING.value,
    )
    session.add(job)
    return job


def get_job(session: DBSession, job_id: int) -> PendingJob | None:
    """Get a job by ID."""
    return session.get(PendingJob, job_id)


def get_job_by_external_id(
    session: DBSession,
    external_id: str,
    job_type: str | None = None,
) -> PendingJob | None:
    """
    Get a job by external ID, returning the most recent if multiple exist.

    Args:
        session: Database session
        external_id: Client-provided idempotency key
        job_type: Optionally filter by job type

    Returns:
        Most recent PendingJob with the external_id, or None if not found
    """
    query = session.query(PendingJob).filter(PendingJob.external_id == external_id)
    if job_type:
        query = query.filter(PendingJob.job_type == job_type)
    return query.order_by(PendingJob.created_at.desc()).first()


def update_job_celery_task_id(
    session: DBSession,
    job: PendingJob,
    celery_task_id: str,
) -> None:
    """Update job with Celery task ID for correlation."""
    job.celery_task_id = celery_task_id
    job.updated_at = datetime.now(timezone.utc)


def start_job(session: DBSession, job_id: int) -> PendingJob | None:
    """
    Mark a job as processing and increment attempts.

    Args:
        session: Database session
        job_id: ID of the job to start

    Returns:
        Updated PendingJob, or None if not found
    """
    job = session.get(PendingJob, job_id)
    if not job:
        return None
    job.mark_processing()
    logger.info(f"Job {job_id} started (attempt {job.attempts})")
    return job


def complete_job(
    session: DBSession,
    job_id: int,
    result_id: int | None = None,
    result_type: str | None = None,
) -> PendingJob | None:
    """
    Mark a job as complete with optional result linking.

    Args:
        session: Database session
        job_id: ID of the job to complete
        result_id: ID of the created/modified item
        result_type: Model name of the result (e.g., "Meeting")

    Returns:
        Updated PendingJob, or None if not found
    """
    job = session.get(PendingJob, job_id)
    if not job:
        return None
    job.mark_complete(result_id=result_id, result_type=result_type)
    logger.info(
        f"Job {job_id} completed (result: {result_type} #{result_id})"
        if result_id
        else f"Job {job_id} completed"
    )
    return job


def fail_job(
    session: DBSession,
    job_id: int,
    error_message: str,
) -> PendingJob | None:
    """
    Mark a job as failed with error message.

    Args:
        session: Database session
        job_id: ID of the job to fail
        error_message: Error message describing the failure

    Returns:
        Updated PendingJob, or None if not found
    """
    job = session.get(PendingJob, job_id)
    if not job:
        return None
    job.mark_failed(error_message)
    logger.warning(f"Job {job_id} failed: {error_message}")
    return job


def list_jobs(
    session: DBSession,
    status: str | JobStatus | None = None,
    job_type: str | JobType | None = None,
    user_id: int | None = None,
    source: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[PendingJob]:
    """
    List jobs with optional filtering.

    Args:
        session: Database session
        status: Filter by status
        job_type: Filter by job type
        user_id: Filter by user
        source: Filter by origin — "manual" (user_id set) or "automatic" (user_id NULL)
        limit: Maximum results
        offset: Number of results to skip

    Returns:
        List of matching PendingJob instances
    """
    query = session.query(PendingJob)

    if status:
        status_val = status.value if isinstance(status, JobStatus) else status
        query = query.filter(PendingJob.status == status_val)

    if job_type:
        type_val = job_type.value if isinstance(job_type, JobType) else job_type
        query = query.filter(PendingJob.job_type == type_val)

    if user_id:
        query = query.filter(PendingJob.user_id == user_id)

    if source == "manual":
        query = query.filter(PendingJob.user_id.isnot(None))
    elif source == "automatic":
        query = query.filter(PendingJob.user_id.is_(None))

    if created_after:
        query = query.filter(PendingJob.created_at >= created_after)
    if created_before:
        query = query.filter(PendingJob.created_at <= created_before)

    return (
        query.order_by(PendingJob.created_at.desc()).limit(limit).offset(offset).all()
    )


@dataclass
class DispatchResult:
    """Result of dispatching a job."""

    job: PendingJob
    is_new: bool
    message: str


def dispatch_job(
    session: DBSession,
    job_type: str | JobType,
    task_name: str,
    task_kwargs: dict[str, Any],
    external_id: str | None = None,
    user_id: int | None = None,
    exclude_from_params: list[str] | None = None,
    allow_resubmit_failed: bool = True,
) -> DispatchResult:
    """
    Create a job record and dispatch a Celery task in one operation.

    This handles the common pattern of:
    1. Check if job with external_id already exists (for idempotency)
    2. Create job record (params derived from task_kwargs)
    3. Dispatch Celery task (with job_id injected)
    4. Link job to Celery task ID
    5. Commit the transaction

    Args:
        session: Database session
        job_type: Type of job (from JobType enum or string)
        task_name: Celery task name to dispatch
        task_kwargs: Keyword arguments for the Celery task (job_id auto-injected)
        external_id: Client-provided idempotency key
        user_id: User who initiated the job
        exclude_from_params: Fields to exclude from stored params (e.g., large data like "transcript")
        allow_resubmit_failed: If True, allow resubmission when previous job failed

    Returns:
        DispatchResult with job, whether it's new, and a message

    Example:
        result = dispatch_job(
            session=db,
            job_type=JobType.MEETING,
            task_name=PROCESS_MEETING,
            task_kwargs={
                "transcript": data.transcript,
                "title": data.title,
            },
            external_id="meeting-12345",
            user_id=user.id,
            exclude_from_params=["transcript"],  # Don't store full transcript in params
        )
        # No need to commit - dispatch_job handles it
    """
    # Check for existing job with same external_id
    if external_id:
        existing = get_job_by_external_id(
            session,
            external_id,
            job_type.value if isinstance(job_type, JobType) else job_type,
        )
        if existing:
            # Allow resubmission if previous job failed
            if not allow_resubmit_failed or existing.status != JobStatus.FAILED.value:
                return DispatchResult(
                    job=existing,
                    is_new=False,
                    message=f"Job already exists with status: {existing.status}",
                )

    # Derive params from task_kwargs, excluding large fields
    exclude_set = set(exclude_from_params or [])
    params = {k: v for k, v in task_kwargs.items() if k not in exclude_set}
    # Store task_name for retry capability
    params["_task_name"] = task_name

    # Create job record
    job = create_job(
        session,
        job_type=job_type,
        params=params,
        external_id=external_id,
        user_id=user_id,
    )
    session.flush()  # Get job.id

    # Always inject job_id into task kwargs
    final_kwargs = {**task_kwargs, "job_id": job.id}

    # Dispatch Celery task - if this fails, mark job as failed
    try:
        task = celery_app.send_task(task_name, kwargs=final_kwargs)
    except Exception as e:
        # Task dispatch failed - mark job as failed so it doesn't sit in PENDING forever
        job.mark_failed(f"Failed to dispatch Celery task: {e}")
        session.commit()
        logger.error(f"Failed to dispatch task {task_name} for job {job.id}: {e}")
        raise

    # Link job to Celery task and commit
    update_job_celery_task_id(session, job, task.id)
    session.commit()

    return DispatchResult(
        job=job,
        is_new=True,
        message=f"Job queued. Track status via GET /jobs/{job.id}",
    )


def retry_failed_job(
    session: DBSession,
    job: PendingJob,
) -> DispatchResult:
    """
    Retry a failed job by resetting its status and re-dispatching.

    This reuses the existing job record rather than creating a new one,
    preserving history and keeping the same job ID for tracking.

    Args:
        session: Database session
        job: The failed job to retry

    Returns:
        DispatchResult with the same job (is_new=False)

    Raises:
        ValueError: If job is not in failed status or missing required params
    """
    if job.status != JobStatus.FAILED.value:
        raise ValueError(f"Can only retry failed jobs, current status: {job.status}")

    task_name = job.params.get("_task_name")
    if not task_name:
        raise ValueError(
            "Job is missing _task_name in params. "
            "This job was created before retry support was added."
        )

    # Lock the job row to prevent concurrent retries (SELECT FOR UPDATE NOWAIT)
    # Using nowait=True to fail fast if another retry is in progress
    try:
        locked_job = (
            session.query(PendingJob)
            .filter(PendingJob.id == job.id)
            .with_for_update(nowait=True)
            .first()
        )
    except OperationalError:
        raise ValueError(
            f"Job {job.id} is currently being retried by another process. "
            "Please wait and try again."
        )
    if not locked_job:
        raise ValueError(f"Job {job.id} not found")

    # Re-check status after acquiring lock (another process may have retried)
    if locked_job.status != JobStatus.FAILED.value:
        raise ValueError(
            f"Job {job.id} is no longer in failed status (current: {locked_job.status}). "
            "It may have been retried by another process."
        )

    # Reset job status
    locked_job.status = JobStatus.PENDING.value
    locked_job.error_message = None
    locked_job.completed_at = None
    locked_job.updated_at = datetime.now(timezone.utc)
    session.flush()

    # Reconstruct task_kwargs from params (excluding internal fields)
    task_kwargs = {k: v for k, v in locked_job.params.items() if not k.startswith("_")}

    # Dispatch with same job_id - if this fails, revert status
    final_kwargs = {**task_kwargs, "job_id": locked_job.id}
    try:
        task = celery_app.send_task(task_name, kwargs=final_kwargs)
    except Exception as e:
        # Dispatch failed - revert to failed status
        locked_job.mark_failed(f"Failed to dispatch retry task: {e}")
        session.commit()
        logger.error(f"Failed to dispatch retry for job {locked_job.id}: {e}")
        raise

    # Update task ID and commit
    update_job_celery_task_id(session, locked_job, task.id)
    session.commit()

    logger.info(f"Retrying job {locked_job.id} (attempt {locked_job.attempts + 1})")

    return DispatchResult(
        job=locked_job,
        is_new=False,
        message=f"Job {locked_job.id} queued for retry. Track status via GET /jobs/{locked_job.id}",
    )


def reingest_job(
    session: DBSession,
    job: PendingJob,
) -> DispatchResult:
    """
    Reingest a completed job by resetting its status and re-dispatching.

    This allows re-running successful ingestion jobs to update content.

    Args:
        session: Database session
        job: The completed job to reingest

    Returns:
        DispatchResult with the same job (is_new=False)

    Raises:
        ValueError: If job is pending/processing or missing required params
    """
    if job.status in (JobStatus.PENDING.value, JobStatus.PROCESSING.value):
        raise ValueError(
            f"Cannot reingest a job that is still {job.status}. "
            "Wait for it to complete or fail first."
        )

    task_name = job.params.get("_task_name")
    if not task_name:
        raise ValueError(
            "Job is missing _task_name in params. "
            "This job was created before reingest support was added."
        )

    # Lock the job row to prevent concurrent reingests
    try:
        locked_job = (
            session.query(PendingJob)
            .filter(PendingJob.id == job.id)
            .with_for_update(nowait=True)
            .first()
        )
    except OperationalError:
        raise ValueError(
            f"Job {job.id} is currently being reingested by another process. "
            "Please wait and try again."
        )
    if not locked_job:
        raise ValueError(f"Job {job.id} not found")

    # Re-check status after acquiring lock
    if locked_job.status in (JobStatus.PENDING.value, JobStatus.PROCESSING.value):
        raise ValueError(
            f"Job {job.id} is now {locked_job.status}. "
            "It may have been reingested by another process."
        )

    # Reset job status
    locked_job.status = JobStatus.PENDING.value
    locked_job.error_message = None
    locked_job.completed_at = None
    locked_job.result_id = None
    locked_job.result_type = None
    locked_job.updated_at = datetime.now(timezone.utc)
    session.flush()

    # Reconstruct task_kwargs from params (excluding internal fields)
    task_kwargs = {k: v for k, v in locked_job.params.items() if not k.startswith("_")}

    # Dispatch with same job_id
    final_kwargs = {**task_kwargs, "job_id": locked_job.id}
    try:
        task = celery_app.send_task(task_name, kwargs=final_kwargs)
    except Exception as e:
        # Dispatch failed - mark as failed
        locked_job.mark_failed(f"Failed to dispatch reingest task: {e}")
        session.commit()
        logger.error(f"Failed to dispatch reingest for job {locked_job.id}: {e}")
        raise

    # Update task ID and commit
    update_job_celery_task_id(session, locked_job, task.id)
    session.commit()

    logger.info(f"Reingesting job {locked_job.id}")

    return DispatchResult(
        job=locked_job,
        is_new=False,
        message=f"Job {locked_job.id} queued for reingest. Track status via GET /jobs/{locked_job.id}",
    )


# ---------------------------------------------------------------------------
# @tracked_task decorator
# ---------------------------------------------------------------------------


@overload
def tracked_task(func: Callable[..., _R]) -> Callable[..., _R]: ...
@overload
def tracked_task(*, job_type: str | None = None) -> Callable[[Callable[..., _R]], Callable[..., _R]]: ...


def tracked_task(
    func: Callable[..., _R] | None = None,
    *,
    job_type: str | None = None,
) -> Callable[..., _R] | Callable[[Callable[..., _R]], Callable[..., _R]]:
    """Decorator that wraps a Celery task with PendingJob lifecycle tracking.

    Combines @safe_task_execution (metrics, error handling, notifications)
    with automatic PendingJob creation and status management.

    Two modes:
    - API-initiated: if ``job_id`` is in kwargs, uses the existing PendingJob
    - Automatic/beat: creates a new PendingJob with user_id=NULL

    The ``job_id`` kwarg is consumed by the decorator and NOT passed to the
    wrapped function.

    If the task returns a dict with ``result_id`` and/or ``result_type``,
    those are forwarded to complete_job for result linking.

    Usage::

        @app.task(name=SYNC_WEBPAGE)
        @tracked_task
        def sync_webpage(url: str) -> dict:
            ...

        @app.task(name=SYNC_COMIC)
        @tracked_task(job_type="comic_sync")
        def sync_comic(comic_id: int) -> dict:
            ...
    """
    def decorator(fn: Callable[..., _R]) -> Callable[..., _R]:
        resolved_job_type = job_type or fn.__name__
        safe_fn = safe_task_execution(fn)

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> _R:
            # Consume job_id — don't pass it to the task function
            incoming_job_id: int | None = kwargs.pop("job_id", None)

            # Resolve Celery task ID for correlation
            task_self = args[0] if args and hasattr(args[0], "request") else None
            celery_task_id = _get_celery_task_id(task_self)

            # Build params snapshot for debugging/retry
            task_name = get_celery_task_name(fn, args)
            params = _build_job_params(task_name, fn, args, kwargs)

            # Start or create the PendingJob
            actual_job_id = _start_or_create_job(
                incoming_job_id=incoming_job_id,
                job_type=resolved_job_type,
                celery_task_id=celery_task_id,
                params=params,
            )

            # Run the task (safe_task_execution handles metrics + notifications)
            try:
                result = safe_fn(*args, **kwargs)
            except TaskPredicate:
                # Celery flow control — Retry/Reject/Ignore are not failures.
                # The PendingJob row is left in its in-progress state so the
                # retried run can pick it up via incoming_job_id.
                raise
            except Exception as exc:
                _mark_job_failed(actual_job_id, str(exc)[:500])
                raise

            _mark_job_complete(actual_job_id, result)
            return result

        return wrapper  # type: ignore[return-value]

    if func is not None:
        return decorator(func)
    return decorator


def _get_celery_task_id(task_self: Any) -> str | None:
    """Extract the Celery task ID from either a bound task or current_task."""
    if task_self and hasattr(task_self, "request"):
        tid = getattr(task_self.request, "id", None)
        if tid:
            return tid
    try:
        from celery import current_task
        if current_task and current_task.request:
            return current_task.request.id
    except Exception:
        pass
    return None


def _build_job_params(
    task_name: str, fn: Callable, args: tuple, kwargs: dict
) -> dict[str, Any]:
    """Build a params dict for the PendingJob record."""
    # Store task name for retry support, plus a summary of kwargs
    params: dict[str, Any] = {"_task_name": task_name}
    # Include non-large kwargs for debugging (truncate large values)
    for k, v in kwargs.items():
        if isinstance(v, str) and len(v) > 200:
            params[k] = v[:200] + "..."
        elif isinstance(v, (str, int, float, bool, type(None))):
            params[k] = v
        else:
            params[k] = repr(v)[:200]
    return params


def _start_or_create_job(
    *,
    incoming_job_id: int | None,
    job_type: str,
    celery_task_id: str | None,
    params: dict[str, Any],
) -> int | None:
    """Start an existing job or create a new one. Returns the job ID or None."""
    try:
        with make_session() as session:
            if incoming_job_id:
                job = start_job(session, incoming_job_id)
                if not job:
                    logger.warning("Job %s not found, proceeding without tracking", incoming_job_id)
                    return None
            else:
                job = create_job(session, job_type=job_type, params=params)
                session.flush()
                job.mark_processing()

            if celery_task_id:
                update_job_celery_task_id(session, job, celery_task_id)

            return job.id
    except Exception:
        logger.warning("Failed to start/create job tracking, proceeding without", exc_info=True)
        return None


def _mark_job_failed(job_id: int | None, error: str) -> None:
    """Mark a job as failed, swallowing DB errors."""
    if not job_id:
        return
    try:
        with make_session() as session:
            fail_job(session, job_id, error)
    except Exception:
        logger.warning("Failed to mark job %s as failed", job_id, exc_info=True)


def _mark_job_complete(job_id: int | None, result: Any) -> None:
    """Mark a job as complete, extracting result linking from the return value."""
    if not job_id:
        return
    result_id = None
    result_type = None
    if isinstance(result, dict):
        result_id = result.get("result_id")
        result_type = result.get("result_type")
    try:
        with make_session() as session:
            complete_job(session, job_id, result_id=result_id, result_type=result_type)
    except Exception:
        logger.warning("Failed to mark job %s as complete", job_id, exc_info=True)
