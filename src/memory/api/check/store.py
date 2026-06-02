"""Redis-backed logic for the check job queue.

Every function takes the async redis client ``r`` explicitly so the layer is
trivially testable with fakeredis. No Lua, no reaper, no Celery:

- An ``open`` ZSET holds claimable job ids (FIFO by submit time).
- A per-job ``lease`` STRING with a TTL is the "in flight" marker; its value is
  the fencing token. Claiming is ``SET NX EX`` on that key, so exactly one
  worker wins; the TTL auto-expires to make a stuck job claimable again with no
  background sweeper.
- A per-user ``wake`` LIST is a doorbell: submit RPUSHes a token, a blocked
  ``/check/next`` BLPOPs it and re-scans.
"""

# redis-py types every async command as ``Awaitable[T] | T`` (the client class
# is shared between sync and async), so pyright resolves ``await r.hgetall(...)``
# to the non-awaitable ``T`` branch and mistypes BLPOP's return. These pragmas
# are scoped to this single raw-async-redis module; remove them when redis-py
# ships separate async stubs.
# pyright: reportGeneralTypeIssues=false, reportArgumentType=false

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, cast

import redis.asyncio as aioredis

from memory.api.check.redis_client import (
    job_key,
    jobs_index_key,
    lease_key,
    open_key,
    wake_key,
)
from memory.api.check.schemas import (
    JobAlreadyComplete,
    JobGone,
    JobRecord,
    NextJob,
    QueueFull,
    SubmitRequest,
)
from memory.common import settings

logger = logging.getLogger(__name__)

_WAKE_LIST_MAX = 16
_MAX_TX_RETRIES = 5


def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def new_job_id() -> str:
    return f"chk_{uuid.uuid4().hex}"


async def submit_job(r: aioredis.Redis, user_id: int, req: SubmitRequest) -> str:
    # Soft cap: the ZCARD/ZADD aren't atomic, so concurrent submits from one
    # user can transiently exceed CHECK_QUEUE_MAX_DEPTH. Accepted — this bounds
    # runaway growth, it isn't a hard quota. Depth = claimable jobs in the open
    # zset.
    depth = await r.zcard(open_key(user_id))
    if depth >= settings.CHECK_QUEUE_MAX_DEPTH:
        raise QueueFull()

    job_id = new_job_id()
    now = _now()
    mapping = {
        "job_id": job_id,
        "user_id": str(user_id),
        "status": "queued",
        "mode": req.mode,
        "text": req.text,
        "context": json.dumps(req.context),
        "callback_url": req.callback_url or "",
        "callback_token": req.callback_token or "",
        "submitted_at": _iso(now),
        "completed_at": "",
        "lease_id": "",
        "result": "",
        "error": "",
        "attempts": "0",
    }
    pipe = r.pipeline(transaction=True)
    pipe.hset(job_key(job_id), mapping=mapping)
    pipe.expire(job_key(job_id), settings.CHECK_JOB_TTL_SEC)
    pipe.zadd(open_key(user_id), {job_id: now})
    pipe.expire(open_key(user_id), settings.CHECK_JOB_TTL_SEC)
    pipe.zadd(jobs_index_key(user_id), {job_id: now})
    pipe.expire(jobs_index_key(user_id), settings.CHECK_JOB_TTL_SEC)
    # Doorbell: wake one blocked claimer; keep the list bounded.
    pipe.rpush(wake_key(user_id), "1")
    pipe.ltrim(wake_key(user_id), -_WAKE_LIST_MAX, -1)
    pipe.expire(wake_key(user_id), settings.CHECK_JOB_TTL_SEC)
    await pipe.execute()
    return job_id


async def get_job(r: aioredis.Redis, job_id: str) -> JobRecord | None:
    job = await r.hgetall(job_key(job_id))
    return cast(JobRecord, job) if job else None


async def _try_claim_one(r: aioredis.Redis, user_id: int) -> NextJob | None:
    """Scan the open zset oldest-first and lease the first free job.

    The lease is acquired with ``SET NX EX``, so among concurrent claimers
    exactly one creates the key and proceeds; the rest skip to the next id.
    """
    ids = await r.zrange(open_key(user_id), 0, -1)  # oldest-first
    for job_id in ids:
        lease_id = uuid.uuid4().hex
        ok = await r.set(
            lease_key(job_id), lease_id, nx=True, ex=settings.CHECK_LEASE_TTL_SEC
        )
        if not ok:
            continue  # someone else holds the lease

        job = await r.hgetall(job_key(job_id))
        if not job:
            # Tombstone: hash TTL-expired but id lingered in open. Clean up.
            await r.zrem(open_key(user_id), job_id)
            await r.delete(lease_key(job_id))
            continue

        # Atomic increment: a crash between the SET NX lease and this HINCRBY
        # leaves the lease to TTL-expire, and the count is durable rather than a
        # lost read-modify-write.
        attempts = await r.hincrby(job_key(job_id), "attempts", 1)
        if attempts > settings.CHECK_MAX_REQUEUE_ATTEMPTS:
            # Poison job: too many claims without completion. Give up.
            await r.hset(
                job_key(job_id),
                mapping={
                    "status": "expired",
                    "completed_at": _iso(_now()),
                    "lease_id": "",
                },
            )
            await r.zrem(open_key(user_id), job_id)
            await r.delete(lease_key(job_id))
            continue

        await r.hset(
            job_key(job_id),
            mapping={"status": "in_flight", "lease_id": lease_id},
        )
        return NextJob.from_record(
            cast(JobRecord, job),
            lease_id,
            _iso(_now() + settings.CHECK_LEASE_TTL_SEC),
        )
    return None


