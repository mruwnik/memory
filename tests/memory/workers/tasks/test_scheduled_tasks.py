import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest

from memory.common.db.models import ScheduledTask, TaskExecution
from memory.common.db.models.discord import DiscordChannel, DiscordServer
from memory.workers.tasks import scheduled_tasks


def make_notification_params(channel, target, **kw):
    return scheduled_tasks.NotificationParams(
        notification_channel=channel,
        notification_target=target,
        message=kw.get("message", "hello"),
        user_id=kw.get("user_id", 1),
        topic=kw.get("topic"),
        data=kw.get("data", {}),
    )


@contextmanager
def null_session():
    yield None


# --- delivery-method derivation ---


@pytest.mark.parametrize(
    "target,is_dm",
    [
        ("U12345", True),
        ("W12345", True),
        ("C12345", False),
        ("G12345", False),
        ("D12345", False),
        ("u-lowercase-ok", True),
    ],
)
def test_slack_target_is_dm(target, is_dm):
    assert scheduled_tasks.slack_target_is_dm(target) is is_dm


@pytest.mark.parametrize(
    "target,is_channel",
    [
        ("424242", True),  # known channel id (created below)
        (424242, True),  # int snowflake (celery JSON boundary) ⇒ same answer
        ("999999", False),  # unknown id ⇒ treated as user DM
        (999999, False),  # int unknown id ⇒ user DM, not a crash
        ("not-a-number", False),
    ],
)
def test_discord_target_is_channel(db_session, target, is_channel):
    server = DiscordServer(id=700, name="G", collect_messages=False)
    db_session.add(server)
    db_session.add(
        DiscordChannel(id=424242, server_id=700, name="general", channel_type="text")
    )
    db_session.commit()
    assert scheduled_tasks.discord_target_is_channel(db_session, target) is is_channel


def test_send_via_discord_routes_to_channel(monkeypatch):
    calls = {}
    monkeypatch.setattr(scheduled_tasks, "make_session", null_session)
    monkeypatch.setattr(scheduled_tasks, "resolve_discord_bot_id", lambda s, p: 7)
    monkeypatch.setattr(scheduled_tasks, "discord_target_is_channel", lambda s, t: True)
    monkeypatch.setattr(
        scheduled_tasks.discord_utils,
        "send_to_channel",
        lambda bot, target, msg: calls.update(channel=(bot, target, msg)) or True,
    )
    monkeypatch.setattr(
        scheduled_tasks.discord_utils,
        "send_dm",
        lambda *a: calls.update(dm=a) or True,
    )
    assert (
        scheduled_tasks.send_via_discord(make_notification_params("discord", "555"))
        is True
    )
    assert calls["channel"] == (7, "555", "hello")
    assert "dm" not in calls


def test_send_via_discord_routes_to_dm(monkeypatch):
    calls = {}
    monkeypatch.setattr(scheduled_tasks, "make_session", null_session)
    monkeypatch.setattr(scheduled_tasks, "resolve_discord_bot_id", lambda s, p: 7)
    monkeypatch.setattr(
        scheduled_tasks, "discord_target_is_channel", lambda s, t: False
    )
    monkeypatch.setattr(
        scheduled_tasks.discord_utils,
        "send_to_channel",
        lambda *a: calls.update(channel=a) or True,
    )
    monkeypatch.setattr(
        scheduled_tasks.discord_utils,
        "send_dm",
        lambda bot, target, msg: calls.update(dm=(bot, target, msg)) or True,
    )
    assert (
        scheduled_tasks.send_via_discord(make_notification_params("discord", "778899"))
        is True
    )
    assert calls["dm"] == (7, "778899", "hello")
    assert "channel" not in calls


def test_send_via_discord_normalizes_int_target(monkeypatch):
    # An int snowflake target (raw from serialize() across the celery JSON
    # boundary) must not crash and must reach the collector API as a str.
    calls = {}
    monkeypatch.setattr(scheduled_tasks, "make_session", null_session)
    monkeypatch.setattr(scheduled_tasks, "resolve_discord_bot_id", lambda s, p: 7)
    monkeypatch.setattr(
        scheduled_tasks, "discord_target_is_channel", lambda s, t: False
    )
    monkeypatch.setattr(
        scheduled_tasks.discord_utils,
        "send_dm",
        lambda bot, target, msg: calls.update(dm=(bot, target, msg)) or True,
    )
    assert (
        scheduled_tasks.send_via_discord(make_notification_params("discord", 778899))
        is True
    )
    assert calls["dm"] == (7, "778899", "hello")


