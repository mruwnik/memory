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
from typing import TYPE_CHECKING, cast

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
    """Parse a rate-limit spec like "10/minute" → (10, 60), in the ``limits``
    package's spec syntax."""
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
    """Convenience wrapper: parse the spec then enforce."""
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


def client_address(request: "Request") -> str:
    """The client's address, believing ``X-Forwarded-For`` only from trusted proxies.

    A key that honors ``X-Forwarded-For`` from any peer (as Uvicorn does with
    ``--proxy-headers``) lets a remote attacker rotate XFF to mint a fresh
    bucket per request and defeat the rate limit.

    Trust ``X-Forwarded-For`` only when the *immediate* TCP peer is a
    configured trusted proxy (``RATE_LIMIT_TRUSTED_PROXIES``: addresses
    or CIDRs, like Uvicorn's ``--forwarded-allow-ips``), and then only
    the hops trusted proxies appended: the address is the right-most entry
    that isn't itself a trusted proxy. Entries to its left were sent by
    the client and can say anything. The
    ``"*"`` wildcard exists for parity with Uvicorn's
    ``--forwarded-allow-ips=*`` default but explicitly opts out of the
    spoofing protection — operators behind a real proxy should list its
    IP instead.
    """
    immediate = request.client.host if request.client else "unknown"
    if not is_trusted_proxy(immediate):
        return immediate
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    untrusted = [h for h in hops if not is_trusted_proxy(h)]
    if untrusted:
        return untrusted[-1]
    # Every hop is a trusted proxy (or "*" trusts all): the left-most is the client.
    return hops[0] if hops else immediate


# NAT64 well-known prefix (RFC 6052): the client's IPv4 address is the last 32 bits.
NAT64_WELL_KNOWN = ipaddress.IPv6Network("64:ff9b::/96")
# Local-use NAT64 (RFC 8215): the IPv4 position depends on the operator's prefix
# length, so such clients are keyed by their full address.
NAT64_LOCAL_USE = ipaddress.IPv6Network("64:ff9b:1::/48")


def client_identity(address: str) -> str:
    """The exact client address, for checks about this one client (such as a
    remembered login address): an IPv4-mapped IPv6 address as its IPv4 address,
    anything else unchanged."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return str(ip.ipv4_mapped)
    return str(ip)


def embedded_ipv4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """The IPv4 address a translated or tunnelled IPv6 address stands for."""
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip in NAT64_WELL_KNOWN:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if ip.teredo is not None:
        return ip.teredo[1]
    return ip.sixtofour


def address_bucket(address: str) -> str:
    """The rate-limit counting key for a client address: an IPv4 address as is;
    an IPv6 address that stands for an IPv4 one (IPv4-mapped, NAT64, Teredo,
    6to4) as that IPv4 address, since those prefixes are shared by many clients;
    a local-use NAT64 address as is; any other IPv6 address as its /64 (one host
    or VPS is routed a whole /64 and may use any address in it). Anything that
    isn't an IP address is returned unchanged."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if isinstance(ip, ipaddress.IPv4Address):
        return str(ip)
    if (ipv4 := embedded_ipv4(ip)) is not None:
        return str(ipv4)
    if ip in NAT64_LOCAL_USE:
        return str(ip)
    return str(ipaddress.IPv6Network((int(ip) >> 64 << 64, 64)))


def rate_limit_key(request: "Request") -> str:
    """Bucket key for rate limiting: the client's address (``client_address``),
    with IPv6 clients keyed by their /64 (``address_bucket``).

    Use this everywhere a rate-limit bucket is keyed on client IP, never
    ``X-Forwarded-For`` from a peer outside ``RATE_LIMIT_TRUSTED_PROXIES``:
    a client that can set it mints a fresh bucket per request.
    """
    return address_bucket(client_address(request))


def rate_limit_reached(key: str, spec: str) -> bool:
    """Whether ``key``'s current window is already full, without counting a hit.
    Reads the in-process count while Redis is unavailable, as check_rate_limit
    counts there then."""
    if not settings.API_RATE_LIMIT_ENABLED:
        return False
    limit, window = parse_limit(spec)
    client = rate_limit_redis()
    if client is None:
        return local_limit_reached(key, limit, window)
    redis_key = f"rl:{key}:{int(time.time()) // window}"
    try:
        # redis-py types sync and async results together.
        count = cast("bytes | None", client.get(redis_key))
    except Exception as exc:
        logger.warning("rate_limit: Redis op failed, reading in process: %s", type(exc).__name__)
        back_off_redis()
        return local_limit_reached(key, limit, window)
    return int(count or 0) >= limit


def local_limit_reached(key: str, limit: int, window_seconds: int) -> bool:
    return not local_limiter.test(RateLimitItemPerSecond(limit, window_seconds), "rl", key)


def remember(key: str, ttl_seconds: int) -> None:
    """Record ``key`` for ``ttl_seconds``; a Redis failure loses the record."""
    client = rate_limit_redis()
    if client is None:
        return
    try:
        client.set(f"rl-mark:{key}", 1, ex=ttl_seconds)
    except Exception as exc:
        logger.warning("rate_limit: Redis op failed: %s", type(exc).__name__)
        back_off_redis()


def remembered(key: str) -> bool:
    client = rate_limit_redis()
    if client is None:
        return False
    try:
        return bool(client.exists(f"rl-mark:{key}"))
    except Exception as exc:
        logger.warning("rate_limit: Redis op failed: %s", type(exc).__name__)
        back_off_redis()
        return False
