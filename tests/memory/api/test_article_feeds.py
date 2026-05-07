"""Tests for Article Feeds API endpoints."""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from memory.common.db.models.sources import ArticleFeed
from memory.common.ssrf import UnsafeURLError


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


# ====== SSRF gate tests ======


@patch("memory.api.article_feeds.validate_public_url")
def test_create_feed_rejects_ssrf_url(mock_validate, client, db_session, user):
    """create_feed must call the SSRF gate; URLs targeting private IPs
    are 400, not 200."""
    mock_validate.side_effect = UnsafeURLError("targets non-public IP")

    response = client.post(
        "/article-feeds",
        json={"url": "http://169.254.169.254/latest/meta-data/"},
    )

    assert response.status_code == 400
    assert "not allowed" in response.json()["detail"].lower()
    # Row must NOT have been written.
    assert db_session.query(ArticleFeed).count() == 0


@patch("memory.parsers.feeds.get_feed_parser")
@patch("memory.api.article_feeds.validate_public_url")
def test_discover_feed_rejects_ssrf_url(
    mock_validate, mock_get_parser, client, db_session, user
):
    """discover_feed must call the SSRF gate before fetching."""
    mock_validate.side_effect = UnsafeURLError("targets non-public IP")

    response = client.post(
        "/article-feeds/discover",
        json="http://10.0.0.5/admin",
    )

    assert response.status_code == 400
    # Parser must NOT have been called — the SSRF gate fired first.
    mock_get_parser.assert_not_called()


@patch("memory.common.celery_app.app")
@patch("memory.api.article_feeds.validate_public_url")
def test_trigger_sync_rejects_ssrf_url(
    mock_validate, mock_app, client, db_session, user
):
    """trigger_sync re-validates the persisted URL — defends against
    a row whose hostname has been DNS-rebound to a private range."""
    feed = ArticleFeed(
        url="http://internal.example.com/feed.xml",
        title="Compromised feed",
    )
    db_session.add(feed)
    db_session.commit()

    mock_validate.side_effect = UnsafeURLError("hostname resolves to private IP")

    response = client.post(f"/article-feeds/{feed.id}/sync")

    assert response.status_code == 400
    # Celery task must NOT have been dispatched.
    mock_app.send_task.assert_not_called()


# ====== Cross-tenant ownership tests ======


def test_list_feeds_filters_by_owner_for_non_admin(regular_client, db_session, user):
    """Non-admin users see only their own feeds."""
    from memory.common.db.models import HumanUser

    other = HumanUser(
        name="Other",
        email="other-feeds@example.com",
        password_hash="bcrypt_hash_placeholder",
    )
    db_session.add(other)
    db_session.commit()

    own = ArticleFeed(
        user_id=user.id,
        url="https://my-feed.example.com/rss.xml",
        title="Mine",
    )
    other_feed = ArticleFeed(
        user_id=other.id,
        url="https://other-feed.example.com/rss.xml",
        title="Theirs",
    )
    legacy = ArticleFeed(
        user_id=None,
        url="https://legacy.example.com/rss.xml",
        title="Pre-ownership",
    )
    db_session.add_all([own, other_feed, legacy])
    db_session.commit()

    response = regular_client.get("/article-feeds")

    assert response.status_code == 200
    urls = {f["url"] for f in response.json()}
    assert urls == {"https://my-feed.example.com/rss.xml"}


def test_list_feeds_admin_sees_all(client, db_session, user):
    """Admin (the default test client) sees every feed including legacy."""
    from memory.common.db.models import HumanUser

    other = HumanUser(
        name="Other",
        email="other-feeds-admin@example.com",
        password_hash="bcrypt_hash_placeholder",
    )
    db_session.add(other)
    db_session.commit()

    db_session.add_all([
        ArticleFeed(user_id=user.id, url="https://a.example.com/rss.xml"),
        ArticleFeed(user_id=other.id, url="https://b.example.com/rss.xml"),
        ArticleFeed(user_id=None, url="https://legacy.example.com/rss.xml"),
    ])
    db_session.commit()

    response = client.get("/article-feeds")
    assert response.status_code == 200
    assert len(response.json()) == 3