@pytest.mark.parametrize(
    "target,expect_open",
    [("U777", True), ("C500", False)],
)
def test_send_via_slack_routes(monkeypatch, target, expect_open):
    methods = []

    creds = Mock(access_token="tok")
    sess = Mock()
    sess.query.return_value.filter.return_value.first.return_value = creds

    @contextmanager
    def fake_session():
        yield sess

    async def fake_call(token, method, **kw):
        methods.append((method, kw))
        if method == "conversations.open":
            return {"channel": {"id": "D-opened"}}
        return {"ok": True}

    monkeypatch.setattr(scheduled_tasks, "make_session", fake_session)
    monkeypatch.setattr(scheduled_tasks, "async_slack_call", fake_call)

    assert (
        scheduled_tasks.send_via_slack(make_notification_params("slack", target))
        is True
    )

    called = [m for m, _ in methods]
    assert ("conversations.open" in called) is expect_open
    post = next(kw for m, kw in methods if m == "chat.postMessage")
    assert post["channel"] == ("D-opened" if expect_open else target)


@pytest.fixture
def pending_scheduled_task(db_session, sample_user):
    """Create a pending scheduled task for testing."""
    task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        task_type="notification",
        topic="Test Topic",
        next_scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None)
        - timedelta(minutes=5),
        message="What is the weather like today?",
        notification_channel="discord",
        notification_target="123456789",
        enabled=True,
    )
    db_session.add(task)
    db_session.commit()
    return task


@pytest.fixture
def pending_execution(db_session, pending_scheduled_task):
    """Create a pending execution for the pending scheduled task."""
    execution = TaskExecution(
        id=str(uuid.uuid4()),
        task_id=pending_scheduled_task.id,
        scheduled_time=pending_scheduled_task.next_scheduled_time,
        status="pending",
    )
    db_session.add(execution)
    db_session.commit()
    return execution


@pytest.fixture
def completed_scheduled_task(db_session, sample_user):
    """Create a completed scheduled task for testing."""
    task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        task_type="notification",
        topic="Completed Topic",
        next_scheduled_time=None,  # No next run since it's completed
        message="Tell me a joke.",
        notification_channel="slack",
        notification_target="U12345678",
        enabled=True,
    )
    db_session.add(task)
    db_session.commit()
    return task


@pytest.fixture
def completed_execution(db_session, completed_scheduled_task):
    """Create a completed execution for the completed scheduled task."""
    execution = TaskExecution(
        id=str(uuid.uuid4()),
        task_id=completed_scheduled_task.id,
        scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None)
        - timedelta(hours=1),
        started_at=datetime.now(timezone.utc).replace(tzinfo=None)
        - timedelta(minutes=30),
        finished_at=datetime.now(timezone.utc).replace(tzinfo=None)
        - timedelta(minutes=29),
        status="completed",
        response="Why did the chicken cross the road? To get to the other side!",
    )
    db_session.add(execution)
    db_session.commit()
    return execution


@pytest.fixture
def future_scheduled_task(db_session, sample_user):
    """Create a future scheduled task for testing."""
    task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        task_type="notification",
        topic="Future Topic",
        next_scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None)
        + timedelta(hours=1),
        message="What will happen tomorrow?",
        notification_channel="email",
        notification_target="user@example.com",
        enabled=True,
    )
    db_session.add(task)
    db_session.commit()
    return task


@patch("memory.workers.tasks.scheduled_tasks.deliver_notification")
def test_execute_scheduled_task_success(
    mock_deliver_notification, pending_execution, db_session
):
    """Test successful execution of a scheduled task."""
    mock_deliver_notification.return_value = True

    result = scheduled_tasks.execute_scheduled_task(pending_execution.id)

    # Verify result
    assert result["success"] is True
    assert result["execution_id"] == pending_execution.id
    assert result["task_type"] == "notification"

    # Verify database was updated
    db_session.refresh(pending_execution)
    assert pending_execution.status == "completed"


def test_execute_scheduled_task_not_found(db_session):
    """Test execution with non-existent execution ID."""
    fake_id = str(uuid.uuid4())

    result = scheduled_tasks.execute_scheduled_task(fake_id)

    assert result == {"error": "Execution not found"}


def test_execute_scheduled_task_not_pending(completed_execution, db_session):
    """Test execution of an execution that is not pending."""
    result = scheduled_tasks.execute_scheduled_task(completed_execution.id)

    assert result == {
        "error": f"Execution is not pending (status: {completed_execution.status})"
    }


