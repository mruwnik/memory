# tests/memory/common/db/models/test_scheduled_tasks.py
from datetime import datetime, timezone

from memory.common.db.models.scheduled_tasks import (
    ScheduledTask,
    TaskExecution,
    compute_next_cron,
    iso_utc,
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
    """Without a next_scheduled_time, a task has no pending run and is disabled."""
    task = ScheduledTask(
        user_id=sample_user.id,
        task_type="notification",
        message="Test",
    )
    db_session.add(task)
    db_session.commit()

    assert task.enabled is False


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


def test_iso_utc_appends_z_to_naive_datetime():
    # Stored datetimes are naive but semantically UTC
    naive = datetime(2026, 5, 29, 9, 0, 0)
    assert iso_utc(naive) == "2026-05-29T09:00:00Z"


def test_iso_utc_none_returns_none():
    assert iso_utc(None) is None


def test_iso_utc_aware_datetime_keeps_offset():
    aware = datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc)
    # Already unambiguous; do not append a second marker
    assert iso_utc(aware) == "2026-05-29T09:00:00+00:00"


def test_serialize_next_scheduled_time_is_utc_marked(db_session, sample_user):
    task = ScheduledTask(
        user_id=sample_user.id,
        task_type="notification",
        next_scheduled_time=datetime(2026, 5, 29, 9, 0, 0),
    )
    db_session.add(task)
    db_session.commit()
    assert task.serialize()["next_scheduled_time"] == "2026-05-29T09:00:00Z"


def test_enabled_true_when_next_scheduled_time_set(db_session, sample_user):
    task = ScheduledTask(
        user_id=sample_user.id,
        task_type="notification",
        next_scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    assert task.enabled is True


def test_enabled_false_when_next_scheduled_time_none(db_session, sample_user):
    task = ScheduledTask(
        user_id=sample_user.id,
        task_type="notification",
        next_scheduled_time=None,
    )
    assert task.enabled is False


def test_setting_enabled_false_clears_next_scheduled_time(db_session, sample_user):
    task = ScheduledTask(
        user_id=sample_user.id,
        task_type="notification",
        next_scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    task.enabled = False
    assert task.next_scheduled_time is None
    assert task.enabled is False


def test_setting_enabled_true_recomputes_recurring(db_session, sample_user):
    task = ScheduledTask(
        user_id=sample_user.id,
        task_type="notification",
        cron_expression="0 9 * * *",
        next_scheduled_time=None,
    )
    task.enabled = True
    assert task.next_scheduled_time is not None
    assert task.enabled is True


def test_setting_enabled_true_oneoff_is_noop(db_session, sample_user):
    # A fired one-off (no cron, no next) cannot be resurrected
    task = ScheduledTask(
        user_id=sample_user.id,
        task_type="notification",
        cron_expression=None,
        next_scheduled_time=None,
    )
    task.enabled = True
    assert task.next_scheduled_time is None
    assert task.enabled is False


def test_enabled_query_expression_filters(db_session, sample_user):
    active = ScheduledTask(
        user_id=sample_user.id, task_type="notification",
        next_scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    inactive = ScheduledTask(
        user_id=sample_user.id, task_type="notification",
        next_scheduled_time=None,
    )
    db_session.add_all([active, inactive])
    db_session.commit()

    enabled_ids = {
        t.id for t in db_session.query(ScheduledTask).filter(ScheduledTask.enabled).all()
    }
    assert active.id in enabled_ids
    assert inactive.id not in enabled_ids
