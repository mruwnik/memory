"""Tests for sessions API endpoints."""

import json
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from memory.common import settings
from memory.common.db.models import Project, Session, User


@pytest.fixture
def sessions_storage_dir(tmp_path):
    """Create a temporary sessions storage directory."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    with patch.object(settings, "SESSIONS_STORAGE_DIR", sessions_dir):
        yield sessions_dir


@pytest.fixture
def project_for_user(db_session, user):
    """Create a project owned by the test user."""
    project = Project(
        user_id=user.id,
        directory="/home/user/myproject",
        name="My Project",
        source="test-host",
    )
    db_session.add(project)
    db_session.commit()
    return project


@pytest.fixture
def session_for_user(db_session, user, project_for_user, sessions_storage_dir):
    """Create a session owned by the test user."""
    session_uuid = uuid4()
    transcript_path = f"{user.id}/{session_uuid}.jsonl"

    # Create the transcript file
    transcript_file = sessions_storage_dir / transcript_path
    transcript_file.parent.mkdir(parents=True, exist_ok=True)
    transcript_file.write_text("")

    session = Session(
        id=session_uuid,
        user_id=user.id,
        project_id=project_for_user.id,
        git_branch="main",
        tool_version="1.0.0",
        source="test-host",
        transcript_path=transcript_path,
    )
    db_session.add(session)
    db_session.commit()
    return session


@pytest.fixture
def other_user(db_session):
    """Create a different user for testing cross-user access."""
    existing = db_session.query(User).filter(User.id == 99999).first()
    if existing:
        return existing
    other = User(
        id=99999,
        name="Other User",
        email="other@example.com",
    )
    db_session.add(other)
    db_session.commit()
    return other


@pytest.fixture
def session_for_other_user(db_session, other_user, sessions_storage_dir):
    """Create a session owned by a different user."""
    session_uuid = uuid4()
    session = Session(
        id=session_uuid,
        user_id=other_user.id,
        git_branch="feature",
        source="other-host",
        transcript_path=f"{other_user.id}/{session_uuid}.jsonl",
    )
    db_session.add(session)
    db_session.commit()
    return session


def test_ingest_session_event_creates_session(
    client: TestClient, user, sessions_storage_dir
):
    """Test ingesting an event creates a new session."""
    session_id = str(uuid4())
    event = {
        "uuid": str(uuid4()),
        "timestamp": "2024-01-15T10:00:00Z",
        "type": "user",
        "user_type": "human",
        "message": {"content": "Hello"},
    }

    response = client.post(
        "/sessions/ingest",
        json={
            "session_id": session_id,
            "cwd": "/home/user/project",
            "source": "test-host",
            "event": event,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "accepted"
    assert data["session_id"] == session_id


def test_ingest_session_event_appends_to_transcript(
    client: TestClient, user, sessions_storage_dir
):
    """Test ingesting multiple events appends to transcript file."""
    session_id = str(uuid4())

    # Ingest first event
    event1 = {
        "uuid": str(uuid4()),
        "timestamp": "2024-01-15T10:00:00Z",
        "type": "user",
        "message": {"content": "First message"},
    }
    client.post(
        "/sessions/ingest",
        json={"session_id": session_id, "event": event1},
    )

    # Ingest second event
    event2 = {
        "uuid": str(uuid4()),
        "timestamp": "2024-01-15T10:01:00Z",
        "type": "assistant",
        "message": {"content": "Response"},
    }
    client.post(
        "/sessions/ingest",
        json={"session_id": session_id, "event": event2},
    )

    # Check transcript file
    transcript_file = sessions_storage_dir / f"{user.id}/{session_id}.jsonl"
    assert transcript_file.exists()

    lines = transcript_file.read_text().strip().split("\n")
    assert len(lines) == 2

    parsed_events = [json.loads(line) for line in lines]
    assert parsed_events[0]["uuid"] == event1["uuid"]
    assert parsed_events[1]["uuid"] == event2["uuid"]


def test_ingest_session_event_creates_project(
    client: TestClient, db_session, user, sessions_storage_dir
):
    """Test ingesting an event with cwd creates a project."""
    session_id = str(uuid4())
    cwd = "/home/user/new-project"

    response = client.post(
        "/sessions/ingest",
        json={
            "session_id": session_id,
            "cwd": cwd,
            "source": "test-host",
            "event": {
                "uuid": str(uuid4()),
                "timestamp": "2024-01-15T10:00:00Z",
                "type": "user",
            },
        },
    )

    assert response.status_code == 200

    # Check project was created
    project = (
        db_session.query(Project)
        .filter(Project.user_id == user.id, Project.directory == cwd)  # type: ignore[attr-defined]
        .first()
    )
    assert project is not None
    assert project.source == "test-host"


def test_ingest_session_event_invalid_uuid(client: TestClient, user):
    """Test ingesting with invalid session UUID returns 400."""
    response = client.post(
        "/sessions/ingest",
        json={
            "session_id": "not-a-valid-uuid",
            "event": {
                "uuid": str(uuid4()),
                "timestamp": "2024-01-15T10:00:00Z",
                "type": "user",
            },
        },
    )

    assert response.status_code == 400
    assert "Invalid session UUID" in response.json()["detail"]


def test_list_projects(client: TestClient, project_for_user):
    """Test listing projects for current user."""
    response = client.get("/sessions/projects")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1

    project_dirs = [p["directory"] for p in data["projects"]]
    assert project_for_user.directory in project_dirs


def test_list_projects_excludes_other_users(
    client: TestClient, db_session, other_user, sessions_storage_dir
):
    """Test listing projects only returns current user's projects."""
    # Create project for other user
    other_project = Project(
        user_id=other_user.id,
        directory="/other/project",
    )
    db_session.add(other_project)
    db_session.commit()

    response = client.get("/sessions/projects")

    assert response.status_code == 200
    data = response.json()
    project_dirs = [p["directory"] for p in data["projects"]]
    assert "/other/project" not in project_dirs