@patch("memory.workers.tasks.scheduled_tasks.deliver_notification")
def test_execute_scheduled_task_with_no_message(
    mock_deliver_notification, db_session, sample_user
):
    """Test execution when message is None, should use empty string."""
    mock_deliver_notification.return_value = True

    # Create task without message
    task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        task_type="notification",
        topic="No Message",
        next_scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None)
        - timedelta(minutes=5),
        message=None,
        notification_channel="discord",
        notification_target="123456789",
        enabled=True,
    )
    db_session.add(task)
    db_session.commit()

    execution = TaskExecution(
        id=str(uuid.uuid4()),
        task_id=task.id,
        scheduled_time=task.next_scheduled_time,
        status="pending",
    )
    db_session.add(execution)
    db_session.commit()

    result = scheduled_tasks.execute_scheduled_task(execution.id)

    assert result["success"] is True


@patch("memory.workers.tasks.scheduled_tasks.deliver_notification")
def test_execute_scheduled_task_send_failure(
    mock_deliver_notification, pending_execution, db_session
):
    """Test execution when notification sending fails."""
    mock_deliver_notification.return_value = False

    result = scheduled_tasks.execute_scheduled_task(pending_execution.id)

    # Should fail since sending failed
    assert result["success"] is False

    # Verify the execution was marked as failed
    db_session.refresh(pending_execution)
    assert pending_execution.status == "failed"
    assert pending_execution.error_message == "Failed to send notification"


@patch("memory.workers.tasks.scheduled_tasks.deliver_notification")
def test_execute_scheduled_task_send_exception(
    mock_deliver_notification, pending_execution, db_session
):
    """Test execution when notification sending raises an exception."""
    mock_deliver_notification.side_effect = Exception("Network error")

    result = scheduled_tasks.execute_scheduled_task(pending_execution.id)

    # Should fail since sending raised exception
    assert result["success"] is False

    # Verify the execution was marked as failed with error message
    db_session.refresh(pending_execution)
    assert pending_execution.status == "failed"
    assert "Network error" in pending_execution.error_message


@patch("memory.workers.tasks.scheduled_tasks.deliver_notification")
def test_execute_scheduled_task_long_message(
    mock_deliver_notification, db_session, sample_user
):
    """Test execution with a long message."""
    mock_deliver_notification.return_value = True

    long_message = "A" * 500  # Long message
    task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        task_type="notification",
        topic="Long Message",
        next_scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None)
        - timedelta(minutes=5),
        message=long_message,
        notification_channel="discord",
        notification_target="123456789",
        enabled=True,
    )
    db_session.add(task)
    db_session.commit()

    execution = TaskExecution(
        id=str(uuid.uuid4()),
        task_id=task.id,
        scheduled_time=task.next_scheduled_time,
        status="pending",
    )
    db_session.add(execution)
    db_session.commit()

    result = scheduled_tasks.execute_scheduled_task(execution.id)

    assert result["success"] is True
    assert result["task_type"] == "notification"

    # deliver_notification was called
    mock_deliver_notification.assert_called_once()


@patch("memory.workers.tasks.scheduled_tasks.app.send_task")
def test_run_scheduled_tasks_with_due_tasks(mock_delay, db_session, sample_user):
    """Test running scheduled tasks with due tasks."""
    # Create multiple due tasks
    due_task1 = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        task_type="notification",
        next_scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None)
        - timedelta(minutes=10),
        message="Test 1",
        notification_channel="discord",
        notification_target="123456789",
        enabled=True,
    )
    due_task2 = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        task_type="notification",
        next_scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None)
        - timedelta(minutes=5),
        message="Test 2",
        notification_channel="slack",
        notification_target="U12345678",
        enabled=True,
    )

    db_session.add_all([due_task1, due_task2])
    db_session.commit()

    mock_task = Mock()
    mock_task.id = "task-123"
    mock_delay.return_value = mock_task

    result = scheduled_tasks.run_scheduled_tasks()

    assert result["count"] == 2
    assert len(result["executions"]) == 2

    # Verify execute_scheduled_task.delay was called for both executions
    assert mock_delay.call_count == 2


@patch("memory.workers.tasks.scheduled_tasks.app.send_task")
def test_run_scheduled_tasks_no_due_tasks(
    mock_delay, future_scheduled_task, db_session
):
    """Test running scheduled tasks when no tasks are due."""
    result = scheduled_tasks.run_scheduled_tasks()

    assert result["count"] == 0
    assert result["executions"] == []

    # No tasks should be scheduled
    mock_delay.assert_not_called()


