"""The process-wide Redis connection pool."""

import redis

from memory.common import settings

_pool: redis.ConnectionPool | None = None


def get_redis() -> redis.Redis:
    """A client on the shared pool for REDIS_URL; cheap to call per use."""
    global _pool
    if _pool is None:
        _pool = redis.ConnectionPool.from_url(settings.REDIS_URL)
    return redis.Redis(connection_pool=_pool)
