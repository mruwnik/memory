"""Tests for common task utilities."""

import pytest
from datetime import datetime, timezone

from memory.common.tasks import (
    task_to_dict,
    get_tasks,
    complete_task,
)
from memory.common.db.models import Task


# =============================================================================
# Tests for task_to_dict
# =============================================================================


def test_task_to_dict_basic():
    """Test converting a task to dict with all fields."""
    task = Task(
        task_title="Test Task",
        due_date=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        priority="high",
        status="pending",
        recurrence="FREQ=DAILY",
        tags=["work", "important"],
        sha256=b"test" + b"0" * 28,
    )
    task.id = 123

    result = task_to_dict(task)

    assert result["id"] == 123
    assert result["task_title"] == "Test Task"
    assert result["priority"] == "high"
    assert result["status"] == "pending"
    assert result["recurrence"] == "FREQ=DAILY"
    assert result["tags"] == ["work", "important"]
    assert "2024-01-15" in result["due_date"]
    assert result["inserted_at"] is None  # Not set for non-persisted task


def test_task_to_dict_minimal():
    """Test converting a task with minimal fields."""
    task = Task(
        task_title="Simple Task",
        status="pending",
        sha256=b"test" + b"0" * 28,
    )
    task.id = 1

    result = task_to_dict(task)

    assert result["id"] == 1
    assert result["task_title"] == "Simple Task"
    assert result["due_date"] is None
    assert result["priority"] is None
    assert result["status"] == "pending"
    assert result["completed_at"] is None


def test_task_to_dict_completed():
    """Test converting a completed task."""
    completed_at = datetime(2024, 1, 20, 15, 30, 0, tzinfo=timezone.utc)
    task = Task(
        task_title="Done Task",
        status="done",
        completed_at=completed_at,
        sha256=b"test" + b"0" * 28,
    )
    task.id = 2

    result = task_to_dict(task)

    assert result["status"] == "done"
    assert "2024-01-20" in result["completed_at"]


# =============================================================================
# Tests for get_tasks (require database)
# =============================================================================


@pytest.fixture
def sample_tasks(db_session):
    """Create sample tasks for testing."""
    tasks = [
        Task(
            task_title="Urgent Task",
            priority="urgent",
            status="pending",
            due_date=datetime(2024, 1, 10, tzinfo=timezone.utc),
            sha256=b"u" * 32,
        ),
        Task(
            task_title="High Priority",
            priority="high",
            status="in_progress",
            due_date=datetime(2024, 1, 15, tzinfo=timezone.utc),
            sha256=b"h" * 32,
        ),
        Task(
            task_title="Low Priority",
            priority="low",
            status="pending",
            due_date=datetime(2024, 1, 20, tzinfo=timezone.utc),
            sha256=b"l" * 32,
        ),
        Task(
            task_title="No Due Date",
            priority="medium",
            status="pending",
            sha256=b"n" * 32,
        ),
        Task(
            task_title="Completed Task",
            priority="high",
            status="done",
            completed_at=datetime(2024, 1, 12, tzinfo=timezone.utc),
            sha256=b"d" * 32,
        ),
    ]
    for t in tasks:
        db_session.add(t)
    db_session.commit()
    return tasks


def test_get_tasks_default_excludes_completed(db_session, sample_tasks):
    """Test that completed tasks are excluded by default."""
    tasks = get_tasks(db_session)

    titles = [t["task_title"] for t in tasks]
    assert "Completed Task" not in titles
    assert "Urgent Task" in titles


def test_get_tasks_include_completed(db_session, sample_tasks):
    """Test including completed tasks."""
    tasks = get_tasks(db_session, include_completed=True)

    titles = [t["task_title"] for t in tasks]
    assert "Completed Task" in titles


def test_get_tasks_filter_by_status(db_session, sample_tasks):
    """Test filtering by status."""
    tasks = get_tasks(db_session, status="in_progress")

    assert len(tasks) == 1
    assert tasks[0]["task_title"] == "High Priority"


def test_get_tasks_filter_by_priority(db_session, sample_tasks):
    """Test filtering by priority."""
    tasks = get_tasks(db_session, priority="urgent")

    assert len(tasks) == 1
    assert tasks[0]["task_title"] == "Urgent Task"


def test_get_tasks_filter_by_multiple_statuses(db_session, sample_tasks):
    """Test filtering by multiple statuses."""
    tasks = get_tasks(db_session, status=["pending", "in_progress"])

    assert len(tasks) == 4


def test_get_tasks_filter_by_due_before(db_session, sample_tasks):
    """Test filtering by due_before."""
    cutoff = datetime(2024, 1, 16, tzinfo=timezone.utc)
    tasks = get_tasks(db_session, due_before=cutoff)

    titles = [t["task_title"] for t in tasks]
    assert "Urgent Task" in titles
    assert "High Priority" in titles
    # "No Due Date" is included because due_before allows null dates
    assert "No Due Date" in titles


def test_get_tasks_respects_limit(db_session, sample_tasks):
    """Test that limit is respected."""
    tasks = get_tasks(db_session, limit=2)

    assert len(tasks) == 2


def test_get_tasks_sorted_by_due_date_and_priority(db_session, sample_tasks):
    """Test that tasks are sorted by due date then priority."""
    tasks = get_tasks(db_session)

    # First should be urgent (earliest due + highest priority)
    assert tasks[0]["task_title"] == "Urgent Task"


def test_get_tasks_empty_database(db_session):
    """Test with no tasks in database."""
    tasks = get_tasks(db_session)

    assert tasks == []


# =============================================================================
# Tests for complete_task
# =============================================================================


def test_complete_task_marks_done(db_session):
    """Test completing a task sets status and completed_at."""
    task = Task(
        task_title="To Complete",
        status="pending",
        sha256=b"c" * 32,
    )
    db_session.add(task)
    db_session.commit()

    result = complete_task(db_session, task.id)

    assert result.status == "done"
    assert result.completed_at is not None


def test_complete_task_not_found(db_session):
    """Test completing a non-existent task returns None."""
    result = complete_task(db_session, 99999)

    assert result is None


def test_complete_task_already_done(db_session):
    """Test completing an already-done task is idempotent."""
    original_time = datetime(2024, 1, 10, tzinfo=timezone.utc)
    task = Task(
        task_title="Already Done",
        status="done",
        completed_at=original_time,
        sha256=b"a" * 32,
    )
    db_session.add(task)
    db_session.commit()

    result = complete_task(db_session, task.id)

    # Should update to new completion time
    assert result.status == "done"
    assert result.completed_at != original_time
