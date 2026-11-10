"""Short-lived Beeper state kept in Redis: which chats have a read queued, and
which accounts' WebSockets are connected."""

import contextlib

import redis

from memory.common.celery_app import SYNC_BEEPER_CHAT, app
from memory.common.redis_pool import get_redis

# A queued read's mark outlives its countdown by this much, so one lost with a
# dead worker lapses before long.
QUEUED_GRACE_SECONDS = 15 * 60
# The collector renews the mark each sync interval; one that died lets it lapse.
CONNECTED_TTL_SECONDS = 3 * 60


def queued_key(chat_pk: int) -> str:
    return f"memory:beeper:chat-queued:{chat_pk}"


def connected_key(account_id: int) -> str:
    return f"memory:beeper:connected:{account_id}"


def queue_chat_sync(chat_pk: int, countdown: int = 0, retry_stopped: bool = False) -> bool:
    """Queue a read of the chat unless one is already queued; True if this call queued it."""
    key = queued_key(chat_pk)
    if not get_redis().set(key, 1, nx=True, ex=countdown + QUEUED_GRACE_SECONDS):
        return False
    try:
        app.send_task(SYNC_BEEPER_CHAT, args=[chat_pk], kwargs={"retry_stopped": retry_stopped}, countdown=countdown)
    except Exception:
        # Nothing was queued, so nothing may wait on the mark; if Redis is down
        # too, the mark lapses on its own and the broker error is the one to raise.
        with contextlib.suppress(redis.RedisError):
            get_redis().delete(key)
        raise
    return True


def chat_sync_started(chat_pk: int) -> None:
    """A queued read began, so the next one may be queued."""
    get_redis().delete(queued_key(chat_pk))


def set_connected(account_id: int, connected: bool) -> None:
    if connected:
        get_redis().set(connected_key(account_id), 1, ex=CONNECTED_TTL_SECONDS)
    else:
        get_redis().delete(connected_key(account_id))


def is_connected(account_id: int) -> bool:
    return bool(get_redis().exists(connected_key(account_id)))
