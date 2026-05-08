"""Tests for the streaming-download-with-cap helper."""

from __future__ import annotations

import pathlib
from unittest.mock import patch

import pytest
import requests

from memory.common.downloads import (
    canonicalize_url_for_loop_detection,
    safe_get,
    stream_download_to_bytes,
    stream_download_to_path,
)
from memory.common.ssrf import UnsafeURLError


class _FakeChunkResponse:
    """Lightweight stand-in for a streaming requests response."""

    def __init__(
        self,
        chunks: list[bytes],
        *,
        content_length: int | None = None,
        raises_on_status: Exception | None = None,
        status_code: int = 200,
        location: str | None = None,
    ) -> None:
        self._chunks = chunks
        self.headers: dict[str, str] = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        if location is not None:
            self.headers["Location"] = location
        self._raises = raises_on_status
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def close(self) -> None:
        pass

    def raise_for_status(self) -> None:
        if self._raises is not None:
            raise self._raises

    def iter_content(self, chunk_size: int):
        yield from self._chunks


@pytest.fixture(autouse=True)
def _stub_ssrf_validation():
    """Disable real DNS lookups in unit tests.

    ``safe_get`` calls ``validate_public_url`` which does ``getaddrinfo``;
    we don't want that hitting the network in unit tests. Individual tests
    that want to assert validation behaviour patch the function themselves
    after entering this fixture's stub.
    """
    with patch(
        "memory.common.downloads.validate_public_url", return_value=None
    ) as m:
        yield m


def test_stream_download_to_bytes_returns_body_within_cap():
    """A small response under the cap returns the joined body."""
    fake = _FakeChunkResponse([b"hello, ", b"world!"], content_length=13)
    with patch("memory.common.downloads.requests.get", return_value=fake):
        result = stream_download_to_bytes("http://example.com/f", max_bytes=1024)
    assert result == b"hello, world!"


def test_stream_download_to_bytes_aborts_on_content_length():
    """Server-supplied Content-Length over the cap fast-fails before reading body."""
    fake = _FakeChunkResponse([b"X" * 1024], content_length=10_000_000)
    with patch("memory.common.downloads.requests.get", return_value=fake):
        result = stream_download_to_bytes("http://example.com/f", max_bytes=1024)
    assert result is None


def test_stream_download_to_bytes_aborts_mid_stream_when_cap_exceeded():
    """A server that lies about Content-Length (or omits it) is still capped mid-stream."""
    # 8 chunks of 1024 bytes = 8192 bytes; cap is 4096.
    fake = _FakeChunkResponse(
        [b"X" * 1024] * 8,
        content_length=None,  # omitted — only the streaming loop catches us
    )
    with patch("memory.common.downloads.requests.get", return_value=fake):
        result = stream_download_to_bytes("http://example.com/f", max_bytes=4096)
    assert result is None


def test_stream_download_to_bytes_handles_request_exception():
    """Network errors return None; the caller doesn't have to wrap a try/except."""
    with patch(
        "memory.common.downloads.requests.get",
        side_effect=requests.RequestException("boom"),
    ):
        result = stream_download_to_bytes("http://example.com/f", max_bytes=1024)
    assert result is None


def test_stream_download_to_path_writes_file_and_returns_true(tmp_path: pathlib.Path):
    fake = _FakeChunkResponse([b"the body bytes"], content_length=14)
    dest = tmp_path / "out.bin"
    with patch("memory.common.downloads.requests.get", return_value=fake):
        ok = stream_download_to_path("http://example.com/f", dest, max_bytes=1024)
    assert ok is True
    assert dest.read_bytes() == b"the body bytes"


def test_stream_download_to_path_cleans_up_partial_file_on_cap_exceeded(
    tmp_path: pathlib.Path,
):
    """A truncated download must NOT leave a partial file behind."""
    fake = _FakeChunkResponse([b"X" * 1024] * 4, content_length=None)
    dest = tmp_path / "partial.bin"
    with patch("memory.common.downloads.requests.get", return_value=fake):
        ok = stream_download_to_path("http://example.com/f", dest, max_bytes=2048)
    assert ok is False
    assert not dest.exists(), "partial file must be removed"


