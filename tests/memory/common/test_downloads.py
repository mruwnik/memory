"""Tests for the streaming-download-with-cap helper."""

from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch

import pytest
import requests

from memory.common.downloads import (
    stream_download_to_bytes,
    stream_download_to_path,
)


class _FakeChunkResponse:
    """Lightweight stand-in for a streaming requests/httpx response."""

    def __init__(
        self,
        chunks: list[bytes],
        *,
        content_length: int | None = None,
        raises_on_status: Exception | None = None,
    ) -> None:
        self._chunks = chunks
        self.headers: dict[str, str] = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        self._raises = raises_on_status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self) -> None:
        if self._raises is not None:
            raise self._raises

    def iter_content(self, chunk_size: int):
        yield from self._chunks

    # httpx-style aliases used by the httpx code path
    def iter_bytes(self, chunk_size: int):
        yield from self._chunks


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


def test_stream_download_to_path_via_httpx(tmp_path: pathlib.Path):
    """The httpx code path exists for callers that need its specific stream API
    (Slack-file ingest passes auth headers; httpx's stream context manager is
    what download_slack_file used pre-extraction)."""
    fake = _FakeChunkResponse([b"hello"], content_length=5)

    fake_client = MagicMock()
    fake_client.__enter__ = lambda self: fake_client
    fake_client.__exit__ = lambda *args: False
    fake_client.stream.return_value = fake

    dest = tmp_path / "httpx_out.bin"
    with patch("memory.common.downloads.httpx.Client", return_value=fake_client):
        ok = stream_download_to_path(
            "http://example.com/f", dest, max_bytes=1024, use_httpx=True
        )
    assert ok is True
    assert dest.read_bytes() == b"hello"
