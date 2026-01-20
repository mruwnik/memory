"""Tests for meetings API endpoints."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from memory.common.db.models import JobType


def test_create_meeting_queues_job(client: TestClient, user):
    """Test creating a meeting queues a processing job."""
    with patch("memory.api.meetings.dispatch_job") as mock_dispatch:
        mock_job = type("MockJob", (), {
            "id": 100,
            "status": "pending",
        })()
        mock_dispatch.return_value = type("DispatchResult", (), {
            "job": mock_job,
            "is_new": True,
            "message": "Job queued. Track status via GET /jobs/100",
        })()

        response = client.post(
            "/meetings",
            json={
                "transcript": "Speaker 1: Hello\nSpeaker 2: Hi there",
                "title": "Test Meeting",
                "meeting_date": "2024-01-15T10:00:00",
                "duration_minutes": 30,
                "attendees": ["Alice", "Bob"],
                "source_tool": "zoom",
                "external_id": "meeting-ext-123",
                "tags": ["project-x"],
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == 100
    assert data["status"] == "queued"
    assert data["external_id"] == "meeting-ext-123"
    assert "Job queued" in data["message"]

    # Verify dispatch_job was called correctly
    mock_dispatch.assert_called_once()
    call_kwargs = mock_dispatch.call_args.kwargs
    assert call_kwargs["job_type"] == JobType.MEETING
    assert call_kwargs["external_id"] == "meeting-ext-123"
    assert call_kwargs["exclude_from_params"] == ["transcript"]
    assert call_kwargs["task_kwargs"]["title"] == "Test Meeting"
    assert call_kwargs["task_kwargs"]["transcript"] == "Speaker 1: Hello\nSpeaker 2: Hi there"


def test_create_meeting_minimal_request(client: TestClient, user):
    """Test creating a meeting with only required fields."""
    with patch("memory.api.meetings.dispatch_job") as mock_dispatch:
        mock_job = type("MockJob", (), {"id": 101, "status": "pending"})()
        mock_dispatch.return_value = type("DispatchResult", (), {
            "job": mock_job,
            "is_new": True,
            "message": "Job queued",
        })()

        response = client.post(
            "/meetings",
            json={"transcript": "Meeting content here"},
        )

    assert response.status_code == 200
    assert response.json()["job_id"] == 101


def test_create_meeting_idempotent_returns_existing(client: TestClient, user):
    """Test that duplicate external_id returns existing job."""
    with patch("memory.api.meetings.dispatch_job") as mock_dispatch:
        mock_job = type("MockJob", (), {
            "id": 50,
            "status": "processing",
        })()
        mock_dispatch.return_value = type("DispatchResult", (), {
            "job": mock_job,
            "is_new": False,
            "message": "Job already exists with status: processing",
        })()

        response = client.post(
            "/meetings",
            json={
                "transcript": "Some content",
                "external_id": "already-exists-123",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == 50
    assert data["status"] == "processing"  # Not "queued"


def test_create_meeting_transcript_too_large(client: TestClient, user):
    """Test that oversized transcripts are rejected."""
    # MAX_TRANSCRIPT_SIZE is 500_000 (500KB)
    large_transcript = "x" * 600_000

    response = client.post(
        "/meetings",
        json={"transcript": large_transcript},
    )

    assert response.status_code == 400
    assert "exceeds maximum size" in response.json()["detail"]


def test_create_meeting_excludes_transcript_from_params(client: TestClient, user):
    """Test that transcript is excluded from stored job params."""
    with patch("memory.api.meetings.dispatch_job") as mock_dispatch:
        mock_job = type("MockJob", (), {"id": 102, "status": "pending"})()
        mock_dispatch.return_value = type("DispatchResult", (), {
            "job": mock_job,
            "is_new": True,
            "message": "Job queued",
        })()

        response = client.post(
            "/meetings",
            json={
                "transcript": "This is a very long transcript...",
                "title": "Big Meeting",
            },
        )

    assert response.status_code == 200

    # Check exclude_from_params was passed
    call_kwargs = mock_dispatch.call_args.kwargs
    assert "transcript" in call_kwargs["exclude_from_params"]


def test_create_meeting_passes_user_id(client: TestClient, user):
    """Test that user_id is passed to dispatch_job."""
    with patch("memory.api.meetings.dispatch_job") as mock_dispatch:
        mock_job = type("MockJob", (), {"id": 103, "status": "pending"})()
        mock_dispatch.return_value = type("DispatchResult", (), {
            "job": mock_job,
            "is_new": True,
            "message": "Job queued",
        })()

        client.post("/meetings", json={"transcript": "content"})

    call_kwargs = mock_dispatch.call_args.kwargs
    assert call_kwargs["user_id"] == user.id


def test_create_meeting_datetime_serialization(client: TestClient, user):
    """Test that meeting_date is properly serialized."""
    with patch("memory.api.meetings.dispatch_job") as mock_dispatch:
        mock_job = type("MockJob", (), {"id": 104, "status": "pending"})()
        mock_dispatch.return_value = type("DispatchResult", (), {
            "job": mock_job,
            "is_new": True,
            "message": "Job queued",
        })()

        response = client.post(
            "/meetings",
            json={
                "transcript": "content",
                "meeting_date": "2024-06-15T14:30:00",
            },
        )

    assert response.status_code == 200
    call_kwargs = mock_dispatch.call_args.kwargs
    # Should be ISO format string
    assert call_kwargs["task_kwargs"]["meeting_date"] == "2024-06-15T14:30:00"
