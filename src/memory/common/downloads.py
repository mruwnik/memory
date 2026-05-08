"""Streaming HTTP download with a hard size cap.

Centralises the "fetch a URL into RAM or disk, but never buffer more than
``max_bytes``" pattern used by image-cache (parsers/html.py),
Discord-attachment ingest (workers/tasks/discord.py), and Slack-file
ingest (workers/tasks/slack.py). Pre-extraction the three sites had
slightly different shapes — Discord even used MD5 for filenames while
the others used SHA-256 — and the Slack version had no cap at all,
buffered the entire 1 GB-max body in RAM, and held a second copy in
``response.content``.

Two entry points:

- :func:`stream_download_to_bytes` — return the body as bytes (suitable
  for in-memory consumers like PIL).
- :func:`stream_download_to_path` — write directly to a file on disk,
  cleaning up the partial file if the cap is exceeded.

Both return ``None`` on any failure (size cap, HTTP error, network
error). The reason is logged at WARNING.

Redirect handling: ``requests``'s default ``allow_redirects=True`` follows
redirects inside a single ``urlopen()`` pass with no intermediate hook,
so an attacker who controls the response of a public URL can ``302`` to
``http://169.254.169.254/`` (cloud IMDS) or to internal docker-network
services. :func:`safe_get` disables native redirect following and
re-validates each redirect target via :func:`memory.common.ssrf.validate_public_url`
before issuing the next hop. ``stream_download_*`` use it transparently.
"""

import logging
import pathlib
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests

from memory.common.ssrf import UnsafeURLError, validate_public_url

logger = logging.getLogger(__name__)

# HTTP status codes that indicate a redirect with a Location header. We
# follow these manually so we can re-run SSRF validation on each hop.
_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})

# Cap manual redirects to match requests' default. Five is enough for
# legitimate redirect chains (e.g. http→https→canonical) and short
# enough to bound work.
DEFAULT_MAX_REDIRECTS = 5

# Default chunk size for streaming. 64 KiB is a reasonable trade-off
# between syscall overhead and burst RAM. 8 KiB (the previous helpers'
# default) is fine too; bigger chunks just mean fewer syscalls.
_CHUNK_SIZE = 64 * 1024

# Header names that must be stripped on cross-host redirects to mirror
# the credential-leak defence built into ``requests.Session.rebuild_auth``
# (and also drop ``Cookie``, which the bare ``requests.get`` path does
# not auto-protect because the cookie jar lives on ``Session``). Stored
# lowercase for case-insensitive matching.
_SENSITIVE_REDIRECT_HEADERS = frozenset(
    {"authorization", "proxy-authorization", "cookie"}
)


def _content_length_exceeds_cap(
    headers: Mapping[str, str], max_bytes: int, url: str
) -> bool:
    """Return True if the server-supplied Content-Length exceeds ``max_bytes``.

    Returning True is a fast-fail before we read any body bytes. Returning
    False does NOT mean the body is small — many servers omit
    Content-Length entirely or lie about it. The streaming loop is the
    real enforcement.
    """
    cl = headers.get("Content-Length") or headers.get("content-length")
    if cl is None:
        return False
    try:
        size = int(cl)
    except ValueError:
        return False
    if size > max_bytes:
        logger.warning(
            "Download from %s aborted: Content-Length %d exceeds cap %d",
            url,
            size,
            max_bytes,
        )
        return True
    return False


def _is_cross_host_redirect(old_url: str, new_url: str) -> bool:
    """Return True if a hop should be treated as cross-origin for header purposes.

    Comparison is on ``(scheme, hostname, port)`` — we strip on any of
    the three changing. This is stricter than ``requests``'s
    ``Session.should_strip_auth`` (which keeps headers across the common
    ``http`` → ``https`` upgrade on default ports) but the conservative
    side is the safe side: we'd rather force a caller to re-supply a
    header than silently leak it through a same-host scheme/port flip
    that an attacker controls. Hostname comparison is case-insensitive.
    """
    old = urlparse(old_url)
    new = urlparse(new_url)
    old_host = (old.hostname or "").lower()
    new_host = (new.hostname or "").lower()
    return (
        old.scheme != new.scheme
        or old_host != new_host
        or old.port != new.port
    )