def test_get_feed_404_for_other_users_feed(regular_client, db_session, user):
    """Cross-tenant get must 404 (not 200, not 403)."""
    from memory.common.db.models import HumanUser

    other = HumanUser(
        name="Other",
        email="other-feed-get@example.com",
        password_hash="bcrypt_hash_placeholder",
    )
    db_session.add(other)
    db_session.commit()

    feed = ArticleFeed(
        user_id=other.id,
        url="https://victim.example.com/rss.xml",
    )
    db_session.add(feed)
    db_session.commit()

    response = regular_client.get(f"/article-feeds/{feed.id}")
    assert response.status_code == 404


def test_get_feed_404_for_legacy_null_owner_non_admin(regular_client, db_session, user):
    """Legacy rows (user_id IS NULL) are admin-only."""
    feed = ArticleFeed(
        user_id=None,
        url="https://legacy-get.example.com/rss.xml",
    )
    db_session.add(feed)
    db_session.commit()

    response = regular_client.get(f"/article-feeds/{feed.id}")
    assert response.status_code == 404


def test_update_feed_404_for_other_users_feed(regular_client, db_session, user):
    """Cross-tenant patch must 404 — and must NOT mutate the feed."""
    from memory.common.db.models import HumanUser

    other = HumanUser(
        name="Other",
        email="other-feed-patch@example.com",
        password_hash="bcrypt_hash_placeholder",
    )
    db_session.add(other)
    db_session.commit()

    feed = ArticleFeed(
        user_id=other.id,
        url="https://target.example.com/rss.xml",
        title="Original",
        active=True,
    )
    db_session.add(feed)
    db_session.commit()
    original_id = feed.id

    response = regular_client.patch(
        f"/article-feeds/{original_id}",
        json={"title": "Hijacked", "active": False},
    )

    assert response.status_code == 404
    db_session.expire_all()
    refreshed = db_session.get(ArticleFeed, original_id)
    assert refreshed is not None
    assert refreshed.title == "Original"
    assert refreshed.active is True


def test_delete_feed_404_for_other_users_feed(regular_client, db_session, user):
    """Cross-tenant delete must 404 — and the feed must survive."""
    from memory.common.db.models import HumanUser

    other = HumanUser(
        name="Other",
        email="other-feed-delete@example.com",
        password_hash="bcrypt_hash_placeholder",
    )
    db_session.add(other)
    db_session.commit()

    feed = ArticleFeed(
        user_id=other.id,
        url="https://victim-delete.example.com/rss.xml",
    )
    db_session.add(feed)
    db_session.commit()
    feed_id = feed.id

    response = regular_client.delete(f"/article-feeds/{feed_id}")
    assert response.status_code == 404

    # Row must still exist.
    db_session.expire_all()
    assert db_session.get(ArticleFeed, feed_id) is not None


def test_create_feed_attributes_to_caller(client, db_session, user):
    """Creating a feed sets user_id to the caller's id."""
    response = client.post(
        "/article-feeds",
        json={"url": "https://owned.example.com/rss.xml"},
    )
    assert response.status_code == 200
    feed = (
        db_session.query(ArticleFeed)
        .filter_by(url="https://owned.example.com/rss.xml")
        .first()
    )
    assert feed is not None
    assert feed.user_id == user.id


@patch("memory.common.celery_app.app")
def test_trigger_sync_404_for_other_users_feed(
    mock_app, regular_client, db_session, user
):
    """Cross-tenant sync trigger must 404 — Celery must not be called."""
    from memory.common.db.models import HumanUser

    other = HumanUser(
        name="Other",
        email="other-feed-sync@example.com",
        password_hash="bcrypt_hash_placeholder",
    )
    db_session.add(other)
    db_session.commit()

    feed = ArticleFeed(
        user_id=other.id,
        url="https://other-sync.example.com/rss.xml",
    )
    db_session.add(feed)
    db_session.commit()

    response = regular_client.post(f"/article-feeds/{feed.id}/sync")
    assert response.status_code == 404
    mock_app.send_task.assert_not_called()
