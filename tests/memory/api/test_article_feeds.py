"""Tests for Article Feeds API endpoints."""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from memory.common.db.models.sources import ArticleFeed


# ====== GET /article-feeds tests ======


def test_list_feeds_returns_all_feeds(client, db_session, user):
    """List feeds returns all feeds in database."""
    feed1 = ArticleFeed(
        url="https://example.com/feed1.xml",
        title="Feed 1",
        description="First feed",
        tags=["tech"],
        check_interval=1440,
        active=True,
    )
    feed2 = ArticleFeed(
        url="https://example.com/feed2.xml",
        title="Feed 2",
        description="Second feed",
        tags=["news"],
        check_interval=720,
        active=False,
    )
    db_session.add(feed1)
    db_session.add(feed2)
    db_session.commit()

    response = client.get("/article-feeds")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["url"] == "https://example.com/feed1.xml"
    assert data[1]["url"] == "https://example.com/feed2.xml"


def test_list_feeds_empty_when_no_feeds(client, db_session, user):
    """List feeds returns empty list when no feeds exist."""
    response = client.get("/article-feeds")

    assert response.status_code == 200
    assert response.json() == []


# ====== POST /article-feeds tests ======


def test_create_feed_success(client, db_session, user):
    """Create feed succeeds with valid data."""
    payload = {
        "url": "https://example.com/rss.xml",
        "title": "My Feed",
        "description": "A test feed",
        "tags": ["tech", "news"],
        "check_interval": 720,
        "active": True,
    }

    response = client.post("/article-feeds", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["url"] == "https://example.com/rss.xml"
    assert data["title"] == "My Feed"
    assert data["description"] == "A test feed"
    assert data["tags"] == ["tech", "news"]
    assert data["check_interval"] == 720
    assert data["active"] is True

    # Verify in database
    feed = db_session.query(ArticleFeed).filter_by(url="https://example.com/rss.xml").first()
    assert feed is not None
    assert feed.title == "My Feed"


def test_create_feed_minimal_data(client, db_session, user):
    """Create feed with minimal data succeeds."""
    payload = {
        "url": "https://example.com/feed.xml",
    }

    response = client.post("/article-feeds", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["url"] == "https://example.com/feed.xml"
    assert data["title"] == "https://example.com/feed.xml"  # Defaults to URL
    assert data["tags"] == []
    assert data["check_interval"] == 1440  # Default 24 hours
    assert data["active"] is True


def test_create_feed_duplicate_url_fails(client, db_session, user):
    """Create feed with duplicate URL fails."""
    feed = ArticleFeed(
        url="https://example.com/feed.xml",
        title="Existing Feed",
    )
    db_session.add(feed)
    db_session.commit()

    payload = {
        "url": "https://example.com/feed.xml",
        "title": "New Feed",
    }

    response = client.post("/article-feeds", json=payload)

    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


def test_create_feed_invalid_url_fails(client, db_session, user):
    """Create feed with invalid URL fails validation."""
    payload = {
        "url": "not-a-valid-url",
        "title": "Bad Feed",
    }

    response = client.post("/article-feeds", json=payload)

    assert response.status_code == 422  # Validation error


# ====== GET /article-feeds/{feed_id} tests ======


def test_get_feed_success(client, db_session, user):
    """Get feed by ID returns feed details."""
    feed = ArticleFeed(
        url="https://example.com/feed.xml",
        title="Test Feed",
        description="A test",
        tags=["tech"],
        check_interval=1440,
        active=True,
    )
    db_session.add(feed)
    db_session.commit()

    response = client.get(f"/article-feeds/{feed.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == feed.id
    assert data["url"] == "https://example.com/feed.xml"
    assert data["title"] == "Test Feed"


def test_get_feed_not_found(client, db_session, user):
    """Get feed returns 404 when feed doesn't exist."""
    response = client.get("/article-feeds/999999")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


# ====== PATCH /article-feeds/{feed_id} tests ======


def test_update_feed_title(client, db_session, user):
    """Update feed title succeeds."""
    feed = ArticleFeed(
        url="https://example.com/feed.xml",
        title="Original Title",
        check_interval=1440,
    )
    db_session.add(feed)
    db_session.commit()

    response = client.patch(
        f"/article-feeds/{feed.id}",
        json={"title": "Updated Title"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Updated Title"

    # Verify in database
    db_session.refresh(feed)
    assert feed.title == "Updated Title"


@pytest.mark.parametrize(
    "field,value",
    [
        ("description", "New description"),
        ("tags", ["new", "tags"]),
        ("check_interval", 360),
        ("active", False),
    ],
)
def test_update_feed_fields(client, db_session, user, field, value):
    """Update feed fields succeeds."""
    feed = ArticleFeed(
        url="https://example.com/feed.xml",
        title="Test Feed",
        description="Old description",
        tags=["old"],
        check_interval=1440,
        active=True,
    )
    db_session.add(feed)
    db_session.commit()

    response = client.patch(
        f"/article-feeds/{feed.id}",
        json={field: value},
    )

    assert response.status_code == 200

    # Verify in database
    db_session.refresh(feed)
    assert getattr(feed, field) == value


def test_update_feed_multiple_fields(client, db_session, user):
    """Update multiple feed fields at once succeeds."""
    feed = ArticleFeed(
        url="https://example.com/feed.xml",
        title="Original",
        check_interval=1440,
        active=True,
    )
    db_session.add(feed)
    db_session.commit()

    response = client.patch(
        f"/article-feeds/{feed.id}",
        json={
            "title": "Updated",
            "check_interval": 720,
            "active": False,
        },
    )

    assert response.status_code == 200

    # Verify all updates
    db_session.refresh(feed)
    assert feed.title == "Updated"
    assert feed.check_interval == 720
    assert feed.active is False


def test_update_feed_not_found(client, db_session, user):
    """Update feed returns 404 when feed doesn't exist."""
    response = client.patch(
        "/article-feeds/999999",
        json={"title": "New Title"},
    )

    assert response.status_code == 404


# ====== DELETE /article-feeds/{feed_id} tests ======


def test_delete_feed_success(client, db_session, user):
    """Delete feed succeeds."""
    feed = ArticleFeed(
        url="https://example.com/feed.xml",
        title="Test Feed",
    )
    db_session.add(feed)
    db_session.commit()
    feed_id = feed.id

    response = client.delete(f"/article-feeds/{feed_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "deleted"

    # Verify deleted from database
    deleted_feed = db_session.query(ArticleFeed).filter_by(id=feed_id).first()
    assert deleted_feed is None


def test_delete_feed_not_found(client, db_session, user):
    """Delete feed returns 404 when not found."""
    response = client.delete("/article-feeds/999999")

    assert response.status_code == 404


# ====== POST /article-feeds/{feed_id}/sync tests ======


@patch("memory.common.celery_app.app")
def test_trigger_sync_success(mock_app, client, db_session, user):
    """Trigger sync sends Celery task."""
    feed = ArticleFeed(
        url="https://example.com/feed.xml",
        title="Test Feed",
    )
    db_session.add(feed)
    db_session.commit()

    mock_task = MagicMock()
    mock_task.id = "task-123-456"
    mock_app.send_task.return_value = mock_task

    response = client.post(f"/article-feeds/{feed.id}/sync")

    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == "task-123-456"
    assert data["status"] == "scheduled"

    # Verify Celery task was sent
    mock_app.send_task.assert_called_once()
    call_args = mock_app.send_task.call_args
    assert call_args[1]["args"] == [feed.id]


@patch("memory.common.celery_app.app")
def test_trigger_sync_not_found(mock_app, client, db_session, user):
    """Trigger sync returns 404 when feed doesn't exist."""
    response = client.post("/article-feeds/999999/sync")

    assert response.status_code == 404
    mock_app.send_task.assert_not_called()


# ====== POST /article-feeds/discover tests ======


@patch("memory.parsers.feeds.get_feed_parser")
def test_discover_feed_success(mock_get_parser, client, db_session, user):
    """Discover feed returns metadata from parser."""
    mock_parser = MagicMock()
    mock_parser.title = "Discovered Feed"
    mock_parser.description = "A discovered feed"
    mock_get_parser.return_value = mock_parser

    response = client.post(
        "/article-feeds/discover?url=https://example.com/blog"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["url"] == "https://example.com/blog"
    assert data["title"] == "Discovered Feed"
    assert data["description"] == "A discovered feed"

    mock_get_parser.assert_called_once_with("https://example.com/blog")


@patch("memory.parsers.feeds.get_feed_parser")
def test_discover_feed_no_parser_found(mock_get_parser, client, db_session, user):
    """Discover feed returns 400 when parser cannot be found."""
    mock_get_parser.return_value = None

    response = client.post(
        "/article-feeds/discover?url=https://example.com/not-a-feed"
    )

    assert response.status_code == 400
    assert "Could not parse" in response.json()["detail"]


@patch("memory.parsers.feeds.get_feed_parser")
def test_discover_feed_invalid_url(mock_get_parser, client, db_session, user):
    """Discover feed with invalid URL fails validation."""
    response = client.post(
        "/article-feeds/discover?url=not-a-url"
    )

    assert response.status_code == 422  # Validation error
    mock_get_parser.assert_not_called()


# ====== Helper function tests ======


def test_feed_to_response_with_all_fields(db_session):
    """feed_to_response converts feed with all fields."""
    from memory.api.article_feeds import feed_to_response

    now = datetime.now(timezone.utc)
    feed = ArticleFeed(
        id=1,
        url="https://example.com/feed.xml",
        title="Test Feed",
        description="A test",
        tags=["tech", "news"],
        check_interval=720,
        last_checked_at=now,
        active=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(feed)
    db_session.commit()

    response = feed_to_response(feed)

    assert response.id == 1
    assert response.url == "https://example.com/feed.xml"
    assert response.title == "Test Feed"
    assert response.description == "A test"
    assert response.tags == ["tech", "news"]
    assert response.check_interval == 720
    assert response.last_checked_at == now.isoformat()
    assert response.active is True
    assert response.created_at == now.isoformat()
    assert response.updated_at == now.isoformat()


def test_feed_to_response_with_minimal_fields(db_session):
    """feed_to_response converts feed with minimal fields."""
    from memory.api.article_feeds import feed_to_response

    now = datetime.now(timezone.utc)
    feed = ArticleFeed(
        id=2,
        url="https://example.com/feed.xml",
        title="Minimal Feed",
        tags=None,
        last_checked_at=None,
        created_at=now,
        updated_at=now,
    )
    db_session.add(feed)
    db_session.commit()

    response = feed_to_response(feed)

    assert response.id == 2
    assert response.url == "https://example.com/feed.xml"
    assert response.title == "Minimal Feed"
    assert response.description is None
    assert response.tags == []
    assert response.last_checked_at is None