def canonicalize_url_for_loop_detection(url: str) -> str:
    """Return a canonical form of ``url`` for redirect-loop equality checks.

    Two URLs that the wire-level dial would treat as the same target should
    compare equal here, even if the textual forms differ. This closes
    classes of "redirect chain that escapes the visited-set membership
    check but is functionally a loop":

    * **Scheme / host case** — ``HTTPS://Host.Example/x`` and
      ``https://host.example/x`` both end up at the same host. Lowercased.
    * **Fragments** — ``#anchor`` is never sent on the wire, so two URLs
      that differ only in fragment dial the same target. Stripped.
    * **Query-parameter ordering** — ``?a=1&b=2`` and ``?b=2&a=1`` are
      semantically equivalent on most servers and the standard library's
      own ``parse_qsl``/``urlencode`` round-trip. Sorted by ``(key, value)``
      so equivalent orderings collapse.

    NOT collapsed:
    * Trailing-slash differences — ``/foo`` vs ``/foo/`` route to
      different resources on a strict server. Treating them as equal
      would create false-positive loop detections. The
      ``DEFAULT_MAX_REDIRECTS`` cap is the real defence; loop detection
      is a courtesy that kicks in earlier when the chain genuinely loops.
    * Percent-encoding — ``%2f`` vs ``/`` is intentionally significant on
      some servers; we don't second-guess.

    The function is exported so tests can pin the canonicalization
    invariants directly.
    """
    parsed = urlparse(url)
    # Sort (key, value) pairs so the order doesn't matter; keep_blank_values
    # so ``?x=`` round-trips faithfully.
    sorted_query = urlencode(
        sorted(parse_qsl(parsed.query, keep_blank_values=True))
    )
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path,
            parsed.params,
            sorted_query,
            "",  # drop fragment
        )
    )


