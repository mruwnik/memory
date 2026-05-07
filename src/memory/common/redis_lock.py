"""Redis-backed distributed lock helper.

Centralises the SET-NX-EX + Lua-based check-and-delete release pattern
that was previously duplicated across worker tasks (email sync, git
notes, Slack channel sync) — and was drifting: the Slack copy was
releasing locks via plain ``client.delete()``, with no ownership check,
which meant a slow worker whose lock had already expired could clobber
another worker's freshly-acquired lock.

Two surfaces:

- ``distributed_lock(name, ttl)`` — context manager that yields a
  :class:`Lock` on success, or ``None`` if another holder has it.
  Use this for the common "skip if another worker is holding it" flow.

- :class:`Lock` — handle returned by ``distributed_lock``. Exposes
  :meth:`Lock.extend` for renewing the TTL during a long-running
  operation (used by the email sync renewer).

The release path uses an atomic Lua script keyed off a per-acquisition
random token, so we only ever delete a lock that we still own.
"""
from __future__ import annotations

import contextlib
import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import redis

from memory.common import settings

logger = logging.getLogger(__name__)


# Lua: delete the key only if its value still equals our token. Used to
# release locks atomically without races against an expired-then-
# reacquired lock owned by a different worker.
_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

# Lua: extend the TTL only if we still own the lock.
_EXTEND_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
else
    return 0
end
"""


@dataclass
class Lock:
    """Handle to a held distributed lock.

    Created by :func:`distributed_lock`; do not instantiate directly.
    Calling :meth:`extend` returns ``True`` if we still owned the lock
    and the TTL was reset, ``False`` if the lock has been lost (e.g.
    expired and reacquired by another worker).
    """

    key: str
    token: str
    ttl_seconds: int
    client: redis.Redis

    def extend(self) -> bool:
        """Reset the lock's TTL if we still own it."""
        result = self.client.eval(
            _EXTEND_SCRIPT, 1, self.key, self.token, self.ttl_seconds  # type: ignore[arg-type]
        )
        return bool(result)

    def release(self) -> bool:
        """Release the lock if we still own it. Returns whether a key was deleted."""
        result = self.client.eval(
            _RELEASE_SCRIPT, 1, self.key, self.token  # type: ignore[arg-type]
        )
        return bool(result)


@contextlib.contextmanager
def distributed_lock(
    name: str,
    ttl_seconds: int,
    *,
    client: redis.Redis | None = None,
) -> Iterator[Lock | None]:
    """Acquire a distributed Redis lock under ``name`` with a TTL.

    Yields a :class:`Lock` if acquired, or ``None`` if another holder
    already has it. Releases atomically via the standard Lua check-and-
    delete script — i.e. we only ever delete the key if its current
    value still equals the token we set when we acquired it.

    Args:
        name: Logical lock name. Used as-is — the caller is responsible
            for namespacing (the historical convention is
            ``"memory:lock:<area>:<id>"``).
        ttl_seconds: Auto-expire after this long even if the holder
            crashes without releasing.
        client: Optional pre-built Redis client (mostly for tests). If
            omitted a fresh client is created against ``REDIS_URL``.
    """
    redis_client = client or redis.from_url(settings.REDIS_URL)
    token = str(uuid.uuid4())
    acquired = bool(redis_client.set(name, token, nx=True, ex=ttl_seconds))
    if not acquired:
        yield None
        return

    lock = Lock(key=name, token=token, ttl_seconds=ttl_seconds, client=redis_client)
    try:
        yield lock
    finally:
        # Ownership-checked release: a slow worker whose lock already
        # expired must NOT delete the key now held by a different
        # worker. (This is the bug the previous Slack copy had: it did
        # a plain ``client.delete()`` with no token check.)
        lock.release()
