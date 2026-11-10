"""Tests for content sources API endpoints."""

import hashlib
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from memory.common import paths, settings
from memory.common.access_control import credential_scopes
from memory.common.db.models import (
    ForumComment,
    ForumPost,
    ForumSyncConfig,
    HumanUser,
    JobType,
    Person,
    Project,
    Report,
    Team,
)
from memory.common.db.models.sources import project_teams, team_members


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
    assert call_kwargs["task_kwargs"]["creator_id"] == user.id
    assert call_kwargs["task_kwargs"]["creator_scopes"] == credential_scopes(user, None)
    # Per uploader, so one user's upload never dedups onto or deletes another's file.
    digest = hashlib.sha256(test_content).hexdigest()[:12]
    assert call_kwargs["task_kwargs"]["file_path"] == str(
        tmp_path / f"{digest}_{user.id}_test_book.epub"
    )


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
    assert call_kwargs["task_kwargs"]["creator_id"] == user.id
    assert call_kwargs["task_kwargs"]["creator_scopes"] == credential_scopes(user, None)
    digest = hashlib.sha256(jpeg_content).hexdigest()[:12]
    assert call_kwargs["task_kwargs"]["file_path"] == str(
        tmp_path / f"{digest}_{user.id}_test_photo.jpg"
    )


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


# ====== Reports upload — project membership ======


@pytest.fixture
def projects(db_session, user):
    """Two projects: the test user's team is on "mine", not on "theirs"."""
    mine = Project(title="Mine", state="open")
    theirs = Project(title="Theirs", state="open")
    team = Team(name="T", slug="t", is_active=True)
    user.person = Person(identifier="me", display_name="Me")
    db_session.add_all([mine, theirs, team])
    db_session.flush()
    db_session.execute(
        team_members.insert().values(team_id=team.id, person_id=user.person.id, role="member")
    )
    db_session.execute(project_teams.insert().values(project_id=mine.id, team_id=team.id))
    db_session.commit()
    return {"mine": mine.id, "theirs": theirs.id}


def upload_report_into(client: TestClient, project_id: int):
    return client.post(
        "/reports/upload",
        files={"file": ("plain.html", BytesIO(b"<p>project report</p>"), "text/html")},
        data={"project_id": str(project_id)},
    )


def test_upload_report_into_a_foreign_project_is_refused(
    member_client: TestClient, mock_dispatch_job, projects
):
    response = upload_report_into(member_client, projects["theirs"])

    assert response.status_code == 403
    mock_dispatch_job.assert_not_called()


@pytest.mark.parametrize(
    "client_fixture, project",
    [("member_client", "mine"), ("client", "mine"), ("client", "theirs")],
)
def test_upload_report_into_a_permitted_project(
    request, mock_dispatch_job, projects, client_fixture, project
):
    response = upload_report_into(request.getfixturevalue(client_fixture), projects[project])

    assert response.status_code == 200
    assert mock_dispatch_job.call_args.kwargs["task_kwargs"]["project_id"] == projects[project]


# ====== Reports upload — overwriting needs edit rights ======

REPORT_HTML = b"<p>quarterly numbers</p>"


def report_stored_as(db_session, creator_id: int, name: str) -> Report:
    """A public report occupying the filename an upload of REPORT_HTML as `name` gets."""
    content_hash = hashlib.sha256(REPORT_HTML).hexdigest()[:12]
    report = Report(
        modality="doc",
        mime_type="text/html",
        report_format="html",
        sha256=b"stored".ljust(32, b"\0"),
        filename=paths.to_db_filename(settings.REPORT_STORAGE_DIR / f"{content_hash}_{name}"),
        creator_id=creator_id,
        sensitivity="public",
    )
    db_session.add(report)
    db_session.commit()
    return report


@pytest.fixture
def other_user(db_session):
    other = HumanUser(id=999, email="other@example.com", name="O", password_hash="x")
    db_session.add(other)
    db_session.commit()
    return other


def upload_report_html(client: TestClient, name: str):
    return client.post(
        "/reports/upload", files={"file": (name, BytesIO(REPORT_HTML), "text/html")}
    )


def test_upload_report_refuses_to_overwrite_a_readable_report_of_someone_else(
    member_client: TestClient, mock_dispatch_job, db_session, other_user
):
    report_stored_as(db_session, other_user.id, "q3.html")

    response = upload_report_html(member_client, "q3.html")

    assert response.status_code == 403
    mock_dispatch_job.assert_not_called()


