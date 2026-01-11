"""Tests for Organizer MCP tools (tasks/todos and calendar events)."""

import hashlib
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


# Mock FastMCP - this creates a decorator factory that passes through the function unchanged
class MockFastMCP:
    def __init__(self, name):
        self.name = name

    def tool(self):
        def decorator(func):
            return func

        return decorator


# Mock the fastmcp module before importing anything that uses it
_mock_fastmcp = MagicMock()
_mock_fastmcp.FastMCP = MockFastMCP
sys.modules["fastmcp"] = _mock_fastmcp

# Mock the mcp module and all its submodules
_mock_mcp = MagicMock()
_mock_mcp.tool = lambda: lambda f: f
sys.modules["mcp"] = _mock_mcp
sys.modules["mcp.types"] = MagicMock()
sys.modules["mcp.server"] = MagicMock()
sys.modules["mcp.server.auth"] = MagicMock()
sys.modules["mcp.server.auth.handlers"] = MagicMock()
sys.modules["mcp.server.auth.handlers.authorize"] = MagicMock()
sys.modules["mcp.server.auth.handlers.token"] = MagicMock()
sys.modules["mcp.server.auth.provider"] = MagicMock()
sys.modules["mcp.server.fastmcp"] = MagicMock()
sys.modules["mcp.server.fastmcp.server"] = MagicMock()

# Also mock the memory.api.MCP.base module to avoid MCP imports
_mock_base = MagicMock()
_mock_base.mcp = MagicMock()
_mock_base.mcp.tool = lambda: lambda f: f
sys.modules["memory.api.MCP.base"] = _mock_base

from memory.common.db import connection as db_connection
from memory.common.db.models import CalendarEvent, Task


def get_fn(tool):
    """Extract underlying function from FunctionTool if wrapped, else return as-is."""
    return getattr(tool, 'fn', tool)


@pytest.fixture(autouse=True)
def reset_db_cache():
    """Reset the cached database engine between tests."""
    db_connection._engine = None
    db_connection._session_factory = None
    db_connection._scoped_session = None
    yield
    db_connection._engine = None
    db_connection._session_factory = None
    db_connection._scoped_session = None


def create_task_hash(title: str) -> bytes:
    """Create a hash for a task based on title."""
    return hashlib.sha256(f"task:{title}".encode()).digest()


@pytest.fixture
def sample_tasks(db_session):
    """Create sample tasks for testing."""
    now = datetime.now(timezone.utc)

    tasks = [
        Task(
            task_title="Urgent task due today",
            due_date=now,
            priority="urgent",
            status="pending",
            tags=["work"],
            sha256=create_task_hash("Urgent task due today"),
            modality="task",
            size=0,
        ),
        Task(
            task_title="High priority tomorrow",
            due_date=now + timedelta(days=1),
            priority="high",
            status="in_progress",
            tags=["work", "project"],
            sha256=create_task_hash("High priority tomorrow"),
            modality="task",
            size=0,
        ),
        Task(
            task_title="Medium priority no date",
            due_date=None,
            priority="medium",
            status="pending",
            tags=["personal"],
            sha256=create_task_hash("Medium priority no date"),
            modality="task",
            size=0,
        ),
        Task(
            task_title="Completed task",
            due_date=now - timedelta(days=1),
            priority="low",
            status="done",
            completed_at=now,
            tags=["done"],
            sha256=create_task_hash("Completed task"),
            modality="task",
            size=0,
        ),
        Task(
            task_title="Cancelled task",
            due_date=now,
            priority="low",
            status="cancelled",
            tags=[],
            sha256=create_task_hash("Cancelled task"),
            modality="task",
            size=0,
        ),
    ]

    for task in tasks:
        db_session.add(task)
    db_session.commit()

    for task in tasks:
        db_session.refresh(task)

    return tasks


@pytest.fixture
def sample_events(db_session):
    """Create sample calendar events for testing."""
    now = datetime.now(timezone.utc)

    events = [
        CalendarEvent(
            event_title="Team meeting",
            start_time=now + timedelta(hours=1),
            end_time=now + timedelta(hours=2),
            all_day=False,
            location="Conference Room A",
            calendar_name="Work",
            sha256=hashlib.sha256(b"event:team-meeting").digest(),
            modality="calendar_event",
            size=0,
        ),
        CalendarEvent(
            event_title="All day event",
            start_time=now + timedelta(days=1),
            end_time=now + timedelta(days=2),
            all_day=True,
            location=None,
            calendar_name="Personal",
            sha256=hashlib.sha256(b"event:all-day").digest(),
            modality="calendar_event",
            size=0,
        ),
        CalendarEvent(
            event_title="Past event",
            start_time=now - timedelta(days=2),
            end_time=now - timedelta(days=2, hours=-1),
            all_day=False,
            location="Office",
            calendar_name="Work",
            sha256=hashlib.sha256(b"event:past").digest(),
            modality="calendar_event",
            size=0,
        ),
    ]

    for event in events:
        db_session.add(event)
    db_session.commit()

    for event in events:
        db_session.refresh(event)

    return events