def test_stream_download_to_path_creates_parent_directory(tmp_path: pathlib.Path):
    """The helper mkdirs the parent path so callers don't have to."""
    fake = _FakeChunkResponse([b"x"], content_length=1)
    dest = tmp_path / "a" / "b" / "c" / "out.bin"
    with patch("memory.common.downloads.requests.get", return_value=fake):
        ok = stream_download_to_path("http://example.com/f", dest, max_bytes=1024)
    assert ok is True
    assert dest.exists()


def test_stream_download_to_path_handles_request_exception(tmp_path: pathlib.Path):
    """Network failure returns False AND removes any partial file."""
    dest = tmp_path / "failed.bin"
    # Pre-create the file to verify cleanup on failure.
    dest.write_bytes(b"stale content")

    with patch(
        "memory.common.downloads.requests.get",
        side_effect=requests.RequestException("boom"),
    ):
        ok = stream_download_to_path("http://example.com/f", dest, max_bytes=1024)
    assert ok is False
    assert not dest.exists()


@pytest.mark.parametrize("invalid_cl", ["not-a-number", "  "])
def test_stream_download_to_bytes_ignores_unparseable_content_length(invalid_cl):
    """Garbage Content-Length doesn't crash; we just rely on the streaming cap."""
    fake = _FakeChunkResponse([b"ok"])
    fake.headers["Content-Length"] = invalid_cl
    with patch("memory.common.downloads.requests.get", return_value=fake):
        result = stream_download_to_bytes("http://example.com/f", max_bytes=1024)
    assert result == b"ok"


# --- safe_get redirect handling -----------------------------------------
# The whole point of safe_get is that ``requests``'s default redirect
# follower runs inside one ``urlopen`` and never re-checks the Location
# target. Without these tests a future contributor could "simplify"
# safe_get back to ``allow_redirects=True`` and reintroduce the SSRF.


def test_safe_get_returns_response_when_no_redirect():
    """Non-3xx response is returned directly."""
    fake = _FakeChunkResponse([b"ok"], status_code=200)
    with patch("memory.common.downloads.requests.get", return_value=fake):
        response = safe_get("http://example.com/f")
    assert response is fake


def test_safe_get_follows_safe_redirect_chain():
    """Multiple 302s to public URLs are followed; final response returned."""
    hop1 = _FakeChunkResponse([], status_code=302, location="http://a.example/2")
    hop2 = _FakeChunkResponse([], status_code=302, location="http://b.example/3")
    final = _FakeChunkResponse([b"final"], status_code=200)

    with patch(
        "memory.common.downloads.requests.get",
        side_effect=[hop1, hop2, final],
    ):
        response = safe_get("http://start.example/1")
    assert response is final


def test_safe_get_blocks_redirect_to_private_ip():
    """A 302 → http://169.254.169.254 (AWS IMDS) must raise, not be fetched.

    This is the redirect-bypass SSRF: initial URL passes validation
    (public IP), then the attacker-controlled response 302s to cloud
    metadata. The fix is to revalidate every Location target.
    """
    hop = _FakeChunkResponse(
        [], status_code=302, location="http://169.254.169.254/latest/meta-data/"
    )
    # Real validate_public_url must run for the redirect target.
    with patch("memory.common.downloads.validate_public_url") as mock_validate:
        mock_validate.side_effect = [None, UnsafeURLError("non-public IP")]
        with patch(
            "memory.common.downloads.requests.get", return_value=hop
        ) as mock_get:
            with pytest.raises(UnsafeURLError):
                safe_get("http://attacker.example/")
    # First call (initial URL) and second call (redirect target) both validated.
    assert mock_validate.call_count == 2
    # We must NOT have issued the second GET to the IMDS endpoint.
    assert mock_get.call_count == 1


