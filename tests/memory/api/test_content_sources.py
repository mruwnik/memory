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