@patch("memory.workers.tasks.scheduled_tasks.app.send_task")
def test_run_scheduled_tasks_skips_tasks_with_no_next_time(
    mock_delay, db_session, sample_user
):
    """A task with next_scheduled_time=None (disabled / fired one-off) is skipped."""
    task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        task_type="notification",
        next_scheduled_time=None,
        message="Inert",
        notification_channel="discord",
        notification_target="123456789",
    )
    db_session.add(task)
    db_session.commit()

    result = scheduled_tasks.run_scheduled_tasks()

    assert result["count"] == 0
    mock_delay.assert_not_called()


@patch("memory.workers.tasks.scheduled_tasks.app.send_task")
def test_run_scheduled_tasks_disabled_task(mock_delay, db_session, sample_user):
    """Test that disabled tasks are not processed.

    Under the derived-``enabled`` model, a disabled task is one with
    ``next_scheduled_time = None`` (no pending run), so it is never due.
    """
    disabled_task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        task_type="notification",
        next_scheduled_time=None,
        message="Disabled",
        notification_channel="discord",
        notification_target="123456789",
    )

    db_session.add(disabled_task)
    db_session.commit()

    result = scheduled_tasks.run_scheduled_tasks()

    # The disabled task should not be processed
    assert result["count"] == 0
    assert result["executions"] == []

    mock_delay.assert_not_called()


@patch("memory.workers.tasks.scheduled_tasks.app.send_task")
def test_run_scheduled_tasks_timezone_handling(mock_delay, db_session, sample_user):
    """Test that timezone handling works correctly."""
    # Create a task that's due (scheduled time in the past)
    past_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)
    due_task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        task_type="notification",
        next_scheduled_time=past_time,
        message="Due task",
        notification_channel="discord",
        notification_target="123456789",
        enabled=True,
    )

    # Create a task that's not due yet
    future_time = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5)
    future_task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        task_type="notification",
        next_scheduled_time=future_time,
        message="Future task",
        notification_channel="discord",
        notification_target="123456789",
        enabled=True,
    )

    db_session.add_all([due_task, future_task])
    db_session.commit()

    mock_task = Mock()
    mock_task.id = "task-123"
    mock_delay.return_value = mock_task

    result = scheduled_tasks.run_scheduled_tasks()

    # Only the due task should be processed
    assert result["count"] == 1

    mock_delay.assert_called_once()


@patch("memory.workers.tasks.scheduled_tasks.deliver_notification")
def test_status_transition_pending_to_completed(
    mock_deliver_notification, pending_execution, db_session
):
    """Test that status transitions correctly during execution."""
    mock_deliver_notification.return_value = True

    # Initial status should be pending
    assert pending_execution.status == "pending"

    scheduled_tasks.execute_scheduled_task(pending_execution.id)

    # Final status should be completed
    db_session.refresh(pending_execution)
    assert pending_execution.status == "completed"


@patch("memory.workers.tasks.scheduled_tasks.deliver_notification")
def test_execute_scheduled_task_pending_executes(
    mock_deliver_notification, db_session, sample_user
):
    """Test that pending executions are executed successfully."""
    mock_deliver_notification.return_value = True

    task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        task_type="notification",
        topic="Status Test",
        next_scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None)
        - timedelta(minutes=5),
        message="Test",
        notification_channel="discord",
        notification_target="123456789",
        enabled=True,
    )
    db_session.add(task)
    db_session.commit()

    execution = TaskExecution(
        id=str(uuid.uuid4()),
        task_id=task.id,
        scheduled_time=task.next_scheduled_time,
        status="pending",
    )
    db_session.add(execution)
    db_session.commit()

    result = scheduled_tasks.execute_scheduled_task(execution.id)

    assert result["success"] is True


@pytest.mark.parametrize("status", ["running", "completed", "failed"])
def test_execute_scheduled_task_non_pending_rejected(status, db_session, sample_user):
    """Test that non-pending executions are rejected."""
    task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        task_type="notification",
        topic="Status Test",
        next_scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None)
        - timedelta(minutes=5),
        message="Test",
        notification_channel="discord",
        notification_target="123456789",
        enabled=True,
    )
    db_session.add(task)
    db_session.commit()

    execution = TaskExecution(
        id=str(uuid.uuid4()),
        task_id=task.id,
        scheduled_time=task.next_scheduled_time,
        status=status,
    )
    db_session.add(execution)
    db_session.commit()

    result = scheduled_tasks.execute_scheduled_task(execution.id)

    assert result == {"error": f"Execution is not pending (status: {status})"}


