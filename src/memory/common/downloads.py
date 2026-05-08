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
from urllib.parse import urljoin

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
    visited: set[str] = {url}

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
        if next_url in visited:
            raise UnsafeURLError(f"Redirect loop detected at {next_url}")
        visited.add(next_url)

        # Always validate redirect targets — that is the entire purpose.
        # The attacker controls the Location header even when the initial
        # URL was clean.
        validate_public_url(next_url)
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
