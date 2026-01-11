"""
Job tracking utilities for async operations.

Provides functions to create, update, and query pending jobs.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from memory.common.celery_app import app as celery_app
from memory.common.db.models import PendingJob, JobStatus, JobType

logger = logging.getLogger(__name__)


def create_job(
    session: Session,
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


def get_job(session: Session, job_id: int) -> PendingJob | None:
    """Get a job by ID."""
    return session.get(PendingJob, job_id)


def get_job_by_external_id(
    session: Session,
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
    session: Session,
    job: PendingJob,
    celery_task_id: str,
) -> None:
    """Update job with Celery task ID for correlation."""
    job.celery_task_id = celery_task_id
    job.updated_at = datetime.now(timezone.utc)


def start_job(session: Session, job_id: int) -> PendingJob | None:
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
    session: Session,
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
    session: Session,
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
    session: Session,
    status: str | JobStatus | None = None,
    job_type: str | JobType | None = None,
    user_id: int | None = None,
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
    session: Session,
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
    session: Session,
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
    session: Session,
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