def test_extract_notification_params_no_channel(db_session, sample_user):
    """Test extract_notification_params returns None when no channel_type is set."""
    task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        task_type="notification",
        next_scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None),
        message="Test",
        notification_channel=None,
        notification_target="123456789",
        enabled=True,
    )
    db_session.add(task)
    db_session.commit()

    result = scheduled_tasks.extract_notification_params(task)

    assert result is None


def test_extract_notification_params_no_target(db_session, sample_user):
    """Test extract_notification_params returns None when no target is set."""
    task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        task_type="notification",
        next_scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None),
        message="Test",
        notification_channel="discord",
        notification_target=None,
        enabled=True,
    )
    db_session.add(task)
    db_session.commit()

    result = scheduled_tasks.extract_notification_params(task)

    assert result is None


def test_deliver_notification_unknown_channel(caplog):
    """Test deliver_notification returns False for unknown notification_channel."""
    params = scheduled_tasks.NotificationParams(
        notification_channel="sms",  # Not supported
        notification_target="123456789",
        message="Test",
        user_id=1,
        topic=None,
        data={},
    )

    result = scheduled_tasks.deliver_notification(params)

    assert result is False
    assert "Unknown notification_channel: sms" in caplog.text


@patch("memory.workers.tasks.scheduled_tasks.send_via_discord")
def test_deliver_notification_routes_to_discord(mock_send_discord):
    """Test deliver_notification routes Discord channel to send_via_discord."""
    mock_send_discord.return_value = True

    params = scheduled_tasks.NotificationParams(
        notification_channel="discord",
        notification_target="123456789",
        message="Test",
        user_id=1,
        topic=None,
        data={},
    )

    result = scheduled_tasks.deliver_notification(params)

    assert result is True
    mock_send_discord.assert_called_once_with(params)


@patch("memory.workers.tasks.scheduled_tasks.send_via_slack")
def test_deliver_notification_routes_to_slack(mock_send_slack):
    """Test deliver_notification routes Slack channel to send_via_slack."""
    mock_send_slack.return_value = True

    params = scheduled_tasks.NotificationParams(
        notification_channel="slack",
        notification_target="U12345678",
        message="Test",
        user_id=1,
        topic=None,
        data={},
    )

    result = scheduled_tasks.deliver_notification(params)

    assert result is True
    mock_send_slack.assert_called_once_with(params)


@patch("memory.workers.tasks.scheduled_tasks.send_via_email")
def test_deliver_notification_routes_to_email(mock_send_email):
    """Test deliver_notification routes email channel to send_via_email."""
    mock_send_email.return_value = True

    params = scheduled_tasks.NotificationParams(
        notification_channel="email",
        notification_target="user@example.com",
        message="Test",
        user_id=1,
        topic=None,
        data={},
    )

    result = scheduled_tasks.deliver_notification(params)

    assert result is True
    mock_send_email.assert_called_once_with(params)


@patch("memory.workers.tasks.scheduled_tasks.app.send_task")
def test_run_scheduled_tasks_updates_next_scheduled_time_for_recurring(
    mock_delay, db_session, sample_user
):
    """Test that recurring tasks get their next_scheduled_time updated."""
    # Create a recurring task that's due
    task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        task_type="notification",
        next_scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None)
        - timedelta(minutes=5),
        message="Recurring",
        notification_channel="discord",
        notification_target="123456789",
        cron_expression="0 9 * * *",  # Every day at 9am
        enabled=True,
    )
    db_session.add(task)
    db_session.commit()

    old_next_time = task.next_scheduled_time
    assert old_next_time is not None

    mock_task = Mock()
    mock_task.id = "task-123"
    mock_delay.return_value = mock_task

    scheduled_tasks.run_scheduled_tasks()

    # Verify next_scheduled_time was updated
    db_session.refresh(task)
    assert task.next_scheduled_time is not None
    assert task.next_scheduled_time > old_next_time


