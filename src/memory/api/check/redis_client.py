"""Check job queue Redis key helpers and a thin client accessor.

The async ``redis.asyncio`` client itself now lives in
``memory.common.redis_async``; this module holds the check-specific key helpers
plus a thin accessor (``get_check_redis``) that delegates to the shared client.
The wrapper is kept so check tests can override a check-scoped dependency
without touching the global.
"""

import redis.asyncio as aioredis

from memory.common.redis_async import get_async_redis

def job_key(job_id: str) -> str:
    return f"check:job:{job_id}"


def open_key(user_id: int | str) -> str:
    """ZSET of claimable job ids for a user (score = submitted epoch, FIFO)."""
    return f"check:open:{user_id}"


def lease_key(job_id: str) -> str:
    """STRING (value = lease_id) marking a job in-flight; auto-expires via TTL."""
    return f"check:lease:{job_id}"


def wake_key(user_id: int | str) -> str:
    """LIST doorbell: submit RPUSHes a token, claim BLPOPs to wake."""
    return f"check:wake:{user_id}"


def jobs_index_key(user_id: int | str) -> str:
    return f"check:jobs:{user_id}"


def get_check_redis() -> aioredis.Redis:
    """Return the shared async Redis client used by the check job queue.

    Each in-flight ``/check/next`` holds one connection for the BLPOP wait
    (<=30s), so Redis connections scale ~1:1 with concurrent long-pollers. Fine
    at current scale; bound ``max_connections`` on the shared client if the
    worker fleet grows large.
    """
    return get_async_redis()
