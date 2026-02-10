# tests/memory/common/db/models/test_scheduled_tasks.py
from datetime import datetime, timezone

from memory.common.db.models.scheduled_tasks import (
    ScheduledTask,
    TaskExecution,
    compute_next_cron,
)


def test_scheduled_task_creation(db_session, sample_user):
    task = ScheduledTask(
        user_id=sample_user.id,
        task_type="notification",
        topic="Test",
        message="Hello",
        notification_channel="discord",
        notification_target="123456",
        next_scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None),
        enabled=True,
    )
    db_session.add(task)
    db_session.commit()

    assert task.id is not None
    assert task.task_type == "notification"
    assert task.enabled is True


def test_task_execution_creation(db_session, sample_user):
    task = ScheduledTask(
        user_id=sample_user.id,
        task_type="notification",
        message="Test message",
        next_scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(task)
    db_session.commit()

    execution = TaskExecution(
        task_id=task.id,
        scheduled_time=task.next_scheduled_time,
        status="pending",
    )
    db_session.add(execution)
    db_session.commit()

    assert execution.id is not None
    assert execution.task_id == task.id
    assert execution.status == "pending"


def test_task_execution_relationship(db_session, sample_user):
    task = ScheduledTask(
        user_id=sample_user.id,
        task_type="notification",
        message="Test",
        next_scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(task)
    db_session.commit()

    exec1 = TaskExecution(
        task_id=task.id,
        scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None),
        status="completed",
    )
    exec2 = TaskExecution(
        task_id=task.id,
        scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None),
        status="pending",
    )
    db_session.add_all([exec1, exec2])
    db_session.commit()

    db_session.refresh(task)
    assert len(task.executions) == 2


def test_compute_next_cron():
    base = datetime(2026, 2, 4, 10, 0, 0)
    next_time = compute_next_cron("0 9 * * *", base)
    assert next_time == datetime(2026, 2, 5, 9, 0, 0)


def test_compute_next_cron_same_day():
    base = datetime(2026, 2, 4, 8, 0, 0)
    next_time = compute_next_cron("0 9 * * *", base)
    assert next_time == datetime(2026, 2, 4, 9, 0, 0)


def test_cascade_delete(db_session, sample_user):
    task = ScheduledTask(
        user_id=sample_user.id,
        task_type="notification",
        message="Test",
        next_scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(task)
    db_session.commit()
    execution = TaskExecution(
        task_id=task.id,
        scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None),
        status="pending",
    )
    db_session.add(execution)
    db_session.commit()
    execution_id = execution.id

    db_session.delete(task)
    db_session.commit()

    assert db_session.get(TaskExecution, execution_id) is None


def test_scheduled_task_serialize(db_session, sample_user):
    """Test that serialize() produces expected dict structure."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    task = ScheduledTask(
        user_id=sample_user.id,
        task_type="notification",
        topic="Test Topic",
        message="Hello World",
        notification_channel="discord",
        notification_target="123456789",
        cron_expression="0 9 * * *",
        next_scheduled_time=now,
        enabled=True,
        data={"key": "value"},
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)

    serialized = task.serialize()

    assert serialized["id"] == task.id
    assert serialized["user_id"] == sample_user.id
    assert serialized["task_type"] == "notification"
    assert serialized["topic"] == "Test Topic"
    assert serialized["message"] == "Hello World"
    assert serialized["notification_channel"] == "discord"
    assert serialized["notification_target"] == "123456789"
    assert serialized["cron_expression"] == "0 9 * * *"
    assert serialized["enabled"] is True
    assert serialized["data"] == {"key": "value"}
    assert serialized["next_scheduled_time"] is not None
    assert serialized["created_at"] is not None


def test_task_execution_serialize(db_session, sample_user):
    """Test that TaskExecution.serialize() produces expected dict structure."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    task = ScheduledTask(
        user_id=sample_user.id,
        task_type="notification",
        message="Test",
        next_scheduled_time=now,
    )
    db_session.add(task)
    db_session.commit()

    execution = TaskExecution(
        task_id=task.id,
        scheduled_time=now,
        started_at=now,
        status="running",
        celery_task_id="celery-task-123",
        data={"attempt": 1},
    )
    db_session.add(execution)
    db_session.commit()
    db_session.refresh(execution)

    serialized = execution.serialize()

    assert serialized["id"] == execution.id
    assert serialized["task_id"] == task.id
    assert serialized["scheduled_time"] is not None
    assert serialized["started_at"] is not None
    assert serialized["finished_at"] is None
    assert serialized["status"] == "running"
    assert serialized["celery_task_id"] == "celery-task-123"
    assert serialized["data"] == {"attempt": 1}


def test_compute_next_cron_no_base_time():
    """Test that compute_next_cron uses current time when base_time is None."""
    # Just verify it doesn't crash and returns a datetime
    next_time = compute_next_cron("0 9 * * *")
    assert isinstance(next_time, datetime)


def test_scheduled_task_default_enabled(db_session, sample_user):
    """Test that enabled defaults to True."""
    task = ScheduledTask(
        user_id=sample_user.id,
        task_type="notification",
        message="Test",
    )
    db_session.add(task)
    db_session.commit()

    assert task.enabled is True


def test_task_execution_default_status(db_session, sample_user):
    """Test that status defaults to 'pending'."""
    task = ScheduledTask(
        user_id=sample_user.id,
        task_type="notification",
        message="Test",
    )
    db_session.add(task)
    db_session.commit()

    execution = TaskExecution(
        task_id=task.id,
        scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(execution)
    db_session.commit()

    assert execution.status == "pending"