def _strip_sensitive_headers(
    headers: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Return a copy of ``headers`` with auth-like names removed.

    Match is case-insensitive against
    :data:`_SENSITIVE_REDIRECT_HEADERS`. Returns ``None`` unchanged so
    callers can keep the ``headers=None`` no-op shape.
    """
    if not headers:
        return headers if headers is None else dict(headers)
    return {
        k: v
        for k, v in headers.items()
        if k.lower() not in _SENSITIVE_REDIRECT_HEADERS
    }


def safe_get(
    url: str,
    *,
    validate_url: bool = True,
    follow_redirects: bool = True,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    **kwargs: Any,
) -> requests.Response:
    """GET ``url`` with redirect-aware SSRF validation.

    Disables ``requests``'s native redirect handling and follows redirects
    manually so each ``Location`` target is re-validated via
    :func:`validate_public_url` (DNS-resolution + private-IP check). This
    closes the SSRF redirect-bypass attack where an attacker hosts a
    public URL that 302s to ``http://169.254.169.254/`` (cloud IMDS) or
    to docker-network services.

    The initial URL is validated unless ``validate_url=False`` (callers
    that already validated may opt out to avoid double DNS lookup; intermediate
    redirect targets are still validated). The kwargs are passed through
    to ``requests.get`` (``stream``, ``timeout``, ``headers``, ...);
    ``allow_redirects`` is forced off internally.

    On a cross-host redirect (different scheme, hostname, or port), the
    ``Authorization``, ``Proxy-Authorization``, and ``Cookie`` headers
    are stripped before the next request — mirroring the
    ``requests.Session.rebuild_auth`` credential-leak defence which the
    bare ``requests.get`` path doesn't get for free. This protects callers
    that pass per-host bearer tokens against an attacker who controls
    the ``Location`` of a public 302 hop.

    DNS-rebinding TOCTOU residual risk: ``validate_public_url`` does its
    own ``getaddrinfo`` and then ``requests.get`` does another at dial
    time. An attacker who controls authoritative DNS for a public
    domain can return a public IP for the first lookup and a private
    IP for the second. The single-resolve attacker is closed; the
    DNS-rebinding attacker is not. See ``memory.common.ssrf`` module
    docstring + follow-up task ``5a471003`` for the proper fix (pin
    the validated IP across validate→fetch via a custom HTTPAdapter).

    Raises:
        UnsafeURLError: initial or any redirect target failed SSRF policy,
            or redirect loop / hop-count cap hit.
        requests.RequestException: connection / timeout / decoding error
            (caller's responsibility — kept consistent with ``requests.get``).
    """
    if validate_url:
        validate_public_url(url)

    # Force-disable native redirects regardless of caller-supplied kwargs;
    # safety of redirect handling is the whole point of this wrapper.
    kwargs["allow_redirects"] = False

    current_url = url
    # ``visited`` stores canonicalized forms (case-folded scheme+host,
    # fragment dropped, query params sorted) so a redirect chain that
    # only flips case or reorders ``?a=1&b=2`` to ``?b=2&a=1`` is still
    # caught as a loop. See :func:`canonicalize_url_for_loop_detection`.
    visited: set[str] = {canonicalize_url_for_loop_detection(url)}

    for _ in range(max_redirects + 1):
        response = requests.get(current_url, **kwargs)

        if not follow_redirects or response.status_code not in _REDIRECT_STATUS_CODES:
            return response

        location = response.headers.get("Location")
        if not location:
            return response

        # Drain/close the redirect response before issuing the next request
        # so the connection can be returned to the pool.
        response.close()

        next_url = urljoin(current_url, location)
        next_canonical = canonicalize_url_for_loop_detection(next_url)
        if next_canonical in visited:
            raise UnsafeURLError(f"Redirect loop detected at {next_url}")
        visited.add(next_canonical)

        # Always validate redirect targets — that is the entire purpose.
        # The attacker controls the Location header even when the initial
        # URL was clean.
        validate_public_url(next_url)

        # Drop credential-shaped headers before the cross-host next-hop.
        # We do this AFTER URL validation so a same-host 302 (the common
        # case) keeps every header untouched.
        if _is_cross_host_redirect(current_url, next_url):
            stripped = _strip_sensitive_headers(kwargs.get("headers"))
            if stripped != kwargs.get("headers"):
                logger.info(
                    "Stripping sensitive headers on cross-host redirect: %s -> %s",
                    current_url,
                    next_url,
                )
                kwargs["headers"] = stripped

        current_url = next_url

    raise UnsafeURLError(
        f"Too many redirects (>{max_redirects}) starting from {url}"
    )


def stream_download_to_bytes(
    url: str,
    max_bytes: int,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = 30.0,
    follow_redirects: bool = True,
    validate_url: bool = True,
) -> bytes | None:
    """GET ``url`` and return the body, or None if it exceeds ``max_bytes``.

    Streams the response so peak RAM use is at most ``max_bytes`` plus
    one chunk. Pre-checks ``Content-Length`` so an obviously-too-big file
    is rejected before any body bytes are read. Redirects are followed
    manually with SSRF validation per hop — see :func:`safe_get`.
    """
    try:
        with safe_get(
            url,
            validate_url=validate_url,
            follow_redirects=follow_redirects,
            timeout=timeout,
            headers=dict(headers) if headers else None,
            stream=True,
        ) as response:
            response.raise_for_status()

            if _content_length_exceeds_cap(response.headers, max_bytes, url):
                return None

            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    logger.warning(
                        "Download from %s aborted: streamed bytes exceeded cap %d",
                        url,
                        max_bytes,
                    )
                    return None
                chunks.append(chunk)
            return b"".join(chunks)
    except UnsafeURLError as e:
        logger.warning("Refusing to download %s: %s", url, e)
        return None
    except requests.RequestException as e:
        logger.warning("Failed to download %s: %s", url, e)
        return None


def stream_download_to_path(
    url: str,
    destination: pathlib.Path,
    max_bytes: int,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = 30.0,
    follow_redirects: bool = True,
    validate_url: bool = True,
) -> bool:
    """Stream ``url`` directly to ``destination``. Returns True on success.

    Cleans up the partial file if the size cap is exceeded mid-stream so
    we never leave an incomplete file behind. The destination's parent
    directory is created if it doesn't exist. Redirects are followed
    manually with SSRF validation per hop — see :func:`safe_get`.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with safe_get(
            url,
            validate_url=validate_url,
            follow_redirects=follow_redirects,
            timeout=timeout,
            headers=dict(headers) if headers else None,
            stream=True,
        ) as response:
            response.raise_for_status()

            if _content_length_exceeds_cap(response.headers, max_bytes, url):
                return False

            return _write_streaming(
                destination,
                response.iter_content(chunk_size=_CHUNK_SIZE),
                max_bytes,
                url,
            )
    except UnsafeURLError as e:
        logger.warning("Refusing to download %s: %s", url, e)
        destination.unlink(missing_ok=True)
        return False
    except requests.RequestException as e:
        logger.warning("Failed to download %s: %s", url, e)
        destination.unlink(missing_ok=True)
        return False


def _write_streaming(
    destination: pathlib.Path,
    chunks,
    max_bytes: int,
    url: str,
) -> bool:
    """Write a chunk iterator to ``destination`` with a hard cap.

    Cleans up the partial file if the cap is exceeded so callers always
    see either a complete file (returns True) or no file at all (returns
    False). Internal helper — not part of the public API.
    """
    downloaded = 0
    with destination.open("wb") as f:
        for chunk in chunks:
            if not chunk:
                continue
            downloaded += len(chunk)
            if downloaded > max_bytes:
                logger.warning(
                    "Download from %s aborted: streamed bytes exceeded cap %d",
                    url,
                    max_bytes,
                )
                f.close()
                destination.unlink(missing_ok=True)
                return False
            f.write(chunk)
    return True
