"""Lightweight Redis-backed rate limiting for buckets tighter than the default.

Every request already counts against a per-client default limit
(``memory.api.request_limits``). Endpoints such as login need more: a
per-account bucket next to the per-IP one, since an attacker rotating
addresses has unbounded throughput against a single victim account.

This module provides a thin fixed-window counter on top of the existing
Redis broker. While Redis is unreachable it counts in this process instead,
so limits still hold per api process and an outage doesn't take auth
offline. A failed connection is retried after REDIS_RETRY_SECONDS.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import time
from typing import TYPE_CHECKING

from limits import RateLimitItemPerSecond
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter
import redis

from memory.common import settings

if TYPE_CHECKING:
    from starlette.requests import Request

logger = logging.getLogger(__name__)


_LIMIT_RE = re.compile(r"\s*(\d+)\s*/\s*(second|minute|hour|day)s?\s*", re.IGNORECASE)
_UNIT_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}

REDIS_RETRY_SECONDS = 30

# Cached module-level client, and the monotonic time before which a failed
# connection isn't retried.
_redis_client: redis.Redis | None = None
_redis_retry_at = 0.0

local_limiter = FixedWindowRateLimiter(MemoryStorage())


def parse_limit(spec: str) -> tuple[int, int]:
    """Parse a rate-limit spec like "10/minute" → (10, 60).

    Accepts the same shorthand format SlowAPI does so we don't have to
    bifurcate the configuration vocabulary.
    """
    match = _LIMIT_RE.fullmatch(spec)
    if not match:
        raise ValueError(f"Invalid rate-limit spec: {spec!r}")
    count = int(match.group(1))
    window = _UNIT_SECONDS[match.group(2).lower()]
    return count, window


def rate_limit_redis() -> redis.Redis | None:
    """Return a cached Redis client, or None if Redis is unavailable.

    Separate from `memory.common.redis_pool.get_redis`: this client has 1s
    socket timeouts and is pinged once. After a failed connection, None is
    returned without retrying for REDIS_RETRY_SECONDS.
    """
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    if time.monotonic() < _redis_retry_at:
        return None
    try:
        client = redis.Redis.from_url(
            settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1
        )
        # Ping once so the cache reflects reality, not optimism
        client.ping()
    except Exception as exc:
        logger.warning(
            "rate_limit: Redis unavailable, counting in process: %s", type(exc).__name__
        )
        back_off_redis()
        return None
    _redis_client = client
    return client


def back_off_redis() -> None:
    """Drop the cached client and don't reconnect for REDIS_RETRY_SECONDS."""
    global _redis_client, _redis_retry_at
    _redis_client = None
    _redis_retry_at = time.monotonic() + REDIS_RETRY_SECONDS


def reset_cache() -> None:
    """Forget the cached Redis client, the retry delay and the in-process counts."""
    global _redis_client, _redis_retry_at
    _redis_client = None
    _redis_retry_at = 0.0
    local_limiter.storage.reset()


def check_local_rate_limit(key: str, limit: int, window_seconds: int, cost: int = 1) -> bool:
    """count_hits' in-process counter, used while Redis is unavailable."""
    return local_limiter.hit(RateLimitItemPerSecond(limit, window_seconds), "rl", key, cost=cost)


def count_hits(key: str, limit: int, window_seconds: int, cost: int = 1) -> bool:
    """Count `cost` hits on `key`; True while its window's total stays within `limit`.

    Uses a fixed-window counter keyed on the rounded-down current window
    instant. Cheaper than a true sliding window and adequate for throttling:
    the worst case is 2x the limit at the window boundary.

    While Redis is unavailable, or on any Redis error, the hits are counted
    in this process instead.
    """
    client = rate_limit_redis()
    if client is None:
        return check_local_rate_limit(key, limit, window_seconds, cost)
    bucket = int(time.time()) // window_seconds
    redis_key = f"rl:{key}:{bucket}"
    try:
        pipe = client.pipeline()
        pipe.incr(redis_key, cost)
        pipe.expire(redis_key, window_seconds + 1)
        count, _ = pipe.execute()
        return int(count) <= limit
    except Exception as exc:
        logger.warning(
            "rate_limit: Redis op failed, counting in process: %s", type(exc).__name__
        )
        back_off_redis()
        return check_local_rate_limit(key, limit, window_seconds, cost)


def check_rate_limit(key: str, limit: int, window_seconds: int) -> bool:
    """Return True if the request is allowed, False if it exceeds `limit`
    (always True with API_RATE_LIMIT_ENABLED off)."""
    if not settings.API_RATE_LIMIT_ENABLED:
        return True
    return count_hits(key, limit, window_seconds)


def cross_token_reads_allowed(source: str, pusher_id: int, reads: int = 1) -> bool:
    """Whether pushes from `pusher_id` (a Slack app or Discord bot of `source`)
    may make `reads` more read-backs this minute with other users' tokens, whose
    API quota they spend."""
    return count_hits(
        f"cross-token:{source}:{pusher_id}", settings.PUSH_CROSS_TOKEN_READS_PER_MINUTE, 60, reads
    )


def check_rate_limit_spec(key: str, spec: str) -> bool:
    """Convenience wrapper: parse the SlowAPI-style spec then enforce."""
    limit, window = parse_limit(spec)
    return check_rate_limit(key, limit, window)


def is_trusted_proxy(host: str) -> bool:
    """Whether ``host`` matches RATE_LIMIT_TRUSTED_PROXIES: ``*``, an exact
    entry, or an address inside a CIDR entry. Parsed at call time so tests
    can patch it."""
    raw = settings.RATE_LIMIT_TRUSTED_PROXIES or ""
    entries = {p.strip() for p in raw.split(",") if p.strip()}
    if "*" in entries or host in entries:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    for entry in entries:
        try:
            network = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            continue
        if address in network:
            return True
    return False


def rate_limit_key(request: "Request") -> str:
    """Bucket key for rate limiting that respects only trusted proxies.

    SlowAPI's bundled ``get_remote_address`` (and any Uvicorn started with
    ``--proxy-headers``) honors ``X-Forwarded-For`` regardless of the
    immediate TCP peer — so a remote attacker can rotate XFF to mint a
    fresh bucket per request and defeat the rate limit.

    Trust ``X-Forwarded-For`` only when the *immediate* TCP peer is a
    configured trusted proxy (``RATE_LIMIT_TRUSTED_PROXIES``: addresses
    or CIDRs, like Uvicorn's ``--forwarded-allow-ips``). The
    ``"*"`` wildcard exists for parity with Uvicorn's
    ``--forwarded-allow-ips=*`` default but explicitly opts out of the
    spoofing protection — operators behind a real proxy should list its
    IP instead.

    Use this everywhere a rate-limit bucket is keyed on client IP. Do
    NOT call ``slowapi.util.get_remote_address`` directly — that helper
    silently inherits the spoofing bypass.
    """
    immediate = request.client.host if request.client else "unknown"
    if is_trusted_proxy(immediate):
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            # Left-most entry is the original client per RFC 7239 §5.2.
            return xff.split(",")[0].strip() or immediate
    return immediate
