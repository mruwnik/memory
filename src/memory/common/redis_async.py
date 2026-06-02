"""Shared async Redis client (redis.asyncio), the async counterpart to the
sync client used elsewhere in the codebase."""

import redis.asyncio as aioredis

from memory.common import settings

_client: aioredis.Redis | None = None


def get_async_redis() -> aioredis.Redis:
    """Return the shared process-wide async Redis client (decode_responses=True)."""
    global _client
    if _client is None:
        _client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client