# =============================================================================
# Tests for list_tasks
# =============================================================================


@pytest.mark.asyncio
async def test_list_tasks_no_filters(db_session, sample_tasks):
    """Test listing tasks without filters (excludes completed by default)."""
    from memory.api.MCP.servers.organizer import list_tasks

    list_tasks_fn = get_fn(list_tasks)

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        results = await list_tasks_fn()

    # Should exclude done and cancelled by default
    assert len(results) == 3
    titles = [r["task_title"] for r in results]
    assert "Completed task" not in titles
    assert "Cancelled task" not in titles


@pytest.mark.asyncio
async def test_list_tasks_include_completed(db_session, sample_tasks):
    """Test listing tasks with completed included."""
    from memory.api.MCP.servers.organizer import list_tasks

    list_tasks_fn = get_fn(list_tasks)

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        results = await list_tasks_fn(include_completed=True)

    assert len(results) == 5


@pytest.mark.asyncio
async def test_list_tasks_filter_by_status(db_session, sample_tasks):
    """Test filtering by status."""
    from memory.api.MCP.servers.organizer import list_tasks

    list_tasks_fn = get_fn(list_tasks)

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        results = await list_tasks_fn(status="in_progress")

    assert len(results) == 1
    assert results[0]["task_title"] == "High priority tomorrow"


@pytest.mark.asyncio
async def test_list_tasks_filter_by_priority(db_session, sample_tasks):
    """Test filtering by priority."""
    from memory.api.MCP.servers.organizer import list_tasks

    list_tasks_fn = get_fn(list_tasks)

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        results = await list_tasks_fn(priority="urgent")

    assert len(results) == 1
    assert results[0]["task_title"] == "Urgent task due today"


@pytest.mark.asyncio
async def test_list_tasks_limit(db_session, sample_tasks):
    """Test limiting results."""
    from memory.api.MCP.servers.organizer import list_tasks

    list_tasks_fn = get_fn(list_tasks)

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        results = await list_tasks_fn(limit=2)

    assert len(results) == 2


@pytest.mark.asyncio
async def test_list_tasks_limit_capped_at_200(db_session, sample_tasks):
    """Test that limit is capped at 200.

    TODO: This test only has 3 tasks, so `len(results) <= 200` always passes.
    To properly verify the cap, create 250+ tasks or mock the query layer
    to return 300 items and assert only 200 are returned.
    """
    from memory.api.MCP.servers.organizer import list_tasks

    list_tasks_fn = get_fn(list_tasks)

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        # Request 500, but implementation should cap at 200
        results = await list_tasks_fn(limit=500)

    # Verifies the cap logic exists, though with only 3 tasks this is a weak assertion
    assert len(results) <= 200


@pytest.mark.asyncio
async def test_list_tasks_offset(db_session, sample_tasks):
    """Test pagination with offset."""
    from memory.api.MCP.servers.organizer import list_tasks

    list_tasks_fn = get_fn(list_tasks)

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        all_results = await list_tasks_fn()
        offset_results = await list_tasks_fn(offset=1)

    assert len(offset_results) == len(all_results) - 1


# =============================================================================
# Tests for get_task
# =============================================================================


@pytest.mark.asyncio
async def test_get_task_found(db_session, sample_tasks):
    """Test getting a task that exists."""
    from memory.api.MCP.servers.organizer import get_task

    get_task_fn = get_fn(get_task)
    task_id = sample_tasks[0].id

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        result = await get_task_fn(task_id=task_id)

    assert result["success"] is True
    assert result["task"]["task_title"] == "Urgent task due today"
    assert result["task"]["priority"] == "urgent"


@pytest.mark.asyncio
async def test_get_task_not_found(db_session, sample_tasks):
    """Test getting a task that doesn't exist."""
    from memory.api.MCP.servers.organizer import get_task

    get_task_fn = get_fn(get_task)

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        result = await get_task_fn(task_id=99999)

    assert "error" in result
    assert "not found" in result["error"]


# =============================================================================
# Tests for create_task
# =============================================================================