@patch("memory.workers.tasks.scheduled_tasks.app.send_task")
def test_run_scheduled_tasks_clears_next_scheduled_time_for_one_time(
    mock_delay, db_session, sample_user
):
    """Test that one-time tasks get their next_scheduled_time cleared."""
    # Create a one-time task that's due
    task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        task_type="notification",
        next_scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None)
        - timedelta(minutes=5),
        message="One-time",
        notification_channel="discord",
        notification_target="123456789",
        cron_expression=None,  # No cron = one-time
        enabled=True,
    )
    db_session.add(task)
    db_session.commit()

    mock_task = Mock()
    mock_task.id = "task-123"
    mock_delay.return_value = mock_task

    scheduled_tasks.run_scheduled_tasks()

    # Verify next_scheduled_time was cleared
    db_session.refresh(task)
    assert task.next_scheduled_time is None


@patch("memory.workers.tasks.scheduled_tasks.app.send_task")
def test_run_scheduled_tasks_recovers_stale_executions(
    mock_delay, db_session, sample_user
):
    """Test that stale running executions are marked as failed."""
    # Create a task with a stale "running" execution (stuck for 3 hours)
    task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        task_type="notification",
        next_scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None)
        + timedelta(hours=1),  # Future
        message="Test",
        notification_channel="discord",
        notification_target="123456789",
        enabled=True,
    )
    db_session.add(task)
    db_session.commit()

    # Create a stale execution that's been "running" for 3 hours
    stale_execution = TaskExecution(
        id=str(uuid.uuid4()),
        task_id=task.id,
        scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None)
        - timedelta(hours=3),
        started_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3),
        status="running",
    )
    db_session.add(stale_execution)
    db_session.commit()

    result = scheduled_tasks.run_scheduled_tasks()

    # The stale execution should be recovered
    assert result["recovered_stale"] == 1

    # Verify the stale execution was marked as failed
    db_session.refresh(stale_execution)
    assert stale_execution.status == "failed"
    assert stale_execution.error_message is not None
    assert "stale" in stale_execution.error_message.lower()
    assert stale_execution.finished_at is not None


@patch("memory.workers.tasks.scheduled_tasks.app.send_task")
def test_run_scheduled_tasks_skips_task_with_pending_execution(
    mock_delay, db_session, sample_user
):
    """Test that due tasks with pending executions are not re-dispatched."""
    # Create a due task
    past_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)
    task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        task_type="notification",
        next_scheduled_time=past_time,
        message="Test",
        notification_channel="discord",
        notification_target="123456789",
        enabled=True,
    )
    db_session.add(task)
    db_session.commit()

    # Create an existing pending execution for this task
    existing_execution = TaskExecution(
        id=str(uuid.uuid4()),
        task_id=task.id,
        scheduled_time=past_time,
        status="pending",
    )
    db_session.add(existing_execution)
    db_session.commit()

    result = scheduled_tasks.run_scheduled_tasks()

    # No new executions should be created since one is already pending
    assert result["count"] == 0
    assert len(result["executions"]) == 0

    # Verify only one execution exists for this task
    executions = (
        db_session.query(TaskExecution).filter(TaskExecution.task_id == task.id).all()
    )
    assert len(executions) == 1
    assert executions[0].id == existing_execution.id


@patch("memory.workers.tasks.scheduled_tasks.app.send_task")
def test_run_scheduled_tasks_recovers_stuck_pending_executions(
    mock_delay, db_session, sample_user
):
    """Test that stuck pending executions are re-dispatched."""
    # Create a task
    task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        task_type="notification",
        next_scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None)
        + timedelta(hours=1),  # Future
        message="Test",
        notification_channel="discord",
        notification_target="123456789",
        enabled=True,
    )
    db_session.add(task)
    db_session.commit()

    # Create a stuck pending execution (pending for over 30 minutes without being picked up)
    stuck_pending = TaskExecution(
        id=str(uuid.uuid4()),
        task_id=task.id,
        scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None)
        - timedelta(hours=1),
        status="pending",
        started_at=None,  # Never started
    )
    db_session.add(stuck_pending)
    db_session.commit()

    result = scheduled_tasks.run_scheduled_tasks()

    # The stuck pending execution should be re-dispatched
    assert result["recovered_pending"] == 1

    # Verify the execution was re-dispatched (send_task called with its ID)
    from memory.common.celery_app import EXECUTE_SCHEDULED_TASK

    mock_delay.assert_any_call(EXECUTE_SCHEDULED_TASK, args=[stuck_pending.id])


# --- Claude session spawning tests ---