def test_safe_get_initial_url_validation_can_be_skipped():
    """validate_url=False skips initial check (caller already validated)
    but still validates redirect targets."""
    hop = _FakeChunkResponse([], status_code=301, location="http://b.example/")
    final = _FakeChunkResponse([b"x"], status_code=200)
    with patch("memory.common.downloads.validate_public_url") as mock_validate:
        with patch(
            "memory.common.downloads.requests.get",
            side_effect=[hop, final],
        ):
            safe_get("http://start.example/", validate_url=False)
    # Only the redirect target was validated, not the initial URL.
    assert mock_validate.call_count == 1


def test_safe_get_caps_redirect_count():
    """Beyond ``max_redirects`` the chain is broken, not followed forever."""
    # Six hops → cap of 5 should reject.
    hops = [
        _FakeChunkResponse([], status_code=302, location=f"http://h{i}.example/")
        for i in range(7)
    ]
    with patch(
        "memory.common.downloads.requests.get", side_effect=hops
    ):
        with pytest.raises(UnsafeURLError, match="Too many redirects"):
            safe_get("http://start.example/", max_redirects=5)


def test_safe_get_detects_redirect_loop():
    """A 302 back to the start URL must be detected as a loop."""
    loop = _FakeChunkResponse([], status_code=302, location="http://start.example/")
    # requests.get gets called repeatedly with the same URL; loop detection
    # fires once we revisit a URL we've already seen.
    with patch(
        "memory.common.downloads.requests.get", return_value=loop
    ):
        with pytest.raises(UnsafeURLError, match="loop"):
            safe_get("http://start.example/")


def test_safe_get_does_not_follow_when_follow_redirects_false():
    """With follow_redirects=False the 3xx is returned to the caller."""
    redirect = _FakeChunkResponse(
        [], status_code=302, location="http://other.example/"
    )
    with patch("memory.common.downloads.requests.get", return_value=redirect):
        response = safe_get("http://start.example/", follow_redirects=False)
    assert response is redirect
    assert response.status_code == 302


def test_safe_get_returns_3xx_without_location_header():
    """3xx without Location is unusual but not a redirect to follow."""
    weird = _FakeChunkResponse([b"body"], status_code=304)  # no Location
    with patch("memory.common.downloads.requests.get", return_value=weird):
        response = safe_get("http://start.example/")
    assert response is weird


def test_safe_get_resolves_relative_redirect():
    """Servers commonly return ``Location: /path`` — must be resolved
    against the current URL before validation."""
    relative_hop = _FakeChunkResponse(
        [], status_code=302, location="/internal-only"
    )
    final = _FakeChunkResponse([b"ok"], status_code=200)
    captured: list[str] = []

    def fake_validate(url: str) -> None:
        captured.append(url)

    with patch(
        "memory.common.downloads.validate_public_url", side_effect=fake_validate
    ):
        with patch(
            "memory.common.downloads.requests.get",
            side_effect=[relative_hop, final],
        ):
            safe_get("http://start.example/page")

    # The relative redirect resolved to start.example/internal-only.
    assert captured == [
        "http://start.example/page",
        "http://start.example/internal-only",
    ]


def test_stream_download_to_bytes_blocks_unsafe_redirect():
    """The redirect SSRF defence is plumbed through to the streaming helper."""
    redirect = _FakeChunkResponse(
        [], status_code=302, location="http://10.0.0.5/secret"
    )
    with patch("memory.common.downloads.validate_public_url") as mock_validate:
        mock_validate.side_effect = [None, UnsafeURLError("10.0.0.5 is private")]
        with patch(
            "memory.common.downloads.requests.get", return_value=redirect
        ):
            result = stream_download_to_bytes(
                "http://attacker.example/", max_bytes=1024
            )
    # SSRF rejection on redirect → None, just like other download failures.
    assert result is None


def test_stream_download_to_path_blocks_unsafe_redirect(tmp_path: pathlib.Path):
    redirect = _FakeChunkResponse(
        [], status_code=302, location="http://192.168.1.1/admin"
    )
    dest = tmp_path / "out.bin"
    with patch("memory.common.downloads.validate_public_url") as mock_validate:
        mock_validate.side_effect = [None, UnsafeURLError("192.168.1.1 is private")]
        with patch(
            "memory.common.downloads.requests.get", return_value=redirect
        ):
            ok = stream_download_to_path(
                "http://attacker.example/", dest, max_bytes=1024
            )
    assert ok is False
    assert not dest.exists()