@pytest.mark.parametrize(
    "client_fixture, owner",
    [("member_client", "user"), ("client", "user"), ("client", "other_user")],
)
def test_upload_report_overwrite_names_the_checked_report(
    request, mock_dispatch_job, db_session, client_fixture, owner
):
    client = request.getfixturevalue(client_fixture)
    report = report_stored_as(db_session, request.getfixturevalue(owner).id, "q3.html")

    response = upload_report_html(client, "q3.html")

    assert response.status_code == 200
    assert mock_dispatch_job.call_args.kwargs["task_kwargs"]["existing_report_id"] == report.id


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


# ====== Forum summary and sync config ======


def forum_post(db_session, guid, published, sensitivity="public", comment_karmas=()):
    post = ForumPost(
        url=f"https://www.lesswrong.com/posts/{guid}/slug",
        title=f"Post {guid}",
        sha256=guid.encode().ljust(32, b"\0"),
        modality="forum",
        sensitivity=sensitivity,
        published_at=datetime.fromisoformat(published).replace(tzinfo=timezone.utc),
    )
    comments = [
        ForumComment(
            post=post,
            comment_id=f"{guid}-c{i}",
            sha256=f"{guid}-c{i}".encode().ljust(32, b"\0"),
            modality="forum",
            sensitivity=sensitivity,
            karma=karma,
        )
        for i, karma in enumerate(comment_karmas)
    ]
    db_session.add_all([post, *comments])
    db_session.commit()
    return post


def test_forum_summary_counts_only_what_the_caller_can_see(member_client, db_session):
    forum_post(db_session, "old", "2026-01-01T00:00:00", comment_karmas=(5, 9))
    forum_post(db_session, "hidden", "2026-10-01T00:00:00", "confidential", comment_karmas=(3,))
    config = ForumSyncConfig.load(db_session, "lesswrong")
    config.last_sync_at = datetime(2026, 10, 27, tzinfo=timezone.utc)
    config.last_sync_result = {"new_posts": 3}
    config.record_sync("LW down")
    db_session.commit()

    response = member_client.get("/forums/summary")

    assert response.status_code == 200
    body = response.json()
    assert (body["posts"], body["comments"]) == (1, 2)
    assert body["newest_post"] == {
        "title": "Post old",
        "url": "https://www.lesswrong.com/posts/old/slug",
        "published_at": "2026-01-01T00:00:00Z",
    }
    assert body["last_sync_at"] == "2026-10-27T00:00:00Z"
    assert body["last_sync_result"] == {"new_posts": 3}
    assert body["sync_error"] is None  # members can't act on it
    assert body["settings"]["min_karma"] == 10
    assert body["settings"]["check_interval"] == 1440


def test_admins_see_why_the_forum_sync_failed(client, db_session):
    ForumSyncConfig.load(db_session, "lesswrong").record_sync("LW down")
    db_session.commit()
    assert client.get("/forums/summary").json()["sync_error"] == "LW down"


def test_forum_summary_with_no_posts(client):
    body = client.get("/forums/summary").json()
    assert (body["posts"], body["comments"], body["newest_post"]) == (0, 0, None)


FORUM_SETTINGS = {
    "active": True,
    "check_interval": 720,
    "days_back": 14,
    "ingest_delay": 3 * 24 * 60,
    "max_items": 500,
    "min_karma": 20,
    "af": True,
    "comments_enabled": False,
    "min_comment_karma": 8,
    "comment_recheck_divisor": 5,
    "comment_recheck_max_days": 60,
    "comment_max_age_days": 180,
    "max_comment_fetches": 50,
}


def test_admin_updates_forum_settings(client, db_session):
    response = client.put("/forums/settings", json=FORUM_SETTINGS)

    assert response.status_code == 200
    assert response.json() == FORUM_SETTINGS
    db_session.expire_all()
    config = db_session.get(ForumSyncConfig, "lesswrong")
    assert {key: getattr(config, key) for key in FORUM_SETTINGS} == FORUM_SETTINGS


def test_forum_settings_are_admin_only(regular_client):
    response = regular_client.put("/forums/settings", json=FORUM_SETTINGS)
    assert response.status_code == 403


@pytest.mark.parametrize(
    "field, value",
    [
        ("check_interval", 0),
        ("days_back", 0),
        ("comment_recheck_divisor", 0),
        ("max_items", -1),
        ("ingest_delay", -1),
        ("comment_recheck_max_days", 10**9),
        ("comment_max_age_days", 10**9),
    ],
)
def test_forum_settings_reject_out_of_range_values(client, field, value):
    response = client.put("/forums/settings", json=FORUM_SETTINGS | {field: value})
    assert response.status_code == 422
