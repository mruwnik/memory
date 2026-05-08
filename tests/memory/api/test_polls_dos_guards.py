"""Tests for poll-endpoint DoS guards (rate limit + per-request slot cap).

Hermetic — exercises the helper functions and rate-limit integration directly,
without requiring Postgres. End-to-end DB tests live in test_polls.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from memory.api.polls import (
    MAX_EMAIL_LENGTH,
    MAX_POLL_AVAILABILITIES_PER_REQUEST,
    POLL_RESPONSE_RATE_LIMIT,
    PollResponseRequest,
    enforce_poll_response_rate_limit,
    reject_oversized_poll_request,
    sanitize_email,
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


def test_rate_limit_does_not_trust_xff_from_untrusted_peer():
    """Regression: enforce_poll_response_rate_limit previously used
    slowapi.util.get_remote_address, which honors X-Forwarded-For whenever
    Uvicorn was started with --proxy-headers. A remote attacker could rotate
    XFF per request to mint fresh buckets and defeat the cap entirely.

    Now that the function uses rate_limit_key, XFF must be ignored when the
    immediate TCP peer is not in RATE_LIMIT_TRUSTED_PROXIES — the bucket key
    falls back to the direct connection IP, so 11 requests from one peer
    still hit the 10/minute cap regardless of how many XFF values the
    attacker rotates through.
    """
    from starlette.datastructures import Headers

    client = _stub_redis_client()

    # 11 requests from the same peer 10.0.0.99, each with a DIFFERENT
    # X-Forwarded-For. The pre-fix code would have created 11 buckets;
    # the new code keys on 10.0.0.99 throughout.
    requests_with_spoofed_xff = []
    for i in range(11):
        request = MagicMock()
        request.client.host = "10.0.0.99"
        request.headers = Headers({"X-Forwarded-For": f"203.0.113.{i}"})
        requests_with_spoofed_xff.append(request)

    with (
        patch.object(rate_limit, "get_redis", return_value=client),
        # Trust no proxies — the typical default for a public-facing API.
        patch("memory.common.settings.RATE_LIMIT_TRUSTED_PROXIES", ""),
    ):
        # First 10 succeed
        for request in requests_with_spoofed_xff[:10]:
            enforce_poll_response_rate_limit(request, "slug-x")
        # 11th must 429 — XFF spoofing did not buy a fresh bucket
        with pytest.raises(HTTPException) as exc_info:
            enforce_poll_response_rate_limit(
                requests_with_spoofed_xff[10], "slug-x"
            )
    assert exc_info.value.status_code == 429


def test_rate_limit_honors_xff_from_trusted_proxy():
    """When the immediate hop is a trusted proxy, the XFF original-client
    IP is used — so two different real clients behind the same proxy each
    get their own bucket (rather than sharing one bucket keyed on the
    proxy's IP)."""
    from starlette.datastructures import Headers

    client = _stub_redis_client()
    proxy_ip = "10.0.0.5"

    request_alice = MagicMock()
    request_alice.client.host = proxy_ip
    request_alice.headers = Headers({"X-Forwarded-For": "203.0.113.7"})

    request_bob = MagicMock()
    request_bob.client.host = proxy_ip
    request_bob.headers = Headers({"X-Forwarded-For": "203.0.113.8"})

    with (
        patch.object(rate_limit, "get_redis", return_value=client),
        patch("memory.common.settings.RATE_LIMIT_TRUSTED_PROXIES", proxy_ip),
    ):
        # Burn Alice's 10/minute allowance
        for _ in range(10):
            enforce_poll_response_rate_limit(request_alice, "slug-y")
        # Bob still gets through — different XFF, separate bucket
        enforce_poll_response_rate_limit(request_bob, "slug-y")
        # 11th from Alice is denied
        with pytest.raises(HTTPException) as exc_info:
            enforce_poll_response_rate_limit(request_alice, "slug-y")
    assert exc_info.value.status_code == 429


# ====== sanitize_email ======
#
# /polls/respond is a public unauthenticated endpoint, so respondent_email
# was a stored-XSS / DoS / PII-harvest sink (CWE-20, CWE-79). The fix is
# the same hardening pattern as sanitize_name: trim, length-cap, basic
# shape check, HTML-escape on the way in.


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("alice@example.com", "alice@example.com"),
        # Whitespace is trimmed.
        ("  bob@example.com  ", "bob@example.com"),
        # Plus-addressing and dots survive intact.
        ("alice+tag@sub.example.co.uk", "alice+tag@sub.example.co.uk"),
    ],
)
def test_sanitize_email_accepts_valid(raw, expected):
    assert sanitize_email(raw) == expected


def test_sanitize_email_html_escapes_metachars():
    """Defense-in-depth: any <, >, & embedded in the address is escaped on
    the way in, so a future renderer that drops the value into HTML
    inherits a safe default.
    """
    # Crafted to pass the shape check (one @, dot in domain) while smuggling
    # an HTML payload — the kind of thing that bites if we ever email or
    # render the value verbatim.
    out = sanitize_email("a<svg>@evil.example")
    assert out is not None
    assert "<svg>" not in out
    assert "&lt;svg&gt;" in out


@pytest.mark.parametrize(
    "raw",
    [
        "no-at-sign",
        "@nolocal.example",
        "no-domain@",
        "two@@ats.example",
        "no-tld@nodomain",
        "spaces in@local.example",
        "newline\n@injection.example",  # CRLF injection
        "carriage\r@injection.example",
        "\x01ctrl@example.com",  # control char
    ],
)
def test_sanitize_email_rejects_malformed(raw):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        sanitize_email(raw)
    assert exc_info.value.status_code == 400


def test_sanitize_email_rejects_oversized():
    """RFC 5321 caps at 254 chars. Above that we 400 to prevent column-
    overflow / DoS via multi-megabyte payloads."""
    from fastapi import HTTPException

    over = "a" * (MAX_EMAIL_LENGTH + 1) + "@example.com"
    with pytest.raises(HTTPException) as exc_info:
        sanitize_email(over)
    assert exc_info.value.status_code == 400
    assert "characters or less" in exc_info.value.detail


def test_sanitize_email_at_cap_passes():
    """Exactly MAX_EMAIL_LENGTH chars must still be accepted."""
    local = "a" * (MAX_EMAIL_LENGTH - len("@example.com"))
    addr = f"{local}@example.com"
    assert len(addr) == MAX_EMAIL_LENGTH
    assert sanitize_email(addr) == addr