# --- safe_get cross-host header stripping --------------------------------
# Mirrors requests.Session.rebuild_auth: any caller that passes
# per-host credentials in headers (Authorization, Proxy-Authorization,
# Cookie) must not leak them to a second host the attacker chooses via
# the Location of a 302 from a public URL.


def test_safe_get_strips_authorization_on_cross_host_redirect():
    """Authorization must not leak to a different host on a 302."""
    hop = _FakeChunkResponse(
        [], status_code=302, location="http://attacker.example/2"
    )
    final = _FakeChunkResponse([b"x"], status_code=200)

    captured: list[dict] = []

    def fake_get(url, **kwargs):
        captured.append({"url": url, "headers": kwargs.get("headers")})
        return [hop, final][len(captured) - 1]

    with patch("memory.common.downloads.requests.get", side_effect=fake_get):
        safe_get(
            "http://api.example/me",
            headers={"Authorization": "Bearer s3cret", "User-Agent": "ua"},
        )

    # First hop sees the Authorization header (same host).
    assert captured[0]["headers"] == {
        "Authorization": "Bearer s3cret",
        "User-Agent": "ua",
    }
    # Second hop must NOT see Authorization (cross-host); benign headers stay.
    assert "Authorization" not in (captured[1]["headers"] or {})
    assert captured[1]["headers"] == {"User-Agent": "ua"}


@pytest.mark.parametrize(
    "header_name",
    [
        "Authorization",
        "authorization",
        "AUTHORIZATION",
        "Proxy-Authorization",
        "proxy-authorization",
        "Cookie",
        "cookie",
        "COOKIE",
    ],
)
def test_safe_get_strip_is_case_insensitive(header_name: str):
    """Header-name matching for stripping is case-insensitive."""
    hop = _FakeChunkResponse(
        [], status_code=302, location="http://attacker.example/2"
    )
    final = _FakeChunkResponse([b"x"], status_code=200)

    captured: list[dict | None] = []

    def fake_get(url, **kwargs):
        captured.append(kwargs.get("headers"))
        return [hop, final][len(captured) - 1]

    with patch("memory.common.downloads.requests.get", side_effect=fake_get):
        safe_get(
            "http://api.example/me",
            headers={header_name: "secret-value", "Accept": "*/*"},
        )

    # First request: header present.
    assert header_name in (captured[0] or {})
    # Second request: header gone irrespective of casing; "Accept" survives.
    second = captured[1] or {}
    assert all(k.lower() != header_name.lower() for k in second)
    assert second.get("Accept") == "*/*"


def test_safe_get_keeps_headers_on_same_host_redirect():
    """A redirect within the same scheme+host+port keeps every header."""
    hop = _FakeChunkResponse(
        [], status_code=302, location="http://api.example/v2/me"
    )
    final = _FakeChunkResponse([b"x"], status_code=200)
    captured: list[dict | None] = []

    def fake_get(url, **kwargs):
        captured.append(kwargs.get("headers"))
        return [hop, final][len(captured) - 1]

    headers = {"Authorization": "Bearer s3cret", "Cookie": "session=abc"}
    with patch("memory.common.downloads.requests.get", side_effect=fake_get):
        safe_get("http://api.example/me", headers=headers)

    # Both hops see the full header set — same-host redirects don't strip.
    assert captured[0] == headers
    assert captured[1] == headers


