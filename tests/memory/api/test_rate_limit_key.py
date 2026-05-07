"""Tests for the API rate-limit key function (``app.rate_limit_key``).

The threat model: SlowAPI's bundled ``get_remote_address`` honors
``X-Forwarded-For`` whenever Uvicorn was started with
``--proxy-headers``. With ``--forwarded-allow-ips=*`` (the Docker
default) every remote attacker can rotate ``X-Forwarded-For`` per
request and mint a fresh limiter bucket — defeating every rate limit.

``app.rate_limit_key`` accepts ``X-Forwarded-For`` only when the
*immediate* TCP peer is a configured trusted proxy.
"""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import pytest
from starlette.datastructures import Headers


@pytest.fixture
def make_request():
    """Build a minimal request-like object with given client + headers.

    Uses ``starlette.datastructures.Headers`` so header lookups are
    case-insensitive — matching the real ``request.headers`` behavior.
    """

    def _make(client_host: str | None, headers: dict[str, str] | None = None):
        client = SimpleNamespace(host=client_host) if client_host else None
        return SimpleNamespace(
            client=client, headers=Headers(headers or {})
        )

    return _make


def test_rate_limit_key_uses_direct_ip_when_no_trust(make_request):
    """Default config does not trust X-Forwarded-For — keying falls back
    to the direct connection IP regardless of header content."""
    from memory.api.app import rate_limit_key

    request = make_request("203.0.113.7", {"X-Forwarded-For": "9.9.9.9"})
    with patch("memory.common.settings.RATE_LIMIT_TRUSTED_PROXIES", ""):
        key = rate_limit_key(cast(Any, request))
    assert key == "203.0.113.7"


def test_rate_limit_key_ignores_xff_from_untrusted_hop(make_request):
    """An attacker connecting directly (not via the trusted proxy) cannot
    use ``X-Forwarded-For`` to rotate buckets."""
    from memory.api.app import rate_limit_key

    request = make_request(
        "1.2.3.4",
        {"X-Forwarded-For": f"{99}.{99}.{99}.{99}"},
    )
    with patch(
        "memory.common.settings.RATE_LIMIT_TRUSTED_PROXIES", "10.0.0.5"
    ):
        key = rate_limit_key(cast(Any, request))
    assert key == "1.2.3.4"


def test_rate_limit_key_honors_xff_from_trusted_proxy(make_request):
    """Behind a real reverse proxy, the immediate hop is the proxy and
    the original client lives in ``X-Forwarded-For``."""
    from memory.api.app import rate_limit_key

    request = make_request("10.0.0.5", {"X-Forwarded-For": "203.0.113.7"})
    with patch(
        "memory.common.settings.RATE_LIMIT_TRUSTED_PROXIES", "10.0.0.5"
    ):
        key = rate_limit_key(cast(Any, request))
    assert key == "203.0.113.7"


def test_rate_limit_key_uses_first_xff_entry(make_request):
    """RFC 7239 §5.2: the left-most XFF entry is the original client."""
    from memory.api.app import rate_limit_key

    request = make_request(
        "10.0.0.5",
        {"X-Forwarded-For": "203.0.113.7, 10.0.0.99, 10.0.0.5"},
    )
    with patch(
        "memory.common.settings.RATE_LIMIT_TRUSTED_PROXIES", "10.0.0.5"
    ):
        key = rate_limit_key(cast(Any, request))
    assert key == "203.0.113.7"


def test_rate_limit_key_falls_back_when_xff_empty(make_request):
    """Trusted proxy that didn't set XFF (e.g. a misconfigured one) → use
    the proxy's own IP rather than crashing."""
    from memory.api.app import rate_limit_key

    request = make_request("10.0.0.5", {})
    with patch(
        "memory.common.settings.RATE_LIMIT_TRUSTED_PROXIES", "10.0.0.5"
    ):
        key = rate_limit_key(cast(Any, request))
    assert key == "10.0.0.5"


def test_rate_limit_key_wildcard_trusts_anyone(make_request):
    """Operators who explicitly opt out of the spoofing protection get
    the old behavior — XFF is honored regardless of the immediate hop."""
    from memory.api.app import rate_limit_key

    request = make_request("1.2.3.4", {"X-Forwarded-For": "203.0.113.7"})
    with patch("memory.common.settings.RATE_LIMIT_TRUSTED_PROXIES", "*"):
        key = rate_limit_key(cast(Any, request))
    assert key == "203.0.113.7"


def test_rate_limit_key_handles_missing_client(make_request):
    """Test client with no ``request.client`` (e.g. ASGI lifespan) doesn't
    crash the key function."""
    from memory.api.app import rate_limit_key

    request = make_request(None, {"X-Forwarded-For": "9.9.9.9"})
    with patch("memory.common.settings.RATE_LIMIT_TRUSTED_PROXIES", ""):
        key = rate_limit_key(cast(Any, request))
    assert key == "unknown"


def test_dockerfile_forwarded_allow_ips_default_is_loopback():
    """Pin the Dockerfile default. The rate-limit tests above bypass
    Uvicorn entirely (constructing Request objects directly), so they
    cannot detect a regression where the Dockerfile flips back to ``*``
    and Uvicorn rewrites ``scope["client"]`` from XFF before
    ``rate_limit_key`` ever runs. This test catches that flip.
    """
    from pathlib import Path

    dockerfile = Path(__file__).resolve().parents[3] / "docker/api/Dockerfile"
    text = dockerfile.read_text()
    assert 'ENV FORWARDED_ALLOW_IPS="127.0.0.1,::1"' in text, (
        "Dockerfile default for FORWARDED_ALLOW_IPS must stay restricted "
        "to loopback so Uvicorn does not rewrite scope[client] from XFF "
        "for arbitrary peers — that rewrite happens before rate_limit_key."
    )


def test_rate_limit_key_attacker_xff_rotation_is_no_op(make_request):
    """Concrete regression: an attacker rotating ``X-Forwarded-For`` per
    request cannot mint fresh buckets unless they're connecting from a
    trusted hop. Keys for 10 different XFF values from the same direct
    IP all collapse to a single bucket."""
    from memory.api.app import rate_limit_key

    keys = set()
    with patch("memory.common.settings.RATE_LIMIT_TRUSTED_PROXIES", ""):
        for i in range(10):
            request = make_request(
                "203.0.113.7",
                {"X-Forwarded-For": f"10.0.{i}.1"},
            )
            keys.add(rate_limit_key(cast(Any, request)))
    assert keys == {"203.0.113.7"}
