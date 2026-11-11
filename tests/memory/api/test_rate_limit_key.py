"""Tests for the API rate-limit key function (``app.rate_limit_key``).

The threat model: a key that honors ``X-Forwarded-For`` from any peer
(Uvicorn with ``--proxy-headers`` and ``--forwarded-allow-ips=*``, the
Docker default) lets every remote attacker rotate ``X-Forwarded-For`` per
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
    from memory.common.rate_limit import rate_limit_key

    request = make_request("203.0.113.7", {"X-Forwarded-For": "9.9.9.9"})
    with patch("memory.common.settings.RATE_LIMIT_TRUSTED_PROXIES", ""):
        key = rate_limit_key(cast(Any, request))
    assert key == "203.0.113.7"


@pytest.mark.parametrize(
    "peer, trusted",
    [
        ("1.2.3.4", "10.0.0.5"),
        ("1.2.3.4", "10.0.0.0/8"),
        ("172.31.0.1", "172.30.0.0/16"),
        ("testclient", "10.0.0.0/8"),
        ("1.2.3.4", "not-an-ip,10.0.0.5"),
    ],
)
def test_rate_limit_key_ignores_xff_from_untrusted_hop(make_request, peer, trusted):
    """An attacker connecting directly (not via the trusted proxy) cannot
    use ``X-Forwarded-For`` to rotate buckets."""
    from memory.common.rate_limit import rate_limit_key

    request = make_request(peer, {"X-Forwarded-For": f"{99}.{99}.{99}.{99}"})
    with patch("memory.common.settings.RATE_LIMIT_TRUSTED_PROXIES", trusted):
        key = rate_limit_key(cast(Any, request))
    assert key == peer


@pytest.mark.parametrize(
    "peer, trusted",
    [
        ("10.0.0.5", "10.0.0.5"),
        # docker-proxy forwards host-loopback connections from the bridge gateway
        ("172.30.0.1", "127.0.0.1,::1,172.30.0.0/16"),
        ("::1", "127.0.0.1,::1"),
        ("10.0.0.5", "not-an-ip,10.0.0.0/24"),
    ],
)
def test_rate_limit_key_honors_xff_from_trusted_proxy(make_request, peer, trusted):
    """Behind a real reverse proxy, the immediate hop is the proxy and
    the original client lives in ``X-Forwarded-For``."""
    from memory.common.rate_limit import rate_limit_key

    request = make_request(peer, {"X-Forwarded-For": "203.0.113.7"})
    with patch("memory.common.settings.RATE_LIMIT_TRUSTED_PROXIES", trusted):
        key = rate_limit_key(cast(Any, request))
    assert key == "203.0.113.7"


@pytest.mark.parametrize(
    "xff, trusted, expected",
    [
        # An appending proxy keeps whatever the client sent on the left.
        ("1.1.1.1, 203.0.113.7", "10.0.0.5", "203.0.113.7"),
        ("9.9.9.9, 203.0.113.7, 10.0.0.99", "10.0.0.5,10.0.0.99", "203.0.113.7"),
        ("203.0.113.7, 10.0.0.99", "10.0.0.5", "10.0.0.99"),
        # Every hop trusted: the left-most is the client.
        ("10.0.0.7, 10.0.0.6", "10.0.0.0/24", "10.0.0.7"),
    ],
)
def test_rate_limit_key_takes_right_most_untrusted_hop(make_request, xff, trusted, expected):
    """Only hops trusted proxies appended count; entries left of the first
    untrusted one (from the right) are the client's to choose."""
    from memory.common.rate_limit import rate_limit_key

    request = make_request("10.0.0.5", {"X-Forwarded-For": xff})
    with patch("memory.common.settings.RATE_LIMIT_TRUSTED_PROXIES", trusted):
        key = rate_limit_key(cast(Any, request))
    assert key == expected


