# pyright: reportArgumentType=false
"""Unit tests for the file-upload streaming size cap.

The audit finding (e36412f2): /books/upload, /photos/upload, and
/reports/upload buffered the entire request body via ``await
file.read()`` with no upper bound, allowing an authenticated user to
OOM the API container or fill FILE_STORAGE_DIR with a multi-GB
upload. Mirrors the streaming-cap pattern from
``cloud_claude.transfer_push``.

These tests exercise ``read_upload_with_cap`` directly with a fake
UploadFile so they don't need the full FastAPI/DB stack.
"""

from io import BytesIO
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, UploadFile

from memory.api.content_sources import read_upload_with_cap


def _make_upload(body: bytes) -> UploadFile:
    """Build a real UploadFile around an in-memory body.

    We use the actual UploadFile class so async ``.read(size)``
    semantics match production exactly.
    """
    return UploadFile(filename="test.bin", file=BytesIO(body))


def _make_request_with_content_length(value: str | None) -> MagicMock | None:
    if value is None:
        return None
    request = MagicMock()
    request.headers = {"content-length": value}
    return request


@pytest.mark.asyncio
async def test_read_upload_with_cap_reads_full_body_under_cap():
    body = b"hello world" * 100  # 1100 bytes
    upload = _make_upload(body)
    result = await read_upload_with_cap(upload, cap_bytes=10_000)
    assert result == body


@pytest.mark.asyncio
async def test_read_upload_with_cap_rejects_body_at_exactly_one_byte_over_cap():
    """The cap is strict: ``total > cap`` triggers 413."""
    body = b"x" * 1001
    upload = _make_upload(body)
    with pytest.raises(HTTPException) as exc:
        await read_upload_with_cap(upload, cap_bytes=1000)
    assert exc.value.status_code == 413
    assert "too large" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_read_upload_with_cap_accepts_body_exactly_at_cap():
    body = b"x" * 1000
    upload = _make_upload(body)
    result = await read_upload_with_cap(upload, cap_bytes=1000)
    assert result == body


@pytest.mark.asyncio
async def test_read_upload_with_cap_pre_check_rejects_declared_oversize():
    """When Content-Length is set and exceeds the cap, refuse before reading."""
    upload = _make_upload(b"x" * 100)  # actual body small
    request = _make_request_with_content_length("999999999")
    with pytest.raises(HTTPException) as exc:
        await read_upload_with_cap(
            upload, cap_bytes=1024 * 1024, request=request
        )
    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_read_upload_with_cap_pre_check_ignores_missing_content_length():
    """No declared Content-Length is fine — fall through to streaming check."""
    body = b"x" * 50
    upload = _make_upload(body)
    request = _make_request_with_content_length(None)
    # request=None is the "no request" path
    result = await read_upload_with_cap(upload, cap_bytes=1000, request=request)
    assert result == body


@pytest.mark.asyncio
async def test_read_upload_with_cap_pre_check_ignores_non_numeric_content_length():
    """A garbage Content-Length header doesn't crash the pre-check."""
    body = b"x" * 50
    upload = _make_upload(body)
    request = MagicMock()
    request.headers = {"content-length": "not-a-number"}
    result = await read_upload_with_cap(
        upload, cap_bytes=1000, request=request
    )
    assert result == body


@pytest.mark.asyncio
async def test_read_upload_with_cap_streaming_catches_lying_content_length():
    """A client whose Content-Length lies still gets 413 mid-stream.

    Real attackers can set a small Content-Length and then keep
    streaming bytes (Transfer-Encoding: chunked, or a buggy proxy
    rewriting headers). The streaming cumulative check is the actual
    defense.
    """
    body = b"x" * 5000
    upload = _make_upload(body)
    request = _make_request_with_content_length("100")  # claims 100 bytes
    with pytest.raises(HTTPException) as exc:
        await read_upload_with_cap(
            upload, cap_bytes=2000, request=request
        )
    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_read_upload_with_cap_handles_empty_body():
    upload = _make_upload(b"")
    result = await read_upload_with_cap(upload, cap_bytes=1000)
    assert result == b""


@pytest.mark.asyncio
async def test_read_upload_with_cap_error_mentions_megabytes():
    """The 413 message expresses the cap in MiB, matching transfer_push."""
    upload = _make_upload(b"x" * 5_000_000)
    with pytest.raises(HTTPException) as exc:
        await read_upload_with_cap(upload, cap_bytes=1024 * 1024)  # 1 MiB
    assert "1 MB" in exc.value.detail
