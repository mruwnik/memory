"""Tests for tasks API endpoints."""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from memory.common.db.models import Task


@pytest.fixture(scope="module")
def app_client(request):
    """Create a test client with mocked authentication (module-scoped to avoid MCP issues)."""
    from fastapi.testclient import TestClient
    from memory.api import auth

    # Patch auth functions
    with patch.object(auth, "get_token", return_value="fake-token"):
        with patch.object(auth, "get_session_user") as mock_get_user:
            # Create a mock user
            mock_user = MagicMock()
            mock_user.id = 1
            mock_user.email = "test@example.com"
            mock_get_user.return_value = mock_user

            from memory.api.app import app

            with TestClient(app, raise_server_exceptions=False) as test_client:
                yield test_client, app


@pytest.fixture
def client(app_client, db_session):
    """Get the test client and configure DB session for each test."""
    from memory.common.db.connection import get_session

    test_client, app = app_client

    # Override the get_session dependency to use our test session
    def get_test_session():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_session] = get_test_session
    yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def sample_tasks(db_session):
    """Create sample tasks in the database."""
    tasks = [
        Task(
            task_title="Task One",
            priority="high",
            status="pending",
            due_date=datetime(2024, 1, 15, tzinfo=timezone.utc),
            sha256=b"1" * 32,
        ),
        Task(
            task_title="Task Two",
            priority="low",
            status="in_progress",
            sha256=b"2" * 32,
        ),
        Task(
            task_title="Completed Task",
            priority="medium",
            status="done",
            completed_at=datetime(2024, 1, 10, tzinfo=timezone.utc),
            sha256=b"3" * 32,
        ),
    ]
    for task in tasks:
        db_session.add(task)
    db_session.commit()
    for task in tasks:
        db_session.refresh(task)
    return tasks


# =============================================================================
# GET /tasks - List tasks
# =============================================================================


def test_list_tasks_returns_active_by_default(client, sample_tasks):
    """Test that listing tasks excludes completed by default."""
    response = client.get("/tasks")

    assert response.status_code == 200
    tasks = response.json()
    titles = [t["task_title"] for t in tasks]
    assert "Task One" in titles
    assert "Task Two" in titles
    assert "Completed Task" not in titles


def test_list_tasks_with_include_completed(client, sample_tasks):
    """Test listing tasks with completed included."""
    response = client.get("/tasks?include_completed=true")

    assert response.status_code == 200
    tasks = response.json()
    titles = [t["task_title"] for t in tasks]
    assert "Completed Task" in titles


def test_list_tasks_filter_by_status(client, sample_tasks):
    """Test filtering tasks by status."""
    response = client.get("/tasks?status=in_progress")

    assert response.status_code == 200
    tasks = response.json()
    assert len(tasks) == 1
    assert tasks[0]["task_title"] == "Task Two"


def test_list_tasks_filter_by_priority(client, sample_tasks):
    """Test filtering tasks by priority."""
    response = client.get("/tasks?priority=high")

    assert response.status_code == 200
    tasks = response.json()
    assert len(tasks) == 1
    assert tasks[0]["task_title"] == "Task One"


def test_list_tasks_respects_limit(client, sample_tasks):
    """Test that limit parameter is respected."""
    response = client.get("/tasks?limit=1")

    assert response.status_code == 200
    tasks = response.json()
    assert len(tasks) == 1


# =============================================================================
# POST /tasks - Create task
# =============================================================================


def test_create_task_minimal(client):
    """Test creating a task with minimal data."""
    response = client.post(
        "/tasks",
        json={"task_title": "New Task"}
    )

    assert response.status_code == 200
    task = response.json()
    assert task["task_title"] == "New Task"
    assert task["status"] == "pending"
    assert task["priority"] is None


def test_create_task_with_all_fields(client):
    """Test creating a task with all fields."""
    response = client.post(
        "/tasks",
        json={
            "task_title": "Full Task",
            "priority": "urgent",
            "due_date": "2024-02-01T10:00:00Z",
            "recurrence": "FREQ=WEEKLY",
            "tags": ["work", "important"],
        }
    )

    assert response.status_code == 200
    task = response.json()
    assert task["task_title"] == "Full Task"
    assert task["priority"] == "urgent"
    assert task["recurrence"] == "FREQ=WEEKLY"
    assert task["tags"] == ["work", "important"]


def test_create_task_validates_priority(client):
    """Test that invalid priority is rejected."""
    response = client.post(
        "/tasks",
        json={"task_title": "Bad Task", "priority": "invalid"}
    )

    assert response.status_code == 422  # Validation error


# =============================================================================
# GET /tasks/{id} - Get single task
# =============================================================================


def test_get_task_by_id(client, sample_tasks):
    """Test getting a single task by ID."""
    task_id = sample_tasks[0].id
    response = client.get(f"/tasks/{task_id}")

    assert response.status_code == 200
    task = response.json()
    assert task["task_title"] == "Task One"


def test_get_task_not_found(client):
    """Test getting a non-existent task returns 404."""
    response = client.get("/tasks/99999")

    assert response.status_code == 404


# =============================================================================
# PATCH /tasks/{id} - Update task
# =============================================================================


def test_update_task_title(client, sample_tasks):
    """Test updating a task's title."""
    task_id = sample_tasks[0].id
    response = client.patch(
        f"/tasks/{task_id}",
        json={"task_title": "Updated Title"}
    )

    assert response.status_code == 200
    task = response.json()
    assert task["task_title"] == "Updated Title"


def test_update_task_status_to_done(client, sample_tasks):
    """Test marking a task as done sets completed_at."""
    task_id = sample_tasks[0].id
    response = client.patch(
        f"/tasks/{task_id}",
        json={"status": "done"}
    )

    assert response.status_code == 200
    task = response.json()
    assert task["status"] == "done"
    assert task["completed_at"] is not None


def test_update_task_reopen_clears_completed_at(client, sample_tasks):
    """Test reopening a task clears completed_at."""
    task_id = sample_tasks[2].id  # Completed Task
    response = client.patch(
        f"/tasks/{task_id}",
        json={"status": "pending"}
    )

    assert response.status_code == 200
    task = response.json()
    assert task["status"] == "pending"
    assert task["completed_at"] is None


def test_update_task_not_found(client):
    """Test updating a non-existent task returns 404."""
    response = client.patch(
        "/tasks/99999",
        json={"task_title": "Won't Work"}
    )

    assert response.status_code == 404


# =============================================================================
# DELETE /tasks/{id} - Delete task
# =============================================================================


def test_delete_task(client, sample_tasks):
    """Test deleting a task."""
    task_id = sample_tasks[0].id
    response = client.delete(f"/tasks/{task_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "deleted"

    # Verify it's gone
    get_response = client.get(f"/tasks/{task_id}")
    assert get_response.status_code == 404


def test_delete_task_not_found(client):
    """Test deleting a non-existent task returns 404."""
    response = client.delete("/tasks/99999")

    assert response.status_code == 404


# =============================================================================
# POST /tasks/{id}/complete - Mark task complete
# =============================================================================


def test_complete_task(client, sample_tasks):
    """Test completing a task via the complete endpoint."""
    task_id = sample_tasks[0].id
    response = client.post(f"/tasks/{task_id}/complete")

    assert response.status_code == 200
    task = response.json()
    assert task["status"] == "done"
    assert task["completed_at"] is not None


def test_complete_task_not_found(client):
    """Test completing a non-existent task returns 404."""
    response = client.post("/tasks/99999/complete")

    assert response.status_code == 404
