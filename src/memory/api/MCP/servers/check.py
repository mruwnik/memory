"""MCP subserver for the check job queue.

Thin wrappers over :mod:`memory.common.check.store`. Each tool resolves the
authenticated user and the async check-queue Redis client, then delegates to the
store layer; access is scoped to the caller's own jobs (admins may read/delete
any job).
"""

from fastmcp import FastMCP

from memory.api.MCP.access import get_mcp_current_user
from memory.api.MCP.visibility import require_scopes, visible_when
from memory.common import settings
from memory.common.access_control import has_admin_scope
from memory.common.check import store
from memory.common.check.redis_client import get_check_redis
from memory.common.check.schemas import (
    JobStatusResponse,
    JobSummary,
    PayloadTooLarge,
    QueueFull,
    SubmitRequest,
)
from memory.common.scopes import SCOPE_CHECK

check_mcp = FastMCP("memory-check")


@check_mcp.tool()
@visible_when(require_scopes(SCOPE_CHECK))
async def ask(
    text: str,
    mode: str = "research",
    context: dict | None = None,
    callback_url: str | None = None,
    callback_token: str | None = None,
    wait: int = 0,
) -> dict:
    """Submit a question to the check queue (verify/research/link).

    Returns the job id and status; if wait>0, polls up to that many seconds
    (capped at CHECK_MAX_WAIT_SEC) for the answer before returning — re-call
    wait_for_answer if still pending.

    Args:
        text: The question or claim to check.
        mode: One of "verify", "research", or "link".
        context: Optional structured context passed through to the worker.
        callback_url: Optional URL to POST the result to when answered.
        callback_token: Optional token echoed back in the callback for auth.
        wait: Seconds to wait inline for an answer (0 returns immediately).

    Returns: Job status including job_id, status, and result/error if answered.
    """
    user = get_mcp_current_user()
    if not user or user.id is None:
        raise ValueError("Not authenticated")
    r = get_check_redis()

    if not store.submit_rate_limit_ok(user.id):
        raise ValueError("rate limit exceeded")

    req = SubmitRequest(
        text=text,
        mode=mode,  # type: ignore[arg-type]
        context=context or {},
        callback_url=callback_url,
        callback_token=callback_token,
    )
    try:
        job_id = await store.submit_job(r, user_id=user.id, req=req)
    except PayloadTooLarge as e:
        raise ValueError(str(e))
    except QueueFull:
        raise ValueError("check queue is full; retry later")
    wait = max(0, min(wait, settings.CHECK_MAX_WAIT_SEC))
    if wait > 0:
        rec = await store.wait_for_answer(r, job_id, timeout=wait)
    else:
        rec = await store.get_job(r, job_id)
    if rec is None:
        raise ValueError("unknown job")
    return JobStatusResponse.from_record(rec).model_dump()


@check_mcp.tool()
@visible_when(require_scopes(SCOPE_CHECK))
async def wait_for_answer(job_id: str, seconds: int | None = None) -> dict:
    """Wait for a check job to be answered.

    Waits up to `seconds` (default CHECK_DEFAULT_WAIT_SEC, capped at
    CHECK_MAX_WAIT_SEC) for the job to reach a terminal status; returns its
    status/result, or its still-pending status if the wait runs out — re-call to
    keep waiting.

    Args:
        job_id: The job id returned by ask.
        seconds: Seconds to wait (default CHECK_DEFAULT_WAIT_SEC).

    Returns: Job status including job_id, status, and result/error if answered.
    """
    user = get_mcp_current_user()
    if not user or user.id is None:
        raise ValueError("Not authenticated")
    r = get_check_redis()

    seconds = seconds if seconds is not None else settings.CHECK_DEFAULT_WAIT_SEC
    rec = await store.get_job(r, job_id)
    if rec is None or (
        rec.get("user_id") != str(user.id) and not has_admin_scope(user)
    ):
        raise ValueError("unknown job")
    rec = await store.wait_for_answer(r, job_id, timeout=seconds)
    if rec is None:
        raise ValueError("unknown job")
    return JobStatusResponse.from_record(rec).model_dump()


@check_mcp.tool()
@visible_when(require_scopes(SCOPE_CHECK))
async def list_jobs(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """List your own check jobs, newest first.

    Args:
        status: Optional status filter (queued, in_flight, ok, error, expired).
        limit: Maximum jobs to return (default 50, clamped to 1..200).
        offset: Number of matching jobs to skip (default 0).

    Returns: {"jobs": [...]} where each entry has job_id, status, mode, and times.
    """
    user = get_mcp_current_user()
    if not user or user.id is None:
        raise ValueError("Not authenticated")
    r = get_check_redis()

    recs = await store.list_jobs(
        r,
        user_id=user.id,
        status=status,
        limit=min(max(limit, 1), 200),
        offset=max(offset, 0),
    )
    return {"jobs": [JobSummary.from_record(rec).model_dump() for rec in recs]}


@check_mcp.tool()
@visible_when(require_scopes(SCOPE_CHECK))
async def delete(job_id: str) -> dict:
    """Delete one of your check jobs.

    Args:
        job_id: The job id to delete.

    Returns: {"deleted": True, "job_id": ...}.
    """
    user = get_mcp_current_user()
    if not user or user.id is None:
        raise ValueError("Not authenticated")
    r = get_check_redis()

    rec = await store.get_job(r, job_id)
    if rec is None or (
        rec.get("user_id") != str(user.id) and not has_admin_scope(user)
    ):
        raise ValueError("unknown job")
    await store.delete_job(r, rec)
    return {"deleted": True, "job_id": job_id}