def test_safe_get_strips_on_scheme_change_same_host():
    """We err on the strip side for ``http`` → ``https`` on the same host
    (stricter than requests' rebuild_auth, but cheap insurance against
    same-host scheme-flip games)."""
    hop = _FakeChunkResponse(
        [], status_code=302, location="https://api.example/me"
    )
    final = _FakeChunkResponse([b"x"], status_code=200)
    captured: list[dict | None] = []

    def fake_get(url, **kwargs):
        captured.append(kwargs.get("headers"))
        return [hop, final][len(captured) - 1]

    with patch("memory.common.downloads.requests.get", side_effect=fake_get):
        safe_get(
            "http://api.example/me",
            headers={"Authorization": "Bearer s3cret"},
        )
    assert captured[0] == {"Authorization": "Bearer s3cret"}
    assert "Authorization" not in (captured[1] or {})


def test_safe_get_strips_on_port_change_same_host():
    """Port flip on the same host also strips — mirrors scheme-flip rationale."""
    hop = _FakeChunkResponse(
        [], status_code=302, location="http://api.example:8080/me"
    )
    final = _FakeChunkResponse([b"x"], status_code=200)
    captured: list[dict | None] = []

    def fake_get(url, **kwargs):
        captured.append(kwargs.get("headers"))
        return [hop, final][len(captured) - 1]

    with patch("memory.common.downloads.requests.get", side_effect=fake_get):
        safe_get(
            "http://api.example/me",
            headers={"Authorization": "Bearer s3cret"},
        )
    assert "Authorization" not in (captured[1] or {})


def test_safe_get_no_headers_no_change():
    """``headers=None`` must remain a no-op after a cross-host redirect."""
    hop = _FakeChunkResponse(
        [], status_code=302, location="http://other.example/2"
    )
    final = _FakeChunkResponse([b"x"], status_code=200)
    captured: list[dict | None] = []

    def fake_get(url, **kwargs):
        captured.append(kwargs.get("headers"))
        return [hop, final][len(captured) - 1]

    with patch("memory.common.downloads.requests.get", side_effect=fake_get):
        safe_get("http://api.example/me")  # no headers kwarg

    # Neither call gets headers; no headers got materialised by the strip path.
    assert captured == [None, None]


def test_safe_get_strip_persists_across_chained_redirects():
    """Once stripped, the header stays stripped on subsequent same-host hops.

    Threat model: cross-host redirect to attacker.example, then attacker
    bounces back to api.example/secondary — header must not reappear.
    """
    hop1 = _FakeChunkResponse(
        [], status_code=302, location="http://attacker.example/middle"
    )
    hop2 = _FakeChunkResponse(
        [], status_code=302, location="http://attacker.example/inner"
    )
    final = _FakeChunkResponse([b"x"], status_code=200)
    captured: list[dict | None] = []

    def fake_get(url, **kwargs):
        captured.append(kwargs.get("headers"))
        return [hop1, hop2, final][len(captured) - 1]

    with patch("memory.common.downloads.requests.get", side_effect=fake_get):
        safe_get(
            "http://api.example/me",
            headers={"Authorization": "Bearer s3cret"},
        )

    assert captured[0] == {"Authorization": "Bearer s3cret"}
    # Second and third hops both lack the header — strip must persist.
    assert "Authorization" not in (captured[1] or {})
    assert "Authorization" not in (captured[2] or {})


# --- canonicalize_url_for_loop_detection ---------------------------------
#
# Pin the canonicalization invariants directly. The redirect loop check
# uses these to catch chains that escape naive set-membership equality
# (case differences, fragment-only diffs, query-param ordering).


@pytest.mark.parametrize(
    "url_a,url_b",
    [
        # Scheme + host case — case-insensitive equality
        ("HTTPS://Host.Example/x", "https://host.example/x"),
        ("Http://Example.com/", "http://example.com/"),
        # Mixed case in just one component
        ("https://EXAMPLE.com/x", "https://example.com/x"),
        ("HTTPS://example.com/x", "https://example.com/x"),
        # Fragment is never sent on the wire — same target
        ("https://example.com/x#anchor", "https://example.com/x"),
        ("https://example.com/x#a", "https://example.com/x#b"),
        # Query parameter ordering — same effective query
        ("https://example.com/?a=1&b=2", "https://example.com/?b=2&a=1"),
        ("https://example.com/?b=2&a=1&c=3", "https://example.com/?a=1&b=2&c=3"),
        # All three together
        (
            "HTTPS://Example.com/p?b=2&a=1#anchor",
            "https://example.com/p?a=1&b=2",
        ),
    ],
)
def test_canonicalize_url_for_loop_detection_collapses_equivalent_forms(
    url_a, url_b
):
    """Two URLs that dial the same wire-level target must canonicalize
    to the same string. This is the structural invariant the redirect-
    loop check relies on."""
    assert canonicalize_url_for_loop_detection(
        url_a
    ) == canonicalize_url_for_loop_detection(url_b)


