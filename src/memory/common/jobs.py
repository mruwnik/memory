"""
Job tracking utilities for async operations.

Provides functions to create, update, and query pending jobs.
Includes the @tracked_task decorator for automatic PendingJob lifecycle.
"""

import inspect
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from uuid import uuid4
from typing import Any, Callable, TypeVar, overload

from sqlalchemy import func
from sqlalchemy.exc import OperationalError

from celery import current_task
from celery.exceptions import Reject, Retry, TaskPredicate

from memory.common.celery_app import app as celery_app
from memory.common.content_processing import (
    get_celery_task_name,
    safe_task_execution,
)
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import PendingJob, JobStatus, JobType
from memory.common.discord import notify_task_failure
from memory.common.metrics import record_metric

logger = logging.getLogger(__name__)


# Kwargs that the dispatcher injects after the params snapshot is taken.
# They are required by the task signature but are NOT user-supplied data, so
# excluding them from the persisted params is fine — they're reconstructed
# from the row itself at retry time.
_DISPATCHER_INJECTED_KWARGS = frozenset({"job_id"})


def get_required_task_kwargs(task_name: str) -> set[str]:
    """Return the set of required kwarg names for a registered Celery task.

    A kwarg is "required" if its parameter has no default and is not the
    bound-task ``self``. Returns an empty set if the task isn't registered
    yet (e.g. validating during a unit test that doesn't import workers) so
    we degrade to "trust the caller" rather than blowing up on validation.

    Used by ``dispatch_job`` to fail-fast when ``exclude_from_params`` would
    strip a parameter the worker actually needs at retry time.
    """
    task = celery_app.tasks.get(task_name)
    if task is None:
        return set()

    # Celery wraps the user function as ``task.run``; inspect that.
    fn = getattr(task, "run", task)
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return set()

    required: set[str] = set()
    for param in sig.parameters.values():
        if param.name == "self":
            continue
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if param.default is inspect.Parameter.empty:
            required.add(param.name)
    return required

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
    *,
    user_id: int | None,
) -> PendingJob | None:
    """
    Get a user's job by external ID, returning the most recent if multiple exist.

    Idempotency keys belong to whoever submitted them, so one user's key
    never matches (or reveals) another user's job.

    Args:
        session: Database session
        external_id: Client-provided idempotency key
        job_type: Optionally filter by job type
        user_id: The submitting user; None matches jobs without a user

    Returns:
        Most recent PendingJob with the external_id, or None if not found
    """
    query = session.query(PendingJob).filter(
        PendingJob.external_id == external_id, PendingJob.user_id == user_id
    )
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
    logger.debug(
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


def filter_jobs(
    query,
    status: str | JobStatus | None = None,
    job_type: str | JobType | None = None,
    user_id: int | None = None,
    source: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
):
    """Apply the job list filters to a PendingJob-based query."""
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

    return query


def list_jobs(
    session: DBSession,
    limit: int = 50,
    offset: int = 0,
    **filters,
) -> list[PendingJob]:
    """List jobs matching ``filter_jobs`` filters, newest first."""
    query = filter_jobs(session.query(PendingJob), **filters)
    return query.order_by(PendingJob.created_at.desc()).limit(limit).offset(offset).all()


def count_jobs_by_status(session: DBSession, **filters) -> dict[str, int]:
    """Count jobs per status in one query; every status is present, zero if unused."""
    query = filter_jobs(session.query(PendingJob.status, func.count()), **filters)
    counted = dict(query.group_by(PendingJob.status).all())
    return {s.value: counted.get(s.value, 0) for s in JobStatus}


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
    3. Assign a Celery task ID and commit the job
    4. Dispatch Celery task (with job_id injected)

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
            user_id=user_id,
        )
        if existing:
            # Allow resubmission if previous job failed
            if not allow_resubmit_failed or existing.status != JobStatus.FAILED.value:
                return DispatchResult(
                    job=existing,
                    is_new=False,
                    message=f"Job already exists with status: {existing.status}",
                )

    # Derive params from task_kwargs, excluding large fields.
    exclude_set = set(exclude_from_params or [])

    # Fail-fast: if a caller tries to exclude a *required* kwarg from the
    # persisted params, retry would later crash with a TypeError because
    # ``retry_failed_job`` reconstructs ``task_kwargs`` from ``params``
    # alone. Catch that at dispatch time so the bug surfaces with the
    # offending caller, not with a stranger hitting the retry button. The
    # dispatcher also injects ``job_id`` itself, so an exclusion of that
    # kwarg is harmless and explicitly allowed.
    required = get_required_task_kwargs(task_name)
    bad_excludes = (exclude_set & required) - _DISPATCHER_INJECTED_KWARGS
    if bad_excludes:
        raise ValueError(
            f"Cannot exclude required kwargs {sorted(bad_excludes)} from "
            f"params for task {task_name!r} — retry_failed_job reconstructs "
            "task_kwargs from params and would crash with TypeError. Either "
            "stop excluding them, store a reference (e.g. a file path), or "
            "make the kwarg optional on the task signature."
        )

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
    task_id = str(uuid4())
    update_job_celery_task_id(session, job, task_id)
    # Workers must see the pending record and task ID before the broker can deliver it.
    session.commit()

    # Dispatch Celery task - if this fails, mark job as failed
    try:
        celery_app.send_task(task_name, kwargs=final_kwargs, task_id=task_id)
    except Exception as e:
        # Task dispatch failed - mark job as failed so it doesn't sit in PENDING forever
        job.mark_failed(f"Failed to dispatch Celery task: {e}")
        session.commit()
        logger.error(f"Failed to dispatch task {task_name} for job {job.id}: {e}")
        raise

    session.refresh(job)

    return DispatchResult(
        job=job,
        is_new=True,
        message=f"Job queued. Track status via GET /api/jobs/{job.id}",
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

    locked_job.mark_pending()
    # A manual retry is a fresh chance, so earlier lost starts don't count towards MAX_TASK_STARTS.
    locked_job.attempts = 0
    session.flush()

    # Reconstruct task_kwargs from params (excluding internal fields)
    task_kwargs = {k: v for k, v in locked_job.params.items() if not k.startswith("_")}

    # Dispatch with same job_id - if this fails, revert status
    final_kwargs = {**task_kwargs, "job_id": locked_job.id}
    task_id = str(uuid4())
    update_job_celery_task_id(session, locked_job, task_id)
    # Workers must see the pending record and task ID before the broker can deliver it.
    session.commit()
    try:
        celery_app.send_task(task_name, kwargs=final_kwargs, task_id=task_id)
    except Exception as e:
        # Dispatch failed - revert to failed status
        locked_job.mark_failed(f"Failed to dispatch retry task: {e}")
        session.commit()
        logger.error(f"Failed to dispatch retry for job {locked_job.id}: {e}")
        raise

    session.refresh(locked_job)

    logger.info(f"Retrying job {locked_job.id} (attempt {locked_job.attempts + 1})")

    return DispatchResult(
        job=locked_job,
        is_new=False,
        message=f"Job {locked_job.id} queued for retry. Track status via GET /api/jobs/{locked_job.id}",
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

    locked_job.mark_pending()
    locked_job.result_id = None
    locked_job.result_type = None
    session.flush()

    # Reconstruct task_kwargs from params (excluding internal fields)
    task_kwargs = {k: v for k, v in locked_job.params.items() if not k.startswith("_")}

    # Dispatch with same job_id
    final_kwargs = {**task_kwargs, "job_id": locked_job.id}
    task_id = str(uuid4())
    update_job_celery_task_id(session, locked_job, task_id)
    # Workers must see the pending record and task ID before the broker can deliver it.
    session.commit()
    try:
        celery_app.send_task(task_name, kwargs=final_kwargs, task_id=task_id)
    except Exception as e:
        # Dispatch failed - mark as failed
        locked_job.mark_failed(f"Failed to dispatch reingest task: {e}")
        session.commit()
        logger.error(f"Failed to dispatch reingest for job {locked_job.id}: {e}")
        raise

    session.refresh(locked_job)

    logger.info(f"Reingesting job {locked_job.id}")

    return DispatchResult(
        job=locked_job,
        is_new=False,
        message=f"Job {locked_job.id} queued for reingest. Track status via GET /api/jobs/{locked_job.id}",
    )


# ---------------------------------------------------------------------------
# @tracked_task decorator
# ---------------------------------------------------------------------------

# acks_late + reject_on_worker_lost requeue a task whose worker died, so a task that
# always kills its worker (OOM, segfault) would otherwise be redelivered forever.
MAX_TASK_STARTS = 3
WORKER_LOST_ERROR = f"Worker lost on {MAX_TASK_STARTS} starts; not retrying"
LOST_JOB_ERROR = "Worker lost: still processing long after the task time limit"


def killed_its_worker(session: DBSession, job_type: str, **params: Any) -> bool:
    """Whether jobs of job_type with these params lost their worker on MAX_TASK_STARTS starts.

    Counts starts across jobs, so work re-queued under a new task id each time
    (a sync re-finding the same item) is caught too.
    """
    lost_starts = (
        session.query(func.coalesce(func.sum(PendingJob.attempts), 0))
        .filter(
            PendingJob.job_type == job_type,
            PendingJob.status == JobStatus.FAILED.value,
            PendingJob.error_message.in_([WORKER_LOST_ERROR, LOST_JOB_ERROR]),
            PendingJob.params.contains(params),
        )
        .scalar()
    )
    return lost_starts >= MAX_TASK_STARTS


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
    - Automatic/beat: one PendingJob (user_id=NULL) per Celery task id, which a
      redelivery of the same message reuses

    A start that finds its job still processing follows a dead worker; after
    MAX_TASK_STARTS such starts the job fails and the message is dropped.

    The ``job_id`` kwarg is consumed by the decorator and NOT passed to the
    wrapped function, nor is ``caused_by`` (see safe_task_execution); neither
    is kept in the job's params.

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
            # Kept out of params: a retry from them is not caused by the delivery.
            caused_by = kwargs.pop("caused_by", None)

            task_self = args[0] if args and hasattr(args[0], "request") else None
            request = celery_request(task_self)

            # Build params snapshot for debugging/retry
            task_name = get_celery_task_name(fn, args)
            params = _build_job_params(task_name, fn, args, kwargs)

            actual_job_id, should_run = _start_or_create_job(
                incoming_job_id=incoming_job_id,
                job_type=resolved_job_type,
                celery_task_id=getattr(request, "id", None),
                retries=getattr(request, "retries", None) or 0,
                params=params,
            )
            if not should_run:
                report_dropped_task(task_name, fn.__name__, kwargs)
                raise Reject(WORKER_LOST_ERROR, requeue=False)

            try:
                result = safe_fn(*args, caused_by=caused_by, **kwargs)
            except Retry:
                # Pending, not processing: the retried run reuses this row by task id
                # and must not mistake it for one whose worker died.
                update_job(actual_job_id, PendingJob.mark_pending)
                raise
            except TaskPredicate:
                # Reject and Ignore end the task by design.
                update_job(actual_job_id, lambda job: job.mark_complete())
                raise
            except Exception as exc:
                error = str(exc)[:500]
                update_job(actual_job_id, lambda job: job.mark_failed(error))
                raise

            update_job(actual_job_id, lambda job: job.mark_complete(*result_link(result)))
            return result

        return wrapper  # type: ignore[return-value]

    if func is not None:
        return decorator(func)
    return decorator


def celery_request(task_self: Any) -> Any:
    """The Celery request of a bound task, else of current_task (None outside a task)."""
    if task_self and getattr(task_self.request, "id", None):
        return task_self.request
    return getattr(current_task, "request", None)


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
    retries: int,
    params: dict[str, Any],
) -> tuple[int | None, bool]:
    """Start this delivery's job row, creating it if needed.

    Returns the job id (None when untracked) and whether the task should run.
    """
    try:
        with make_session() as session:
            job = None
            if incoming_job_id:
                job = session.get(PendingJob, incoming_job_id, with_for_update=True)
                if not job:
                    logger.warning("Job %s not found, proceeding without tracking", incoming_job_id)
                    return None, True
            elif celery_task_id:
                # A message redelivered after its worker died keeps its task id.
                job = (
                    session.query(PendingJob)
                    .filter(
                        PendingJob.celery_task_id == celery_task_id,
                        PendingJob.job_type == job_type,
                    )
                    .order_by(PendingJob.id.desc())
                    .with_for_update()
                    .first()
                )
            if job is None:
                job = create_job(session, job_type=job_type, params=params)
                session.flush()

            # Still processing means the previous start never finished: its worker died.
            # Each Celery retry was a start that did finish.
            lost_starts = job.attempts - retries
            if job.status == JobStatus.PROCESSING.value and lost_starts >= MAX_TASK_STARTS:
                job.mark_failed(WORKER_LOST_ERROR)
                logger.error("Job %s: worker lost on %s starts, dropping the task", job.id, lost_starts)
                return job.id, False

            job.mark_processing()
            if celery_task_id:
                update_job_celery_task_id(session, job, celery_task_id)
            return job.id, True
    except Exception:
        logger.warning("Failed to start/create job tracking, proceeding without", exc_info=True)
        return None, True


def result_link(result: Any) -> tuple[int | None, str | None]:
    """The (result_id, result_type) a task's result dict links its job to."""
    if not isinstance(result, dict):
        return None, None
    return result.get("result_id"), result.get("result_type")


def report_dropped_task(task_name: str, fn_name: str, kwargs: dict[str, Any]) -> None:
    """Record a failure metric and notification for a task dropped for losing its worker."""
    try:
        record_metric(
            metric_type="task",
            name=task_name,
            status="failure",
            labels={"error": WORKER_LOST_ERROR},
        )
    except Exception:
        logger.warning("Failed to record metric for %s", task_name, exc_info=True)
    notify_task_failure(task_name=fn_name, error_message=WORKER_LOST_ERROR, task_kwargs=kwargs)


def update_job(job_id: int | None, change: Callable[[PendingJob], None]) -> None:
    """Apply change to a job row, swallowing DB errors so tracking never fails a task."""
    if not job_id:
        return
    try:
        with make_session() as session:
            job = session.get(PendingJob, job_id)
            if job:
                change(job)
    except Exception:
        logger.warning("Failed to update job %s", job_id, exc_info=True)
