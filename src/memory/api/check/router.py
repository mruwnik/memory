"""HTTP endpoints for the check job queue.

Route order matters: ``/check/next`` is declared before ``/check/{job_id}`` so
the literal isn't captured as a job id.
"""

import asyncio
import json
import logging

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from memory.api.auth import require_scope
from memory.api.check import store
from memory.api.check.callbacks import deliver_callback
from memory.api.check.redis_client import get_check_redis
from memory.api.check.schemas import (
    JobAlreadyComplete,
    JobGone,
    JobRecord,
    JobStatusResponse,
    JobSummary,
    ListResponse,
    NextJob,
    QueueFull,
    ResultRequest,
    SubmitRequest,
    SubmitResponse,
)
from memory.common import settings
from memory.common.access_control import has_admin_scope
from memory.common.db.models.users import User
from memory.common.rate_limit import check_rate_limit_spec
from memory.common.scopes import SCOPE_CHECK

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/check", tags=["check"])

# Keep in-flight callback tasks referenced so they aren't GC'd mid-flight.
_callback_tasks: set[asyncio.Task] = set()


def _spawn_callback(job: JobRecord) -> None:
    if not job.get("callback_url"):
        return
    task = asyncio.create_task(deliver_callback(job))
    _callback_tasks.add(task)
    task.add_done_callback(_callback_tasks.discard)


@router.post("", response_model=SubmitResponse)
async def submit_check(
    req: SubmitRequest,
    user: User = require_scope(SCOPE_CHECK),
    r: aioredis.Redis = Depends(get_check_redis),
):
    if len(req.text.encode("utf-8")) > settings.CHECK_MAX_TEXT_BYTES:
        raise HTTPException(status_code=413, detail="text exceeds CHECK_MAX_TEXT_BYTES")
    # context is stored verbatim and echoed back on /check/next, so it gets the
    # same byte cap as text — otherwise a caller could sidestep the text limit by
    # stuffing the payload into context.
    if len(json.dumps(req.context).encode("utf-8")) > settings.CHECK_MAX_TEXT_BYTES:
        raise HTTPException(status_code=413, detail="context exceeds CHECK_MAX_TEXT_BYTES")
    # check_rate_limit_spec is a sync-redis call; it's a single fast op so the
    # brief event-loop block is acceptable and not worth an async variant here.
    if not check_rate_limit_spec(
        f"check_submit:{user.id}", f"{settings.CHECK_RATE_LIMIT_PER_MIN}/minute"
    ):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    try:
        job_id = await store.submit_job(r, user_id=user.id, req=req)
    except QueueFull:
        raise HTTPException(
            status_code=429,
            detail={"error": "queue_full", "retry_after_seconds": 30},
        )
    return {"job_id": job_id, "status": "queued"}


@router.get("", response_model=ListResponse)
async def list_checks(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = require_scope(SCOPE_CHECK),
    r: aioredis.Redis = Depends(get_check_redis),
):
    recs = await store.list_jobs(r, user_id=user.id, status=status,
                                 limit=limit, offset=offset)
    return ListResponse(jobs=[JobSummary.from_record(r) for r in recs])


@router.get("/next", response_model=NextJob)
async def next_check(
    wait: int = Query(default=30),
    user: User = require_scope(SCOPE_CHECK),
    r: aioredis.Redis = Depends(get_check_redis),
):
    wait = max(0, min(wait, settings.CHECK_MAX_LONG_POLL_SEC))
    job = await store.claim_next(r, user_id=user.id, wait=wait)
    if job is None:
        return Response(status_code=204)
    return job


@router.get("/{job_id}", response_model=JobStatusResponse)
async def poll_check(
    job_id: str,
    user: User = require_scope(SCOPE_CHECK),
    r: aioredis.Redis = Depends(get_check_redis),
):
    rec = await store.get_job(r, job_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="unknown job")
    if rec.get("user_id") != str(user.id) and not has_admin_scope(user):
        raise HTTPException(status_code=404, detail="unknown job")
    return JobStatusResponse.from_record(rec)


@router.post("/{job_id}/result")
async def submit_result(
    job_id: str,
    req: ResultRequest,
    user: User = require_scope(SCOPE_CHECK),
    r: aioredis.Redis = Depends(get_check_redis),
):
    # ResultRequest.status is Literal["ok","error"], so complete_job's
    # ValueError guard is unreachable via HTTP (Pydantic 422s first).
    try:
        rec = await store.complete_job(
            r, job_id, req.lease_id, status=req.status,
            result=req.result, error=req.error,
        )
    except JobAlreadyComplete:
        raise HTTPException(status_code=409, detail="job already completed")
    except JobGone:
        raise HTTPException(status_code=410, detail="job lease expired or unknown")
    _spawn_callback(rec)
    return {"ok": True}