@pytest.mark.parametrize(
    "url_a,url_b",
    [
        # Trailing slash IS significant — different resources on a strict
        # server. Must NOT collapse (would create false-positive loops).
        ("https://example.com/foo", "https://example.com/foo/"),
        # Different paths
        ("https://example.com/a", "https://example.com/b"),
        # Different hosts
        ("https://a.example.com/", "https://b.example.com/"),
        # Different schemes (http vs https — same host, different
        # security contexts)
        ("http://example.com/x", "https://example.com/x"),
        # Different query parameter values
        ("https://example.com/?a=1", "https://example.com/?a=2"),
        # Different ports
        ("https://example.com:443/", "https://example.com:8443/"),
        # Percent-encoded vs literal — intentionally NOT collapsed
        # (some servers route differently)
        ("https://example.com/a%2Fb", "https://example.com/a/b"),
    ],
)
def test_canonicalize_url_for_loop_detection_keeps_distinct_targets_distinct(
    url_a, url_b
):
    """Forms that dial different wire-level targets must remain distinct.

    Over-collapsing would create false-positive loop detections that
    refuse legitimate redirect chains.
    """
    assert canonicalize_url_for_loop_detection(
        url_a
    ) != canonicalize_url_for_loop_detection(url_b)


def test_canonicalize_url_for_loop_detection_preserves_blank_query_value():
    """``?x=`` (empty value) should round-trip — that's a syntactically
    valid empty parameter that some servers care about. Pinning this so
    a future ``parse_qsl(...)`` simplification doesn't drop the key."""
    canonical = canonicalize_url_for_loop_detection(
        "https://example.com/?x=&y=2"
    )
    assert "x=" in canonical
    assert "y=2" in canonical


# --- safe_get loop detection on canonicalization-equivalent URLs --------


def test_safe_get_detects_loop_across_case_differences():
    """A 302 chain that flips host case must still be detected as a loop.

    Without canonicalization, ``http://a.example/`` and
    ``http://A.Example/`` are textually distinct and the visited-set
    check would let the chain run to ``max_redirects``. With
    canonicalization, the loop fires immediately.
    """
    # Two hops that round-trip via case flip on the same host.
    hop1 = _FakeChunkResponse([], status_code=302, location="http://A.Example/")
    hop2 = _FakeChunkResponse([], status_code=302, location="http://a.example/")
    with patch(
        "memory.common.downloads.requests.get", side_effect=[hop1, hop2]
    ):
        with pytest.raises(UnsafeURLError, match="loop"):
            safe_get("http://a.example/")


def test_safe_get_detects_loop_across_fragment_differences():
    """``#a`` vs ``#b`` are dialled identically; loop check must fire."""
    hop1 = _FakeChunkResponse(
        [], status_code=302, location="http://start.example/#frag"
    )
    with patch(
        "memory.common.downloads.requests.get", return_value=hop1
    ):
        with pytest.raises(UnsafeURLError, match="loop"):
            safe_get("http://start.example/")


def test_safe_get_detects_loop_across_query_param_reorder():
    """``?a=1&b=2`` and ``?b=2&a=1`` dial the same target; loop check
    must fire even when the redirect Location reorders the params."""
    hop = _FakeChunkResponse(
        [], status_code=302, location="http://start.example/?b=2&a=1"
    )
    with patch(
        "memory.common.downloads.requests.get", return_value=hop
    ):
        with pytest.raises(UnsafeURLError, match="loop"):
            safe_get("http://start.example/?a=1&b=2")


