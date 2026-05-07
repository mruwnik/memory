"""Lightweight Redis-backed rate limiting for endpoints SlowAPI doesn't cover.

The main API uses slowapi for default per-IP rate limits, but the MCP
custom_route handlers (e.g. /oauth/login) live on the FastMCP sub-app and
don't go through SlowAPI's middleware. Login is also a place where per-IP
limits are insufficient: an attacker rotating X-Forwarded-For has unbounded
throughput against a single victim account, so we want a per-account bucket
in addition to the per-IP one.

This module provides a thin sliding-window counter on top of the existing
Redis broker. It fails *open* on Redis errors so a Redis outage doesn't
take auth offline — that's a deliberate availability/security trade-off
appropriate for self-hosted deployments. Operators who want fail-closed
should configure Redis HA.
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING

import redis

from memory.common import settings

if TYPE_CHECKING:
    from starlette.requests import Request

logger = logging.getLogger(__name__)


_LIMIT_RE = re.compile(r"\s*(\d+)\s*/\s*(second|minute|hour|day)s?\s*", re.IGNORECASE)
_UNIT_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}

# Cached module-level client. None means we haven't tried yet, False means
# the last attempt failed and we're failing open until the next process.
_redis_client: redis.Redis | None | bool = None


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


def get_redis() -> redis.Redis | None:
    """Return a cached Redis client, or None if Redis is unavailable."""
    global _redis_client
    if _redis_client is False:
        return None
    if _redis_client is not None:
        return _redis_client  # type: ignore[return-value]
    try:
        client = redis.Redis.from_url(
            settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1
        )
        # Ping once so the cache reflects reality, not optimism
        client.ping()
        _redis_client = client
        return client
    except Exception as exc:
        logger.warning(
            "rate_limit: Redis unavailable, failing open: %s", type(exc).__name__
        )
        _redis_client = False
        return None


def reset_cache() -> None:
    """Clear the cached Redis client. Used by tests."""
    global _redis_client
    _redis_client = None


def check_rate_limit(key: str, limit: int, window_seconds: int) -> bool:
    """Return True if the request is allowed, False if it exceeds `limit`.

    Uses a fixed-window counter keyed on the rounded-down current window
    instant. Cheaper than a true sliding window and adequate for login
    throttling — the worst case is 2x the limit at the window boundary.

    Fails open: any Redis error counts as "allowed" so an outage doesn't
    take auth offline.
    """
    if not settings.API_RATE_LIMIT_ENABLED:
        return True
    client = get_redis()
    if client is None:
        return True
    bucket = int(time.time()) // window_seconds
    redis_key = f"rl:{key}:{bucket}"
    try:
        pipe = client.pipeline()
        pipe.incr(redis_key, 1)
        pipe.expire(redis_key, window_seconds + 1)
        count, _ = pipe.execute()
        return int(count) <= limit
    except Exception as exc:
        logger.warning(
            "rate_limit: Redis op failed, failing open: %s", type(exc).__name__
        )
        return True


def check_rate_limit_spec(key: str, spec: str) -> bool:
    """Convenience wrapper: parse the SlowAPI-style spec then enforce."""
    limit, window = parse_limit(spec)
    return check_rate_limit(key, limit, window)


def _trusted_proxies() -> set[str]:
    """Parse RATE_LIMIT_TRUSTED_PROXIES at call time so tests can patch it."""
    raw = settings.RATE_LIMIT_TRUSTED_PROXIES or ""
    return {p.strip() for p in raw.split(",") if p.strip()}


def rate_limit_key(request: "Request") -> str:
    """Bucket key for rate limiting that respects only trusted proxies.

    SlowAPI's bundled ``get_remote_address`` (and any Uvicorn started with
    ``--proxy-headers``) honors ``X-Forwarded-For`` regardless of the
    immediate TCP peer — so a remote attacker can rotate XFF to mint a
    fresh bucket per request and defeat the rate limit.

    Trust ``X-Forwarded-For`` only when the *immediate* TCP peer is a
    configured trusted proxy (``RATE_LIMIT_TRUSTED_PROXIES``). The
    ``"*"`` wildcard exists for parity with Uvicorn's
    ``--forwarded-allow-ips=*`` default but explicitly opts out of the
    spoofing protection — operators behind a real proxy should list its
    IP instead.

    Use this everywhere a rate-limit bucket is keyed on client IP. Do
    NOT call ``slowapi.util.get_remote_address`` directly — that helper
    silently inherits the spoofing bypass.
    """
    immediate = request.client.host if request.client else "unknown"
    trusted = _trusted_proxies()
    if "*" in trusted or immediate in trusted:
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            # Left-most entry is the original client per RFC 7239 §5.2.
            return xff.split(",")[0].strip() or immediate
    return immediate