async def claim_next(r: aioredis.Redis, user_id: int, wait: int) -> NextJob | None:
    """Claim the next free job for ``user_id``, blocking up to ``wait`` seconds.

    Each pass scans the open zset and tries to ``SET NX`` a lease on the first
    free id. If none is free we BLPOP the user's doorbell, which a concurrent
    submit RPUSHes into; that wakes us to re-scan. The re-scan is the source of
    truth — the woken job may already have been taken by another worker, so we
    never trust the doorbell token itself.
    """
    deadline = _now() + wait
    while True:
        job = await _try_claim_one(r, user_id)
        if job is not None:
            return job
        remaining = deadline - _now()
        # BLPOP's minimum timeout is 1s and timeout=0 blocks forever, so a
        # sub-second remainder can't be honoured without overshooting the
        # caller's deadline. Give up instead. (wait=0 lands here with
        # remaining <= 0 and returns after the first scan.)
        if remaining < 1:
            return None
        await r.blpop([wake_key(user_id)], timeout=int(remaining))
        # loop back and re-scan


async def complete_job(
    r: aioredis.Redis,
    job_id: str,
    lease_id: str,
    status: str,
    result: dict[str, Any] | None,
    error: str | None,
) -> JobRecord:
    """Fenced commit of a worker result.

    The lease key's *value* is the fence and WATCH on it is what makes the fence
    atomic: we decide (terminal-status then lease-value checks) and commit inside
    a MULTI guarded by WATCH on the lease key. If another worker reclaims the
    lease (new SET) or the lease TTL-expires between WATCH and EXEC, EXEC aborts
    and we retry — the re-read then sees the new value (or nil) and yields
    JobGone (410). Without WATCH, a stalled worker that passed the value check
    could land its pipeline after a reclaim, clobbering the new holder's job and
    deleting its lease.
    """
    if status not in ("ok", "error"):
        raise ValueError(f"invalid completion status: {status!r}")

    lkey = lease_key(job_id)
    jkey = job_key(job_id)
    async with r.pipeline(transaction=True) as pipe:
        for _ in range(_MAX_TX_RETRIES):
            try:
                await pipe.watch(lkey)
                current = await pipe.hgetall(jkey)
                if not current:
                    await pipe.unwatch()
                    raise JobGone()
                # Terminal-status checks come before the lease check so a
                # completed/poisoned job reports its outcome (409/410) even
                # though its lease has already been deleted.
                st = current.get("status")
                if st in ("ok", "error"):
                    await pipe.unwatch()
                    raise JobAlreadyComplete()
                if st == "expired":
                    await pipe.unwatch()
                    raise JobGone()
                held = await pipe.get(lkey)
                if not lease_id or held != lease_id:
                    await pipe.unwatch()
                    raise JobGone()
                pipe.multi()
                pipe.hset(
                    jkey,
                    mapping={
                        "status": status,
                        "result": json.dumps(result) if result is not None else "",
                        "error": error or "",
                        "completed_at": _iso(_now()),
                    },
                )
                pipe.zrem(open_key(current["user_id"]), job_id)
                pipe.delete(lkey)
                await pipe.execute()
                break
            except aioredis.WatchError:
                continue  # lease changed/expired under us; re-read and re-decide
        else:
            raise JobGone()  # repeated contention; treat as a lost lease
    return cast(JobRecord, await r.hgetall(jkey))


async def list_jobs(
    r: aioredis.Redis,
    user_id: int,
    status: str | None,
    limit: int,
    offset: int,
) -> list[JobRecord]:
    """Newest-first list of a user's jobs, pruning index entries whose hash has
    expired.

    ``offset``/``limit`` page over *live, status-matching* jobs (not raw index
    positions), so a tombstone inside the window never shortens the returned
    page. We stop fetching once ``limit`` jobs are collected. With no status
    filter that means ~``offset+limit`` HGETALLs (plus any tombstones); with a
    status filter the budget counts only matching jobs, so a sparse filter can
    HGETALL many non-matching ids first — worst case O(jobs) for the user."""
    ids = await r.zrevrange(jobs_index_key(user_id), 0, -1)
    out: list[JobRecord] = []
    skipped = 0
    for job_id in ids:
        if len(out) >= limit:
            break
        job = await r.hgetall(job_key(job_id))
        if not job:
            await r.zrem(jobs_index_key(user_id), job_id)  # lazy prune tombstones
            continue
        if status is not None and job.get("status") != status:
            continue
        if skipped < offset:
            skipped += 1
            continue
        out.append(cast(JobRecord, job))
    return out


async def delete_job(r: aioredis.Redis, job: JobRecord) -> None:
    """Hard-delete a job from every structure: hash, open set, index, lease.

    Idempotent on the set/index/lease members. Does NOT abort a worker already
    running the job — its later complete_job sees the hash/lease gone and raises
    JobGone."""
    job_id = job["job_id"]
    user_id = job["user_id"]
    pipe = r.pipeline(transaction=True)
    pipe.delete(job_key(job_id))
    pipe.delete(lease_key(job_id))
    pipe.zrem(open_key(user_id), job_id)
    pipe.zrem(jobs_index_key(user_id), job_id)
    await pipe.execute()
