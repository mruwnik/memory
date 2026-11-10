"""Webhook-driven refetches of one issue or PR, debounced per number so a burst
of events (a push and its checks, a review and its comments) costs one fetch."""

import contextlib

import redis

from memory.common.celery_app import REFETCH_GITHUB_ITEM, app
from memory.common.redis_pool import get_redis

REFETCH_DELAY_SECONDS = 15
# The mark outlives its countdown by this much, so one lost with a dead worker
# lapses before long.
QUEUED_GRACE_SECONDS = 15 * 60


def queued_key(repo_id: int, number: int) -> str:
    return f"memory:github:refetch-queued:{repo_id}:{number}"


def queue_refetch(repo_id: int, number: int, kind: str) -> bool:
    """Queue a refetch unless one is already waiting; True if this call queued it."""
    key = queued_key(repo_id, number)
    if not get_redis().set(key, 1, nx=True, ex=REFETCH_DELAY_SECONDS + QUEUED_GRACE_SECONDS):
        return False
    try:
        app.send_task(
            REFETCH_GITHUB_ITEM, args=[repo_id, number, kind], countdown=REFETCH_DELAY_SECONDS
        )
    except Exception:
        # Nothing was queued, so nothing may wait on the mark.
        with contextlib.suppress(redis.RedisError):
            get_redis().delete(key)
        raise
    return True


def refetch_started(repo_id: int, number: int) -> None:
    """A queued refetch began, so events from here on need another one."""
    get_redis().delete(queued_key(repo_id, number))