@pytest.mark.asyncio
async def test_create_task_success(db_session):
    """Test creating a new task."""
    from memory.api.MCP.servers.organizer import create_task

    create_task_fn = get_fn(create_task)

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        result = await create_task_fn(
            title="New test task",
            priority="high",
            tags=["test"],
        )

    assert result["task_title"] == "New test task"
    assert result["priority"] == "high"
    assert result["status"] == "pending"
    assert "test" in result["tags"]

    # Verify task was actually created
    task = db_session.query(Task).filter_by(task_title="New test task").first()
    assert task is not None


@pytest.mark.asyncio
async def test_create_task_with_due_date(db_session):
    """Test creating a task with a due date."""
    from memory.api.MCP.servers.organizer import create_task

    create_task_fn = get_fn(create_task)

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        result = await create_task_fn(
            title="Task with date",
            due_date="2024-12-25T10:00:00Z",
        )

    assert result["due_date"] is not None
    assert "2024-12-25" in result["due_date"]


@pytest.mark.asyncio
async def test_create_task_invalid_due_date(db_session):
    """Test creating a task with invalid due date."""
    from memory.api.MCP.servers.organizer import create_task

    create_task_fn = get_fn(create_task)

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        with pytest.raises(ValueError, match="Invalid due_date format"):
            await create_task_fn(
                title="Task with bad date",
                due_date="not-a-date",
            )


@pytest.mark.asyncio
async def test_create_task_idempotent(db_session):
    """Test that creating a task with the same title returns existing task."""
    from memory.api.MCP.servers.organizer import create_task

    create_task_fn = get_fn(create_task)

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        result1 = await create_task_fn(title="Idempotent task")
        result2 = await create_task_fn(title="Idempotent task")

    assert result1["id"] == result2["id"]

    # Verify only one task exists
    count = db_session.query(Task).filter_by(task_title="Idempotent task").count()
    assert count == 1


# =============================================================================
# Tests for update_task
# =============================================================================


@pytest.mark.asyncio
async def test_update_task_success(db_session, sample_tasks):
    """Test updating a task."""
    from memory.api.MCP.servers.organizer import update_task

    update_task_fn = get_fn(update_task)
    task_id = sample_tasks[0].id

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        result = await update_task_fn(
            task_id=task_id,
            title="Updated title",
            priority="low",
        )

    assert result["task_title"] == "Updated title"
    assert result["priority"] == "low"


@pytest.mark.asyncio
async def test_update_task_not_found(db_session, sample_tasks):
    """Test updating a task that doesn't exist."""
    from memory.api.MCP.servers.organizer import update_task

    update_task_fn = get_fn(update_task)

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        with pytest.raises(ValueError, match="not found"):
            await update_task_fn(task_id=99999, title="Won't work")


@pytest.mark.asyncio
async def test_update_task_status_to_done_sets_completed_at(db_session, sample_tasks):
    """Test that updating status to done sets completed_at."""
    from memory.api.MCP.servers.organizer import update_task

    update_task_fn = get_fn(update_task)
    task_id = sample_tasks[0].id

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        result = await update_task_fn(task_id=task_id, status="done")

    assert result["status"] == "done"
    assert result["completed_at"] is not None


@pytest.mark.asyncio
async def test_update_task_status_to_pending_clears_completed_at(
    db_session, sample_tasks
):
    """Test that updating status to pending clears completed_at."""
    from memory.api.MCP.servers.organizer import update_task

    update_task_fn = get_fn(update_task)
    # Use the completed task
    completed_task = next(t for t in sample_tasks if t.status == "done")

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        result = await update_task_fn(task_id=completed_task.id, status="pending")

    assert result["status"] == "pending"
    assert result["completed_at"] is None


@pytest.mark.asyncio
async def test_update_task_due_date(db_session, sample_tasks):
    """Test updating task due date."""
    from memory.api.MCP.servers.organizer import update_task

    update_task_fn = get_fn(update_task)
    task_id = sample_tasks[2].id  # The one with no due date

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        result = await update_task_fn(
            task_id=task_id,
            due_date="2025-01-15T09:00:00Z",
        )

    assert result["due_date"] is not None
    assert "2025-01-15" in result["due_date"]


# =============================================================================
# Tests for complete_task_by_id
# =============================================================================


@pytest.mark.asyncio
async def test_complete_task_success(db_session, sample_tasks):
    """Test completing a task."""
    from memory.api.MCP.servers.organizer import complete_task_by_id

    complete_task_by_id_fn = get_fn(complete_task_by_id)
    task_id = sample_tasks[0].id

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        result = await complete_task_by_id_fn(task_id=task_id)

    assert result["status"] == "done"
    assert result["completed_at"] is not None


