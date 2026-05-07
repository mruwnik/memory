"""Tests for poll-endpoint DoS guards (rate limit + per-request slot cap).

Hermetic — exercises the helper functions and rate-limit integration directly,
without requiring Postgres. End-to-end DB tests live in test_polls.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from memory.api.polls import (
    MAX_POLL_AVAILABILITIES_PER_REQUEST,
    POLL_RESPONSE_RATE_LIMIT,
    PollResponseRequest,
    enforce_poll_response_rate_limit,
    reject_oversized_poll_request,
)
from memory.common import rate_limit


@pytest.fixture(autouse=True)
def reset_rate_limit_cache():
    rate_limit.reset_cache()
    yield
    rate_limit.reset_cache()


def _make_request(ip: str = "1.2.3.4") -> MagicMock:
    """Build a Starlette-shaped request object with the given client IP."""
    request = MagicMock()
    request.client.host = ip
    request.headers = {}
    return request


# ====== reject_oversized_poll_request ======


def test_oversized_request_400():
    too_many = PollResponseRequest.model_validate(
        {
            "availabilities": [
                {
                    "slot_start": "2026-01-01T00:00:00+00:00",
                    "slot_end": "2026-01-01T00:30:00+00:00",
                }
                for _ in range(MAX_POLL_AVAILABILITIES_PER_REQUEST + 1)
            ]
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        reject_oversized_poll_request(too_many)
    assert exc_info.value.status_code == 400
    assert "Too many" in exc_info.value.detail


def test_at_cap_request_passes():
    at_cap = PollResponseRequest.model_validate(
        {
            "availabilities": [
                {
                    "slot_start": "2026-01-01T00:00:00+00:00",
                    "slot_end": "2026-01-01T00:30:00+00:00",
                }
                for _ in range(MAX_POLL_AVAILABILITIES_PER_REQUEST)
            ]
        }
    )
    # No exception
    reject_oversized_poll_request(at_cap)


def test_empty_request_passes():
    """A response with zero slots is structurally allowed (real validation
    happens in the slot loop). The DoS cap doesn't reject it."""
    empty = PollResponseRequest(availabilities=[])
    reject_oversized_poll_request(empty)


# ====== enforce_poll_response_rate_limit ======


def _stub_redis_client() -> MagicMock:
    """Build a redis double whose pipeline().incr() tracks per-key counters
    (required because the real code keys on (IP, slug) and we want test
    cases that exercise multiple keys to see distinct buckets)."""
    client = MagicMock()
    counters: dict[str, int] = {}
    pending_key: dict[str, str] = {}
    pipe = MagicMock()

    def incr(key, amount):
        counters[key] = counters.get(key, 0) + amount
        pending_key["k"] = key
        return pipe

    def expire(key, ttl):
        return pipe

    def execute():
        return [counters[pending_key["k"]], True]

    pipe.incr.side_effect = incr
    pipe.expire.side_effect = expire
    pipe.execute.side_effect = execute
    client.pipeline.return_value = pipe
    client.ping.return_value = True
    return client


def test_rate_limit_passes_under_threshold():
    client = _stub_redis_client()
    request = _make_request(ip="10.0.0.1")
    with patch.object(rate_limit, "get_redis", return_value=client):
        for _ in range(5):
            # 10/minute → 5 requests fine
            enforce_poll_response_rate_limit(request, "slug-a")


def test_rate_limit_429_when_exceeded():
    client = _stub_redis_client()
    request = _make_request(ip="10.0.0.2")

    # Burn through the 10/minute allowance
    with patch.object(rate_limit, "get_redis", return_value=client):
        for _ in range(10):
            enforce_poll_response_rate_limit(request, "slug-b")

        # 11th request gets 429
        with pytest.raises(HTTPException) as exc_info:
            enforce_poll_response_rate_limit(request, "slug-b")
    assert exc_info.value.status_code == 429
    assert "Too many" in exc_info.value.detail


def test_rate_limit_keyed_per_ip_and_slug():
    """Different IPs and different slugs get separate buckets — a noisy
    attacker doesn't lock out legitimate respondents on other polls."""
    client = _stub_redis_client()
    request_a = _make_request(ip="10.0.0.10")
    request_b = _make_request(ip="10.0.0.20")

    with patch.object(rate_limit, "get_redis", return_value=client):
        # Burn IP A's bucket on slug-1
        for _ in range(10):
            enforce_poll_response_rate_limit(request_a, "slug-1")
        # IP B on slug-1 still works (different IP)
        enforce_poll_response_rate_limit(request_b, "slug-1")
        # IP A on slug-2 still works (different slug)
        enforce_poll_response_rate_limit(request_a, "slug-2")


def test_rate_limit_fails_open_when_redis_down():
    """Redis outage must not lock public poll responses out entirely."""
    request = _make_request()
    with patch.object(rate_limit, "get_redis", return_value=None):
        for _ in range(50):
            # No HTTPException — fail open
            enforce_poll_response_rate_limit(request, "slug-x")


def test_rate_limit_constant_format():
    """The constant is in slowapi syntax so it can be configured later
    without code changes — guard against regressions."""
    count, window = rate_limit.parse_limit(POLL_RESPONSE_RATE_LIMIT)
    assert count == 10
    assert window == 60
