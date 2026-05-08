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
"""

import logging
import pathlib
from typing import Mapping

import requests

logger = logging.getLogger(__name__)

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


def stream_download_to_bytes(
    url: str,
    max_bytes: int,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = 30.0,
    follow_redirects: bool = True,
) -> bytes | None:
    """GET ``url`` and return the body, or None if it exceeds ``max_bytes``.

    Streams the response so peak RAM use is at most ``max_bytes`` plus
    one chunk. Pre-checks ``Content-Length`` so an obviously-too-big file
    is rejected before any body bytes are read.
    """
    try:
        with requests.get(
            url,
            timeout=timeout,
            headers=dict(headers) if headers else None,
            stream=True,
            allow_redirects=follow_redirects,
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
) -> bool:
    """Stream ``url`` directly to ``destination``. Returns True on success.

    Cleans up the partial file if the size cap is exceeded mid-stream so
    we never leave an incomplete file behind. The destination's parent
    directory is created if it doesn't exist.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with requests.get(
            url,
            timeout=timeout,
            headers=dict(headers) if headers else None,
            stream=True,
            allow_redirects=follow_redirects,
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