def test_list_sessions(client: TestClient, session_for_user):
    """Test listing sessions for current user."""
    response = client.get("/sessions/")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1

    session_ids = [s["session_id"] for s in data["sessions"]]
    assert str(session_for_user.id) in session_ids


def test_list_sessions_filter_by_project(
    client: TestClient, db_session, user, project_for_user, sessions_storage_dir
):
    """Test filtering sessions by project ID."""
    # Create another session without project
    other_session = Session(
        id=uuid4(),
        user_id=user.id,
        project_id=None,
        source="test",
    )
    db_session.add(other_session)
    db_session.commit()

    response = client.get(f"/sessions/?project_id={project_for_user.id}")

    assert response.status_code == 200
    data = response.json()
    # All returned sessions should have the specified project
    for session in data["sessions"]:
        assert session["project_id"] == project_for_user.id


def test_list_sessions_excludes_other_users(
    client: TestClient, session_for_other_user
):
    """Test listing sessions only returns current user's sessions."""
    response = client.get("/sessions/")

    assert response.status_code == 200
    data = response.json()
    session_ids = [s["session_id"] for s in data["sessions"]]
    assert str(session_for_other_user.id) not in session_ids


def test_get_session_transcript(
    client: TestClient, session_for_user, sessions_storage_dir
):
    """Test getting session transcript."""
    # Write some events to the transcript
    transcript_file = sessions_storage_dir / session_for_user.transcript_path
    events = [
        {"uuid": str(uuid4()), "type": "user", "timestamp": "2024-01-15T10:00:00Z"},
        {"uuid": str(uuid4()), "type": "assistant", "timestamp": "2024-01-15T10:01:00Z"},
    ]
    transcript_file.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    response = client.get(f"/sessions/{session_for_user.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == str(session_for_user.id)
    assert data["total_events"] == 2
    assert len(data["events"]) == 2


def test_get_session_transcript_pagination(
    client: TestClient, session_for_user, sessions_storage_dir
):
    """Test transcript pagination."""
    # Write 5 events to the transcript
    transcript_file = sessions_storage_dir / session_for_user.transcript_path
    events = [
        {"uuid": str(uuid4()), "type": "user", "timestamp": f"2024-01-15T10:0{i}:00Z"}
        for i in range(5)
    ]
    transcript_file.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    # Get first page
    response1 = client.get(f"/sessions/{session_for_user.id}?limit=2&offset=0")
    assert response1.status_code == 200
    data1 = response1.json()
    assert len(data1["events"]) == 2
    assert data1["total_events"] == 5

    # Get second page
    response2 = client.get(f"/sessions/{session_for_user.id}?limit=2&offset=2")
    assert response2.status_code == 200
    data2 = response2.json()
    assert len(data2["events"]) == 2

    # Events should be different
    page1_uuids = {e["uuid"] for e in data1["events"]}
    page2_uuids = {e["uuid"] for e in data2["events"]}
    assert page1_uuids.isdisjoint(page2_uuids)


def test_get_session_transcript_not_found(client: TestClient, user):
    """Test getting transcript for non-existent session."""
    fake_uuid = str(uuid4())
    response = client.get(f"/sessions/{fake_uuid}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Session not found"


def test_get_session_transcript_other_user(client: TestClient, session_for_other_user):
    """Test cannot get other user's session transcript."""
    response = client.get(f"/sessions/{session_for_other_user.id}")

    # Should return 404 to avoid leaking session existence
    assert response.status_code == 404


def test_get_session_transcript_invalid_uuid(client: TestClient, user):
    """Test getting transcript with invalid UUID returns 400."""
    response = client.get("/sessions/not-a-valid-uuid")

    assert response.status_code == 400
    assert "Invalid session UUID" in response.json()["detail"]


def test_ingest_with_parent_session(
    client: TestClient, session_for_user, sessions_storage_dir
):
    """Test ingesting a subagent session with parent reference."""
    child_session_id = str(uuid4())
    parent_session_id = str(session_for_user.id)

    response = client.post(
        "/sessions/ingest",
        json={
            "session_id": child_session_id,
            "parent_session_id": parent_session_id,
            "event": {
                "uuid": str(uuid4()),
                "timestamp": "2024-01-15T10:00:00Z",
                "type": "assistant",
            },
        },
    )

    assert response.status_code == 200


def test_ingest_with_git_branch(client: TestClient, user, sessions_storage_dir):
    """Test ingesting event with git branch info."""
    session_id = str(uuid4())

    response = client.post(
        "/sessions/ingest",
        json={
            "session_id": session_id,
            "cwd": "/home/user/project",
            "event": {
                "uuid": str(uuid4()),
                "timestamp": "2024-01-15T10:00:00Z",
                "type": "user",
                "git_branch": "feature/new-feature",
                "version": "1.2.3",
            },
        },
    )

    assert response.status_code == 200


@pytest.mark.parametrize(
    "limit,offset",
    [
        (10, 0),
        (50, 10),
        (100, 0),
    ],
)
def test_list_sessions_pagination_params(
    client: TestClient, session_for_user, limit, offset
):
    """Test sessions list accepts pagination parameters."""
    response = client.get(f"/sessions/?limit={limit}&offset={offset}")

    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "sessions" in data


@pytest.mark.parametrize(
    "limit,offset",
    [
        (10, 0),
        (50, 10),
        (100, 0),
    ],
)
def test_list_projects_pagination_params(
    client: TestClient, project_for_user, limit, offset
):
    """Test projects list accepts pagination parameters."""
    response = client.get(f"/sessions/projects?limit={limit}&offset={offset}")

    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "projects" in data