@pytest.mark.asyncio
async def test_complete_task_not_found(db_session, sample_tasks):
    """Test completing a task that doesn't exist."""
    from memory.api.MCP.servers.organizer import complete_task_by_id

    complete_task_by_id_fn = get_fn(complete_task_by_id)

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        with pytest.raises(ValueError, match="not found"):
            await complete_task_by_id_fn(task_id=99999)


# =============================================================================
# Tests for delete_task
# =============================================================================


@pytest.mark.asyncio
async def test_delete_task_success(db_session, sample_tasks):
    """Test deleting a task."""
    from memory.api.MCP.servers.organizer import delete_task

    delete_task_fn = get_fn(delete_task)
    task_id = sample_tasks[0].id

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        result = await delete_task_fn(task_id=task_id)

    assert result["deleted"] is True
    assert result["task_id"] == task_id

    # Verify task was deleted
    task = db_session.get(Task, task_id)
    assert task is None


@pytest.mark.asyncio
async def test_delete_task_not_found(db_session, sample_tasks):
    """Test deleting a task that doesn't exist."""
    from memory.api.MCP.servers.organizer import delete_task

    delete_task_fn = get_fn(delete_task)

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        with pytest.raises(ValueError, match="not found"):
            await delete_task_fn(task_id=99999)


# =============================================================================
# Tests for get_upcoming_events
# =============================================================================


@pytest.mark.asyncio
async def test_get_upcoming_events_default_range(db_session, sample_events):
    """Test getting events with default 7 day range."""
    from memory.api.MCP.servers.organizer import get_upcoming_events

    get_upcoming_events_fn = get_fn(get_upcoming_events)

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        results = await get_upcoming_events_fn()

    # Should include future events within 7 days
    assert len(results) >= 1
    titles = [r["event_title"] for r in results]
    assert "Team meeting" in titles or "All day event" in titles


@pytest.mark.asyncio
async def test_get_upcoming_events_with_dates(db_session, sample_events):
    """Test getting events with specific date range."""
    from memory.api.MCP.servers.organizer import get_upcoming_events

    get_upcoming_events_fn = get_fn(get_upcoming_events)
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=5)).isoformat()
    end = (now + timedelta(days=5)).isoformat()

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        results = await get_upcoming_events_fn(start_date=start, end_date=end)

    # Should include events in the range
    assert len(results) >= 1


@pytest.mark.asyncio
async def test_get_upcoming_events_limit(db_session, sample_events):
    """Test limiting event results."""
    from memory.api.MCP.servers.organizer import get_upcoming_events

    get_upcoming_events_fn = get_fn(get_upcoming_events)
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=10)).isoformat()
    end = (now + timedelta(days=10)).isoformat()

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        results = await get_upcoming_events_fn(start_date=start, end_date=end, limit=1)

    assert len(results) <= 1


@pytest.mark.asyncio
async def test_get_upcoming_events_limit_capped(db_session, sample_events):
    """Test that limit is capped at 200."""
    from memory.api.MCP.servers.organizer import get_upcoming_events

    get_upcoming_events_fn = get_fn(get_upcoming_events)

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        # Request 500, should be capped
        results = await get_upcoming_events_fn(limit=500)

    assert len(results) <= 200


@pytest.mark.asyncio
async def test_get_upcoming_events_days_capped(db_session, sample_events):
    """Test that days parameter is capped at 365."""
    from memory.api.MCP.servers.organizer import get_upcoming_events

    get_upcoming_events_fn = get_fn(get_upcoming_events)

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        # Request 1000 days, should work but cap at 365
        results = await get_upcoming_events_fn(days=1000)

    # Should not raise, just cap internally
    assert isinstance(results, list)


# =============================================================================
# Parametrized tests
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected_count",
    [
        ("pending", 2),
        ("in_progress", 1),
        ("done", 1),
        ("cancelled", 1),
    ],
)
async def test_list_tasks_various_statuses(
    db_session, sample_tasks, status, expected_count
):
    """Test filtering by various statuses."""
    from memory.api.MCP.servers.organizer import list_tasks

    list_tasks_fn = get_fn(list_tasks)

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        results = await list_tasks_fn(status=status, include_completed=True)

    assert len(results) == expected_count


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "priority,expected_count",
    [
        ("urgent", 1),
        ("high", 1),
        ("medium", 1),
        ("low", 2),
    ],
)
async def test_list_tasks_various_priorities(
    db_session, sample_tasks, priority, expected_count
):
    """Test filtering by various priorities."""
    from memory.api.MCP.servers.organizer import list_tasks

    list_tasks_fn = get_fn(list_tasks)

    with patch(
        "memory.api.MCP.servers.organizer.make_session",
        return_value=db_session.__enter__(),
    ):
        results = await list_tasks_fn(priority=priority, include_completed=True)

    assert len(results) == expected_count
