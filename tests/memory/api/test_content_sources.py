"""Tests for content sources API endpoints."""

from io import BytesIO
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from memory.common.db.models import JobType


@pytest.fixture
def mock_dispatch_job():
    """Mock dispatch_job to avoid Celery dependencies."""
    with patch("memory.api.content_sources.dispatch_job") as mock:
        mock_job = type("MockJob", (), {
            "id": 100,
            "status": "pending",
            "celery_task_id": "celery-task-123",
        })()
        mock.return_value = type("DispatchResult", (), {
            "job": mock_job,
            "is_new": True,
            "message": "Job queued",
        })()
        yield mock


def test_upload_book_returns_job_id(client: TestClient, mock_dispatch_job, user, tmp_path):
    """Test book upload returns job_id for tracking."""
    # Create a minimal valid epub-like file
    test_content = b"PK\x03\x04" + b"\x00" * 100  # Minimal ZIP header

    with patch("memory.api.content_sources.settings") as mock_settings:
        mock_settings.EBOOK_STORAGE_DIR = tmp_path
        mock_settings.MAX_BOOK_UPLOAD_BYTES = 100 * 1024 * 1024

        response = client.post(
            "/books/upload",
            files={"file": ("test_book.epub", BytesIO(test_content), "application/epub+zip")},
            data={"title": "Test Book", "author": "Test Author", "tags": "fiction,test"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == 100
    assert data["status"] == "queued"
    assert data["task_id"] == "celery-task-123"
    assert "test_book.epub" in data["message"]

    # Verify dispatch_job was called with correct params
    mock_dispatch_job.assert_called_once()
    call_kwargs = mock_dispatch_job.call_args.kwargs
    assert call_kwargs["job_type"] == JobType.CONTENT_INGEST
    assert call_kwargs["task_kwargs"]["title"] == "Test Book"
    assert call_kwargs["task_kwargs"]["author"] == "Test Author"
    assert call_kwargs["task_kwargs"]["tags"] == ["fiction", "test"]


def test_upload_book_invalid_extension(client: TestClient, user):
    """Test that invalid file types are rejected."""
    response = client.post(
        "/books/upload",
        files={"file": ("test.txt", BytesIO(b"not an ebook"), "text/plain")},
    )

    assert response.status_code == 400
    assert "Invalid file type" in response.json()["detail"]


def test_upload_photo_returns_job_id(client: TestClient, mock_dispatch_job, user, tmp_path):
    """Test photo upload returns job_id for tracking."""
    # Create a minimal valid JPEG header
    jpeg_content = bytes([0xFF, 0xD8, 0xFF, 0xE0]) + b"\x00" * 100

    with patch("memory.api.content_sources.settings") as mock_settings:
        mock_settings.PHOTO_STORAGE_DIR = tmp_path
        mock_settings.MAX_PHOTO_UPLOAD_BYTES = 100 * 1024 * 1024

        response = client.post(
            "/photos/upload",
            files={"file": ("test_photo.jpg", BytesIO(jpeg_content), "image/jpeg")},
            data={"tags": "vacation,beach"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == 100
    assert data["status"] == "queued"
    assert "test_photo.jpg" in data["message"]

    # Verify dispatch_job was called with correct params
    mock_dispatch_job.assert_called_once()
    call_kwargs = mock_dispatch_job.call_args.kwargs
    assert call_kwargs["job_type"] == JobType.CONTENT_INGEST
    assert call_kwargs["task_kwargs"]["tags"] == ["vacation", "beach"]


def test_upload_photo_invalid_extension(client: TestClient, user):
    """Test that invalid image types are rejected."""
    response = client.post(
        "/photos/upload",
        files={"file": ("test.pdf", BytesIO(b"not an image"), "application/pdf")},
    )

    assert response.status_code == 400
    assert "Invalid file type" in response.json()["detail"]


# ====== Reports upload — XSS/allow_scripts gating ======


def test_upload_report_admin_can_set_allow_scripts(
    client: TestClient, mock_dispatch_job, user, tmp_path
):
    """Admin (default test client has scopes=['*']) may upload with allow_scripts=true."""
    html = b"<html><body><script>console.log(1)</script></body></html>"

    response = client.post(
        "/reports/upload",
        files={"file": ("evil.html", BytesIO(html), "text/html")},
        data={"allow_scripts": "true"},
    )

    assert response.status_code == 200
    call_kwargs = mock_dispatch_job.call_args.kwargs
    assert call_kwargs["task_kwargs"]["allow_scripts"] is True


def test_upload_report_non_admin_rejected_with_allow_scripts(
    regular_client: TestClient, mock_dispatch_job, user, tmp_path
):
    """Non-admin uploading allow_scripts=true must get 403, not silent acceptance."""
    html = b"<html><body><script>fetch('/users/me/api-keys')</script></body></html>"

    response = regular_client.post(
        "/reports/upload",
        files={"file": ("evil.html", BytesIO(html), "text/html")},
        data={"allow_scripts": "true"},
    )

    assert response.status_code == 403
    assert "admin" in response.json()["detail"].lower()
    # Critical: dispatch_job must NOT have been called with allow_scripts=True
    mock_dispatch_job.assert_not_called()


def test_upload_report_non_admin_default_allow_scripts_false(
    regular_client: TestClient, mock_dispatch_job, user, tmp_path
):
    """Non-admin upload without allow_scripts works and persists allow_scripts=False."""
    html = b"<html><body><h1>plain</h1></body></html>"

    response = regular_client.post(
        "/reports/upload",
        files={"file": ("plain.html", BytesIO(html), "text/html")},
    )

    assert response.status_code == 200
    call_kwargs = mock_dispatch_job.call_args.kwargs
    assert call_kwargs["task_kwargs"]["allow_scripts"] is False


def test_upload_report_non_admin_allowed_connect_urls_dropped(
    regular_client: TestClient, mock_dispatch_job, user, tmp_path
):
    """Non-admin's allowed_connect_urls is dropped (defense in depth)."""
    html = b"<html><body>x</body></html>"

    response = regular_client.post(
        "/reports/upload",
        files={"file": ("plain.html", BytesIO(html), "text/html")},
        data={"allowed_connect_urls": "https://attacker.example.com,https://evil.test"},
    )

    assert response.status_code == 200
    call_kwargs = mock_dispatch_job.call_args.kwargs
    # allowed_connect_urls becomes None (empty list parses to None in upload_report)
    assert call_kwargs["task_kwargs"]["allowed_connect_urls"] is None


def test_upload_report_admin_can_set_allowed_connect_urls(
    client: TestClient, mock_dispatch_job, user, tmp_path
):
    """Admin may pass allowed_connect_urls — needed for legitimate interactive reports."""
    html = b"<html><body>x</body></html>"

    response = client.post(
        "/reports/upload",
        files={"file": ("plain.html", BytesIO(html), "text/html")},
        data={
            "allow_scripts": "true",
            "allowed_connect_urls": "https://api.example.com",
        },
    )

    assert response.status_code == 200
    call_kwargs = mock_dispatch_job.call_args.kwargs
    assert call_kwargs["task_kwargs"]["allow_scripts"] is True
    assert call_kwargs["task_kwargs"]["allowed_connect_urls"] == ["https://api.example.com"]


# ====== Forum sync — admin-gated, rate-limited, max_items capped ======


@pytest.fixture
def reset_forum_sync_rate_limit_cache():
    """Make sure stale Redis state from earlier tests doesn't leak in."""
    from memory.common import rate_limit
    rate_limit.reset_cache()
    yield
    rate_limit.reset_cache()


def test_forum_sync_rejects_non_admin(regular_client: TestClient):
    """Regression: trigger_forum_sync was previously open to any authenticated
    user, letting a low-privilege contributor blow through the embedding
    budget by issuing thousands of max_items=1000 requests. Now restricted
    to admin scope.
    """
    with patch("memory.api.content_sources.celery_app") as mock_celery:
        response = regular_client.post(
            "/forums/sync",
            json={"min_karma": 10, "limit": 50, "max_items": 1000},
        )
    assert response.status_code == 403
    assert "admin" in response.json()["detail"].lower()
    mock_celery.send_task.assert_not_called()


def test_forum_sync_admin_caps_max_items_server_side(
    client: TestClient, reset_forum_sync_rate_limit_cache
):
    """Even an admin caller cannot exceed the server-side max_items ceiling.
    The previous endpoint forwarded the caller's value verbatim, so a
    request with max_items=1_000_000 would happily burn through that many
    items' worth of embeddings.
    """
    from memory.api.content_sources import FORUM_SYNC_MAX_ITEMS_CAP

    fake_task = type("T", (), {"id": "task-xyz"})()
    with patch("memory.api.content_sources.celery_app") as mock_celery:
        mock_celery.send_task.return_value = fake_task
        response = client.post(
            "/forums/sync",
            json={"min_karma": 10, "limit": 50, "max_items": 1_000_000},
        )

    assert response.status_code == 200
    call_kwargs = mock_celery.send_task.call_args.kwargs["kwargs"]
    assert call_kwargs["max_items"] == FORUM_SYNC_MAX_ITEMS_CAP


def test_forum_sync_rate_limited(
    client: TestClient, reset_forum_sync_rate_limit_cache
):
    """Per-user rate limit: 1/minute. Second consecutive call must 429."""
    fake_task = type("T", (), {"id": "task-xyz"})()
    with patch("memory.api.content_sources.celery_app") as mock_celery:
        mock_celery.send_task.return_value = fake_task

        first = client.post(
            "/forums/sync",
            json={"min_karma": 10, "limit": 50, "max_items": 100},
        )
        second = client.post(
            "/forums/sync",
            json={"min_karma": 10, "limit": 50, "max_items": 100},
        )

    assert first.status_code == 200
    assert second.status_code == 429
    assert "rate limit" in second.json()["detail"].lower()
    # Only one task was actually enqueued.
    assert mock_celery.send_task.call_count == 1
