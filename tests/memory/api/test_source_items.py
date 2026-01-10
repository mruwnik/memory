"""Tests for source items API endpoints."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from memory.common.db.models import Meeting, BlogPost, SourceItem, JobType


@pytest.fixture
def meeting(db_session):
    """Create a sample meeting."""
    meeting = Meeting(
        sha256=b"test_meeting_api_hash" + bytes(11),  # 32 bytes
        title="API Test Meeting",
        content="Meeting transcript content.",
        summary="Test summary",
        modality="meeting",
        tags=["test"],
        size=100,
    )
    db_session.add(meeting)
    db_session.commit()
    return meeting


@pytest.fixture
def blog_post(db_session):
    """Create a sample blog post."""
    post = BlogPost(
        sha256=b"test_blog_api_hash_xx" + bytes(11),  # 32 bytes
        title="API Test Blog",
        content="Blog content here.",
        modality="blog",
        tags=["test"],
        size=50,
    )
    db_session.add(post)
    db_session.commit()
    return post


def test_get_source_item(client: TestClient, meeting):
    """Test getting a source item by ID."""
    response = client.get(f"/source-items/{meeting.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == meeting.id
    assert data["type"] == "Meeting"
    assert data["modality"] == "meeting"
    assert data["tags"] == ["test"]


def test_get_source_item_not_found(client: TestClient):
    """Test getting a non-existent source item."""
    response = client.get("/source-items/99999")

    assert response.status_code == 404
    assert response.json()["detail"] == "Source item not found"


def test_get_source_item_blog(client: TestClient, blog_post):
    """Test getting a blog post source item."""
    response = client.get(f"/source-items/{blog_post.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "BlogPost"
    assert data["modality"] == "blog"


def test_reingest_meeting(client: TestClient, meeting, user):
    """Test reingesting a meeting dispatches correct task."""
    with patch("memory.api.source_items.dispatch_job") as mock_dispatch:
        # Mock the dispatch result
        mock_job = type("MockJob", (), {
            "id": 123,
            "status": "pending",
        })()
        mock_dispatch.return_value = type("DispatchResult", (), {
            "job": mock_job,
            "is_new": True,
            "message": "Job queued",
        })()

        response = client.post(f"/source-items/{meeting.id}/reingest")

    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == 123
    assert data["status"] == "queued"
    assert data["item_id"] == meeting.id
    assert data["item_type"] == "Meeting"

    # Verify dispatch_job was called with correct params
    mock_dispatch.assert_called_once()
    call_kwargs = mock_dispatch.call_args.kwargs
    assert call_kwargs["job_type"] == JobType.MEETING
    assert "REPROCESS_MEETING" in call_kwargs["task_name"] or "reprocess" in call_kwargs["task_name"].lower()
    assert call_kwargs["task_kwargs"]["item_id"] == meeting.id


def test_reingest_blog_post(client: TestClient, blog_post, user):
    """Test reingesting a blog post dispatches generic reingest task."""
    with patch("memory.api.source_items.dispatch_job") as mock_dispatch:
        mock_job = type("MockJob", (), {
            "id": 456,
            "status": "pending",
        })()
        mock_dispatch.return_value = type("DispatchResult", (), {
            "job": mock_job,
            "is_new": True,
            "message": "Job queued",
        })()

        response = client.post(f"/source-items/{blog_post.id}/reingest")

    assert response.status_code == 200
    data = response.json()
    assert data["item_type"] == "BlogPost"

    # Verify it uses REINGEST_ITEM (generic), not REPROCESS_MEETING
    call_kwargs = mock_dispatch.call_args.kwargs
    assert call_kwargs["job_type"] == JobType.REPROCESS
    assert call_kwargs["task_kwargs"]["item_type"] == "BlogPost"


def test_reingest_not_found(client: TestClient):
    """Test reingesting a non-existent source item."""
    response = client.post("/source-items/99999/reingest")

    assert response.status_code == 404
    assert response.json()["detail"] == "Source item not found"


def test_reingest_returns_existing_job(client: TestClient, meeting, user):
    """Test that reingest returns existing job if one is already in progress."""
    with patch("memory.api.source_items.dispatch_job") as mock_dispatch:
        mock_job = type("MockJob", (), {
            "id": 789,
            "status": "processing",
        })()
        mock_dispatch.return_value = type("DispatchResult", (), {
            "job": mock_job,
            "is_new": False,
            "message": "Job already exists with status: processing",
        })()

        response = client.post(f"/source-items/{meeting.id}/reingest")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "processing"  # Returns existing status, not "queued"
    assert data["message"] == "Job already exists with status: processing"
