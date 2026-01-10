"""API endpoints for job status tracking."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session as DBSession

from memory.api.auth import get_current_user
from memory.common.db.connection import get_session
from memory.common.db.models import PendingJob, PendingJobPayload, User, JobStatus
from memory.common import jobs as job_utils

router = APIRouter(prefix="/jobs", tags=["jobs"])


# NOTE: /external/{external_id} must come BEFORE /{job_id} to avoid
# FastAPI treating "external" as a job_id integer
@router.get("/external/{external_id}")
def get_job_by_external_id(
    external_id: str,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
    job_type: str | None = Query(None, description="Filter by job type"),
) -> PendingJobPayload:
    """
    Get job status by external ID (client-provided idempotency key).

    This is useful for checking the status of a job when you only have
    the external_id you provided when creating it.
    """
    job = job_utils.get_job_by_external_id(db, external_id, job_type)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.user_id and job.user_id != user.id:
        raise HTTPException(status_code=404, detail="Job not found")

    return PendingJobPayload.model_validate(job)


@router.get("/{job_id}")
def get_job(
    job_id: int,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> PendingJobPayload:
    """
    Get the status of a specific job.

    Returns 404 if the job doesn't exist or doesn't belong to the user.
    """
    job = job_utils.get_job(db, job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Users can only see their own jobs (admins could see all)
    if job.user_id and job.user_id != user.id:
        raise HTTPException(status_code=404, detail="Job not found")

    return PendingJobPayload.model_validate(job)


@router.get("")
def list_jobs(
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
    status: str | None = Query(None, description="Filter by status"),
    job_type: str | None = Query(None, description="Filter by job type"),
    limit: int = Query(50, ge=1, le=200, description="Maximum results"),
    offset: int = Query(0, ge=0, description="Results to skip"),
) -> list[PendingJobPayload]:
    """
    List jobs for the current user.

    Supports filtering by status and job type.
    """
    jobs = job_utils.list_jobs(
        db,
        status=status,
        job_type=job_type,
        user_id=user.id,
        limit=limit,
        offset=offset,
    )

    return [PendingJobPayload.model_validate(job) for job in jobs]


@router.post("/{job_id}/retry")
def retry_job(
    job_id: int,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> PendingJobPayload:
    """
    Retry a failed job.

    Only failed jobs can be retried. Creates a new job with the same
    parameters and dispatches it for processing.
    """
    job = job_utils.get_job(db, job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.user_id and job.user_id != user.id:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != JobStatus.FAILED.value:
        raise HTTPException(
            status_code=400,
            detail=f"Only failed jobs can be retried. Current status: {job.status}",
        )

    # Job retry requires per-type dispatch logic that isn't implemented yet
    raise HTTPException(
        status_code=501,
        detail="Job retry is not yet implemented. Please create a new job instead.",
    )
