"""HTTP endpoints for the check job queue.

Route order matters: ``/check/next`` is declared before ``/check/{job_id}`` so
the literal isn't captured as a job id.
"""

import asyncio
import logging

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from memory.api.auth import require_scope
from memory.common.check import store
from memory.common.check.callbacks import deliver_callback
from memory.common.check.redis_client import get_check_redis
from memory.common.check.schemas import (
    JobAlreadyComplete,
    JobGone,
    JobRecord,
    JobStatusResponse,
    NextJob,
    PayloadTooLarge,
    QueueFull,
    ResultRequest,
    SubmitRequest,
    SubmitResponse,
)
from memory.common import settings
from memory.common.access_control import has_admin_scope
from memory.common.db.models.users import User
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
    # submit_rate_limit_ok is a sync-redis call; it's a single fast op so the
    # brief event-loop block is acceptable and not worth an async variant here.
    if not store.submit_rate_limit_ok(user.id):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    try:
        job_id = await store.submit_job(r, user_id=user.id, req=req)
    except PayloadTooLarge as e:
        raise HTTPException(status_code=413, detail=str(e))
    except QueueFull:
        raise HTTPException(
            status_code=429,
            detail={"error": "queue_full", "retry_after_seconds": 30},
        )
    return {"job_id": job_id, "status": "queued"}


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
    wait: int = Query(default=0),
    user: User = require_scope(SCOPE_CHECK),
    r: aioredis.Redis = Depends(get_check_redis),
):
    wait = max(0, min(wait, settings.CHECK_MAX_WAIT_SEC))
    rec = await store.get_job(r, job_id)
    if rec is None or (rec.get("user_id") != str(user.id) and not has_admin_scope(user)):
        raise HTTPException(status_code=404, detail="unknown job")
    if wait > 0 and rec.get("status") not in store.TERMINAL_STATUSES:
        rec = await store.wait_for_answer(r, job_id, timeout=wait)
        if rec is None:  # job's TTL expired mid-wait
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