@pytest.fixture
def claude_session_task(db_session, sample_user):
    """Create a scheduled task for Claude session spawning."""
    task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        task_type="claude_session",
        topic="Daily review",
        data={
            "spawn_config": {
                "environment_id": 1,
                "initial_prompt": "Review the latest changes",
            }
        },
        cron_expression="0 9 * * *",
        next_scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None)
        - timedelta(minutes=5),
        enabled=True,
    )
    db_session.add(task)
    db_session.commit()
    return task


@patch("memory.workers.tasks.scheduled_tasks.requests.post")
def test_spawn_claude_session_success(mock_post, claude_session_task, db_session):
    """Test successful spawning of a Claude session via API."""
    # Mock the API response
    mock_response = Mock()
    mock_response.ok = True
    mock_response.json.return_value = {"session_id": "u1-e1-abc123"}
    mock_post.return_value = mock_response

    result = scheduled_tasks.spawn_claude_session(claude_session_task, db=db_session)

    assert result == "u1-e1-abc123"
    mock_post.assert_called_once()
    url_arg = mock_post.call_args.args[0]
    assert "/claude/spawn" in url_arg
    assert "Authorization" in mock_post.call_args.kwargs.get("headers", {})


def test_spawn_claude_session_missing_config(db_session, sample_user):
    """Test that missing spawn_config raises ValueError."""
    task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        task_type="claude_session",
        topic="No config",
        data={},
        enabled=True,
    )
    db_session.add(task)
    db_session.commit()

    with pytest.raises(ValueError, match="Missing spawn_config"):
        scheduled_tasks.spawn_claude_session(task, db=db_session)


@patch("memory.workers.tasks.scheduled_tasks.requests.post")
def test_spawn_claude_session_run_id_suffixed(mock_post, db_session, sample_user):
    """Test that run_id gets a timestamp suffix appended."""
    task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        task_type="claude_session",
        topic="Daily review",
        data={
            "spawn_config": {
                "environment_id": 1,
                "initial_prompt": "test",
                "run_id": "daily-review",
            }
        },
        enabled=True,
    )
    db_session.add(task)
    db_session.commit()

    mock_response = Mock()
    mock_response.ok = True
    mock_response.json.return_value = {"session_id": "u1-e1-abc123"}
    mock_post.return_value = mock_response

    scheduled_tasks.spawn_claude_session(task, db=db_session)

    posted_json = mock_post.call_args.kwargs["json"]
    assert posted_json["run_id"].startswith("daily-review-")
    # Should have YYYYMMDD-HHMMSS suffix
    assert re.match(r"daily-review-\d{8}-\d{6}", posted_json["run_id"])


@patch("memory.workers.tasks.scheduled_tasks.requests.post")
def test_spawn_claude_session_does_not_mutate_task_data(
    mock_post, db_session, sample_user
):
    """Test that spawn_claude_session doesn't mutate task.data in place.

    The original run_id in the task's data must remain unchanged so that
    subsequent cron executions don't accumulate timestamp suffixes.
    """
    original_run_id = "daily-review"
    task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        task_type="claude_session",
        topic="Daily review",
        data={
            "spawn_config": {
                "environment_id": 1,
                "initial_prompt": "test",
                "run_id": original_run_id,
            }
        },
        enabled=True,
    )
    db_session.add(task)
    db_session.commit()

    mock_response = Mock()
    mock_response.ok = True
    mock_response.json.return_value = {"session_id": "u1-e1-abc123"}
    mock_post.return_value = mock_response

    scheduled_tasks.spawn_claude_session(task, db=db_session)

    # The original task data must NOT have been mutated
    assert task.data is not None
    assert task.data["spawn_config"]["run_id"] == original_run_id


@patch("memory.workers.tasks.scheduled_tasks.requests.post")
def test_spawn_claude_session_passes_enable_playwright(
    mock_post, db_session, sample_user
):
    """Test that enable_playwright is passed through to the spawn API call."""
    task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        task_type="claude_session",
        topic="Playwright session",
        data={
            "spawn_config": {
                "environment_id": 1,
                "initial_prompt": "test with playwright",
                "enable_playwright": True,
            }
        },
        enabled=True,
    )
    db_session.add(task)
    db_session.commit()

    mock_response = Mock()
    mock_response.ok = True
    mock_response.json.return_value = {"session_id": "u1-e1-abc123"}
    mock_post.return_value = mock_response

    scheduled_tasks.spawn_claude_session(task, db=db_session)

    posted_json = mock_post.call_args.kwargs["json"]
    assert posted_json["enable_playwright"] is True