def test_rate_limit_key_falls_back_when_xff_empty(make_request):
    """Trusted proxy that didn't set XFF (e.g. a misconfigured one) → use
    the proxy's own IP rather than crashing."""
    from memory.common.rate_limit import rate_limit_key

    request = make_request("10.0.0.5", {})
    with patch(
        "memory.common.settings.RATE_LIMIT_TRUSTED_PROXIES", "10.0.0.5"
    ):
        key = rate_limit_key(cast(Any, request))
    assert key == "10.0.0.5"


def test_rate_limit_key_wildcard_trusts_anyone(make_request):
    """Operators who explicitly opt out of the spoofing protection get
    the old behavior — XFF is honored regardless of the immediate hop."""
    from memory.common.rate_limit import rate_limit_key

    request = make_request("1.2.3.4", {"X-Forwarded-For": "203.0.113.7"})
    with patch("memory.common.settings.RATE_LIMIT_TRUSTED_PROXIES", "*"):
        key = rate_limit_key(cast(Any, request))
    assert key == "203.0.113.7"


def test_rate_limit_key_handles_missing_client(make_request):
    """Test client with no ``request.client`` (e.g. ASGI lifespan) doesn't
    crash the key function."""
    from memory.common.rate_limit import rate_limit_key

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
    from memory.common.rate_limit import rate_limit_key

    keys = set()
    with patch("memory.common.settings.RATE_LIMIT_TRUSTED_PROXIES", ""):
        for i in range(10):
            request = make_request(
                "203.0.113.7",
                {"X-Forwarded-For": f"10.0.{i}.1"},
            )
            keys.add(rate_limit_key(cast(Any, request)))
    assert keys == {"203.0.113.7"}


@pytest.mark.parametrize(
    "peer, expected",
    [
        ("203.0.113.7", "203.0.113.7"),
        ("2001:db8:1:2::1", "2001:db8:1:2::/64"),
        ("2001:db8:1:2:ffff:ffff:ffff:ffff", "2001:db8:1:2::/64"),
        ("2001:db8:1:3::1", "2001:db8:1:3::/64"),
        ("::ffff:203.0.113.7", "203.0.113.7"),
        ("64:ff9b::203.0.113.7", "203.0.113.7"),
        ("64:ff9b::198.51.100.9", "198.51.100.9"),
        ("64:ff9b:1:2::1", "64:ff9b:1:2::1"),
        ("64:ff9b:1:2::2", "64:ff9b:1:2::2"),
        # Teredo: server 65.54.227.120, client 192.0.2.45 stored inverted.
        ("2001:0:4136:e378:8000:63bf:3fff:fdd2", "192.0.2.45"),
        ("2002:cb00:7107::1", "203.0.113.7"),
        ("testclient", "testclient"),
    ],
)
def test_rate_limit_key_buckets_ipv6_by_64(make_request, peer, expected):
    from memory.common.rate_limit import rate_limit_key

    with patch("memory.common.settings.RATE_LIMIT_TRUSTED_PROXIES", ""):
        assert rate_limit_key(cast(Any, make_request(peer))) == expected


def test_rate_limit_key_buckets_forwarded_ipv6_by_64(make_request):
    from memory.common.rate_limit import rate_limit_key

    keys = set()
    with patch("memory.common.settings.RATE_LIMIT_TRUSTED_PROXIES", "10.0.0.5"):
        for i in range(10):
            request = make_request("10.0.0.5", {"X-Forwarded-For": f"2001:db8:0:7::{i + 1:x}"})
            keys.add(rate_limit_key(cast(Any, request)))
    assert keys == {"2001:db8:0:7::/64"}


@pytest.mark.parametrize(
    "address, expected",
    [
        ("2001:db8:1:2::1", "2001:db8:1:2::1"),
        ("2001:db8:1:2::2", "2001:db8:1:2::2"),
        ("::ffff:203.0.113.7", "203.0.113.7"),
        ("203.0.113.7", "203.0.113.7"),
        ("testclient", "testclient"),
    ],
)
def test_client_identity_is_the_exact_address(address, expected):
    from memory.common.rate_limit import client_identity

    assert client_identity(address) == expected