@patch("memory.workers.tasks.scheduled_tasks.requests.post")
def test_spawn_claude_session_api_failure(mock_post, claude_session_task, db_session):
    """Test that API failure raises ValueError."""
    mock_response = Mock()
    mock_response.ok = False
    mock_response.status_code = 503
    mock_response.text = "Orchestrator unavailable"
    mock_post.return_value = mock_response

    with pytest.raises(ValueError, match="API returned 503"):
        scheduled_tasks.spawn_claude_session(claude_session_task, db=db_session)


# ----- run_id sanitization regression tests -----


def test_sanitize_slug_strips_shell_metachars():
    """Direct test of the helper used by spawn_claude_session."""
    assert scheduled_tasks.sanitize_slug("normal-name") == "normal-name"
    # Path traversal segments get hyphenated.
    assert scheduled_tasks.sanitize_slug("../../etc/passwd") == "etc-passwd"
    # Spaces become hyphens.
    assert scheduled_tasks.sanitize_slug("branch with spaces") == "branch-with-spaces"
    # Lowercase.
    assert scheduled_tasks.sanitize_slug("UPPER") == "upper"
    # All-punctuation strips to empty.
    assert scheduled_tasks.sanitize_slug("...") == ""


@patch("memory.workers.tasks.scheduled_tasks.requests.post")
def test_spawn_claude_session_sanitises_user_run_id(mock_post, db_session, sample_user):
    """User-supplied run_id used to flow through verbatim, which would let
    a scheduler caller embed shell metacharacters or path-traversal
    segments into the CLAUDE_RUN_ID env var (and from there into the
    container entrypoint's branch-creation step). Pin that the same
    `sanitize_slug` applied to the task.topic path is now applied to
    user-supplied run_id too."""
    task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        task_type="claude_session",
        topic="Daily review",
        data={
            "spawn_config": {
                "environment_id": 1,
                "initial_prompt": "test",
                "run_id": "../../etc/passwd",
            }
        },
        enabled=True,
    )
    db_session.add(task)
    db_session.commit()

    mock_response = Mock()
    mock_response.ok = True
    mock_response.json.return_value = {"session_id": "u1-e1-abc123"}
    mock_post.return_value = mock_response

    scheduled_tasks.spawn_claude_session(task, db=db_session)

    posted_run_id = mock_post.call_args.kwargs["json"]["run_id"]
    # Path-traversal characters must be gone.
    assert ".." not in posted_run_id
    assert "/" not in posted_run_id
    # No leading hyphen — would be interpreted as a flag.
    assert not posted_run_id.startswith("-")
    # Still has the timestamp suffix.
    assert re.search(r"-\d{8}-\d{6}$", posted_run_id)


@patch("memory.workers.tasks.scheduled_tasks.requests.post")
def test_spawn_claude_session_run_id_falls_back_when_slug_empty(
    mock_post, db_session, sample_user
):
    """A run_id consisting entirely of punctuation strips to "" and would
    yield a leading-hyphen branch name that the git CLI rejects. Falls
    back to the task-id stub instead."""
    task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        task_type="claude_session",
        topic="Daily review",
        data={
            "spawn_config": {
                "environment_id": 1,
                "initial_prompt": "test",
                "run_id": "...",  # all punctuation
            }
        },
        enabled=True,
    )
    db_session.add(task)
    db_session.commit()

    mock_response = Mock()
    mock_response.ok = True
    mock_response.json.return_value = {"session_id": "u1-e1-abc123"}
    mock_post.return_value = mock_response

    scheduled_tasks.spawn_claude_session(task, db=db_session)

    posted_run_id = mock_post.call_args.kwargs["json"]["run_id"]
    # Falls back to task-<task-id-stub>-<timestamp>
    assert posted_run_id.startswith("task-")
    assert not posted_run_id.startswith("-")
    assert re.search(r"-\d{8}-\d{6}$", posted_run_id)


@patch("memory.workers.tasks.scheduled_tasks.deliver_notification")
def test_send_notification_task_delivers_without_db(mock_deliver, sample_user):
    mock_deliver.return_value = True
    result = scheduled_tasks.send_notification(
        channel="discord",
        target="123456789",
        message="**Hi**\n\nthere",
        user_id=sample_user.id,
        topic="Hi",
        data={"discord_bot_id": 7},
    )
    assert result == {"sent": True}
    params = mock_deliver.call_args.args[0]
    assert params.notification_channel == "discord"
    assert params.notification_target == "123456789"
    assert params.message == "**Hi**\n\nthere"
    assert params.user_id == sample_user.id
    assert params.data == {"discord_bot_id": 7}
