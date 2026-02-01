import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch
import uuid

from memory.common.db.models import (
    ScheduledLLMCall,
    HumanUser,
)
from memory.workers.tasks import scheduled_calls


@pytest.fixture
def sample_user(db_session):
    """Create a sample user for testing."""
    user = HumanUser.create_with_password(
        email="testuser@example.com",
        name="Test User",
        password="testpassword123",
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def pending_scheduled_call(db_session, sample_user):
    """Create a pending scheduled call for testing."""
    call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        topic="Test Topic",
        scheduled_time=datetime.now(timezone.utc) - timedelta(minutes=5),
        model=None,  # No model means message returned as-is
        message="What is the weather like today?",
        system_prompt="You are a helpful assistant.",
        channel_type="discord",
        channel_identifier="123456789",
        status="pending",
    )
    db_session.add(call)
    db_session.commit()
    return call


@pytest.fixture
def completed_scheduled_call(db_session, sample_user):
    """Create a completed scheduled call for testing."""
    call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        topic="Completed Topic",
        scheduled_time=datetime.now(timezone.utc) - timedelta(hours=1),
        executed_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        model=None,
        message="Tell me a joke.",
        system_prompt="You are a funny assistant.",
        channel_type="slack",
        channel_identifier="U12345678",
        status="completed",
        response="Why did the chicken cross the road? To get to the other side!",
    )
    db_session.add(call)
    db_session.commit()
    return call


@pytest.fixture
def future_scheduled_call(db_session, sample_user):
    """Create a future scheduled call for testing."""
    call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        topic="Future Topic",
        scheduled_time=datetime.now(timezone.utc) + timedelta(hours=1),
        model=None,
        message="What will happen tomorrow?",
        channel_type="email",
        channel_identifier="user@example.com",
        status="pending",
    )
    db_session.add(call)
    db_session.commit()
    return call


@patch("memory.workers.tasks.scheduled_calls.send_message")
def test_execute_scheduled_call_success(
    mock_send_message, pending_scheduled_call, db_session
):
    """Test successful execution of a scheduled LLM call."""
    mock_send_message.return_value = True

    result = scheduled_calls.execute_scheduled_call(pending_scheduled_call.id)

    # Verify result
    assert result["success"] is True
    assert result["scheduled_call_id"] == pending_scheduled_call.id
    assert result["channel_type"] == "discord"

    # Verify database was updated
    db_session.refresh(pending_scheduled_call)
    assert pending_scheduled_call.status == "completed"


def test_execute_scheduled_call_not_found(db_session):
    """Test execution with non-existent call ID."""
    fake_id = str(uuid.uuid4())

    result = scheduled_calls.execute_scheduled_call(fake_id)

    assert result == {"error": "Scheduled call not found"}


def test_execute_scheduled_call_not_pending(completed_scheduled_call, db_session):
    """Test execution of a call that is not pending or queued."""
    result = scheduled_calls.execute_scheduled_call(completed_scheduled_call.id)

    # The current implementation checks for "pending" or "queued"
    assert result == {"error": "Call is not ready (status: completed)"}


@patch("memory.workers.tasks.scheduled_calls.send_message")
def test_execute_scheduled_call_with_default_system_prompt(
    mock_send_message, db_session, sample_user
):
    """Test execution when system_prompt is None, should use default."""
    mock_send_message.return_value = True

    # Create call without system prompt
    call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        topic="No System Prompt",
        scheduled_time=datetime.now(timezone.utc) - timedelta(minutes=5),
        model=None,
        message="Test prompt",
        system_prompt=None,
        channel_type="discord",
        channel_identifier="123456789",
        status="pending",
    )
    db_session.add(call)
    db_session.commit()

    result = scheduled_calls.execute_scheduled_call(call.id)

    assert result["success"] is True


@patch("memory.workers.tasks.scheduled_calls.send_message")
def test_execute_scheduled_call_send_failure(
    mock_send_message, pending_scheduled_call, db_session
):
    """Test execution when message sending fails."""
    mock_send_message.return_value = False

    result = scheduled_calls.execute_scheduled_call(pending_scheduled_call.id)

    # Should fail since sending failed
    assert result["success"] is False

    # Verify the call was marked as failed
    db_session.refresh(pending_scheduled_call)
    assert pending_scheduled_call.status == "failed"
    assert pending_scheduled_call.error_message == "Failed to send message"


@patch("memory.workers.tasks.scheduled_calls.send_message")
def test_execute_scheduled_call_send_exception(
    mock_send_message, pending_scheduled_call, db_session
):
    """Test execution when message sending raises an exception."""
    mock_send_message.side_effect = Exception("Network error")

    result = scheduled_calls.execute_scheduled_call(pending_scheduled_call.id)

    # Should fail since sending raised exception
    assert result["success"] is False

    # Verify the call was marked as failed with error message
    db_session.refresh(pending_scheduled_call)
    assert pending_scheduled_call.status == "failed"
    assert "Network error" in pending_scheduled_call.error_message


@patch("memory.workers.tasks.scheduled_calls.send_message")
def test_execute_scheduled_call_with_model(
    mock_send_message, db_session, sample_user
):
    """Test execution with a model specified (model is stored but not used for notifications)."""
    mock_send_message.return_value = True

    call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        topic="Model Test",
        scheduled_time=datetime.now(timezone.utc) - timedelta(minutes=5),
        model="test-model",  # Model is stored but notifications just send the message
        message="Test message",
        channel_type="discord",
        channel_identifier="123456789",
        status="pending",
    )
    db_session.add(call)
    db_session.commit()

    result = scheduled_calls.execute_scheduled_call(call.id)

    assert result["success"] is True
    assert result["channel_type"] == "discord"


@patch("memory.workers.tasks.scheduled_calls.send_message")
def test_execute_scheduled_call_long_message(
    mock_send_message, db_session, sample_user
):
    """Test execution with a long message."""
    mock_send_message.return_value = True

    long_message = "A" * 500  # Long message
    call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        topic="Long Message",
        scheduled_time=datetime.now(timezone.utc) - timedelta(minutes=5),
        model=None,
        message=long_message,
        channel_type="discord",
        channel_identifier="123456789",
        status="pending",
    )
    db_session.add(call)
    db_session.commit()
    call_id = call.id

    result = scheduled_calls.execute_scheduled_call(call_id)

    assert result["success"] is True
    assert result["channel_type"] == "discord"

    # send_message was called
    mock_send_message.assert_called_once()


@patch("memory.workers.tasks.scheduled_calls.execute_scheduled_call")
def test_run_scheduled_calls_with_due_calls(
    mock_execute_delay, db_session, sample_user
):
    """Test running scheduled calls with due calls."""
    # Create multiple due calls
    due_call1 = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        scheduled_time=datetime.now(timezone.utc) - timedelta(minutes=10),
        model=None,
        message="Test 1",
        channel_type="discord",
        channel_identifier="123456789",
        status="pending",
    )
    due_call2 = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        scheduled_time=datetime.now(timezone.utc) - timedelta(minutes=5),
        model=None,
        message="Test 2",
        channel_type="slack",
        channel_identifier="U12345678",
        status="pending",
    )

    db_session.add_all([due_call1, due_call2])
    db_session.commit()

    mock_task = Mock()
    mock_task.id = "task-123"
    mock_execute_delay.delay.return_value = mock_task

    result = scheduled_calls.run_scheduled_calls()

    assert result["count"] == 2
    assert due_call1.id in result["calls"]
    assert due_call2.id in result["calls"]

    # Verify execute_scheduled_call.delay was called for both
    assert mock_execute_delay.delay.call_count == 2
    mock_execute_delay.delay.assert_any_call(due_call1.id)
    mock_execute_delay.delay.assert_any_call(due_call2.id)


@patch("memory.workers.tasks.scheduled_calls.execute_scheduled_call")
def test_run_scheduled_calls_no_due_calls(
    mock_execute_delay, future_scheduled_call, db_session
):
    """Test running scheduled calls when no calls are due."""
    result = scheduled_calls.run_scheduled_calls()

    assert result["count"] == 0
    assert result["calls"] == []

    # No tasks should be scheduled
    mock_execute_delay.delay.assert_not_called()


@patch("memory.workers.tasks.scheduled_calls.execute_scheduled_call")
def test_run_scheduled_calls_mixed_statuses(
    mock_execute_delay, db_session, sample_user
):
    """Test that only pending calls are processed."""
    # Create calls with different statuses
    pending_call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        scheduled_time=datetime.now(timezone.utc) - timedelta(minutes=5),
        model=None,
        message="Pending",
        channel_type="discord",
        channel_identifier="123456789",
        status="pending",
    )
    executing_call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        scheduled_time=datetime.now(timezone.utc) - timedelta(minutes=5),
        model=None,
        message="Executing",
        channel_type="discord",
        channel_identifier="123456789",
        status="executing",
    )
    completed_call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        scheduled_time=datetime.now(timezone.utc) - timedelta(minutes=5),
        model=None,
        message="Completed",
        channel_type="discord",
        channel_identifier="123456789",
        status="completed",
    )

    db_session.add_all([pending_call, executing_call, completed_call])
    db_session.commit()

    mock_task = Mock()
    mock_task.id = "task-123"
    mock_execute_delay.delay.return_value = mock_task

    result = scheduled_calls.run_scheduled_calls()

    # Only the pending call should be processed
    assert result["count"] == 1
    assert result["calls"] == [pending_call.id]

    mock_execute_delay.delay.assert_called_once_with(pending_call.id)


@patch("memory.workers.tasks.scheduled_calls.execute_scheduled_call")
def test_run_scheduled_calls_timezone_handling(
    mock_execute_delay, db_session, sample_user
):
    """Test that timezone handling works correctly."""
    # Create a call that's due (scheduled time in the past)
    past_time = datetime.now(timezone.utc) - timedelta(minutes=5)
    due_call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        scheduled_time=past_time.replace(tzinfo=None),  # Store as naive datetime
        model=None,
        message="Due call",
        channel_type="discord",
        channel_identifier="123456789",
        status="pending",
    )

    # Create a call that's not due yet
    future_time = datetime.now(timezone.utc) + timedelta(minutes=5)
    future_call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        scheduled_time=future_time.replace(tzinfo=None),  # Store as naive datetime
        model=None,
        message="Future call",
        channel_type="discord",
        channel_identifier="123456789",
        status="pending",
    )

    db_session.add_all([due_call, future_call])
    db_session.commit()

    mock_task = Mock()
    mock_task.id = "task-123"
    mock_execute_delay.delay.return_value = mock_task

    result = scheduled_calls.run_scheduled_calls()

    # Only the due call should be processed
    assert result["count"] == 1
    assert result["calls"] == [due_call.id]

    mock_execute_delay.delay.assert_called_once_with(due_call.id)


@patch("memory.workers.tasks.scheduled_calls.send_message")
def test_status_transition_pending_to_completed(
    mock_send_message, pending_scheduled_call, db_session
):
    """Test that status transitions correctly during execution."""
    mock_send_message.return_value = True

    # Initial status should be pending
    assert pending_scheduled_call.status == "pending"

    scheduled_calls.execute_scheduled_call(pending_scheduled_call.id)

    # Final status should be completed
    db_session.refresh(pending_scheduled_call)
    assert pending_scheduled_call.status == "completed"


@pytest.mark.parametrize(
    "status,should_execute",
    [
        ("pending", True),
        ("queued", True),  # queued is also valid now
        ("executing", False),
        ("completed", False),
        ("failed", False),
        ("cancelled", False),
    ],
)
@patch("memory.workers.tasks.scheduled_calls.send_message")
def test_execute_scheduled_call_status_check(
    mock_send_message, status, should_execute, db_session, sample_user
):
    """Test that only pending or queued calls are executed."""
    mock_send_message.return_value = True

    call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        topic="Status Test",
        scheduled_time=datetime.now(timezone.utc) - timedelta(minutes=5),
        model=None,
        message="Test",
        channel_type="discord",
        channel_identifier="123456789",
        status=status,
    )
    db_session.add(call)
    db_session.commit()

    result = scheduled_calls.execute_scheduled_call(call.id)

    if should_execute:
        assert result["success"] is True
    else:
        assert result == {"error": f"Call is not ready (status: {status})"}


def test_send_message_no_channel_type(db_session, sample_user, caplog):
    """Test send_message returns False when no channel_type is set."""
    call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        scheduled_time=datetime.now(timezone.utc),
        model=None,
        message="Test",
        channel_type=None,
        channel_identifier="123456789",
        status="pending",
    )
    db_session.add(call)
    db_session.commit()

    result = scheduled_calls.send_message(call)

    assert result is False
    assert "No channel_type" in caplog.text


def test_send_message_unknown_channel_type(db_session, sample_user, caplog):
    """Test send_message returns False for unknown channel_type."""
    call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        scheduled_time=datetime.now(timezone.utc),
        model=None,
        message="Test",
        channel_type="sms",  # Not supported
        channel_identifier="123456789",
        status="pending",
    )
    db_session.add(call)
    db_session.commit()

    result = scheduled_calls.send_message(call)

    assert result is False
    assert "Unknown channel_type: sms" in caplog.text


@patch("memory.workers.tasks.scheduled_calls.send_via_discord")
def test_send_message_routes_to_discord(mock_send_discord, db_session, sample_user):
    """Test send_message routes Discord channel_type to send_via_discord."""
    mock_send_discord.return_value = True

    call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        scheduled_time=datetime.now(timezone.utc),
        model=None,
        message="Test",
        channel_type="discord",
        channel_identifier="123456789",
        status="pending",
    )
    db_session.add(call)
    db_session.commit()

    result = scheduled_calls.send_message(call)

    assert result is True
    mock_send_discord.assert_called_once_with(call)


@patch("memory.workers.tasks.scheduled_calls.send_via_slack")
def test_send_message_routes_to_slack(mock_send_slack, db_session, sample_user):
    """Test send_message routes Slack channel_type to send_via_slack."""
    mock_send_slack.return_value = True

    call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        scheduled_time=datetime.now(timezone.utc),
        model=None,
        message="Test",
        channel_type="slack",
        channel_identifier="U12345678",
        status="pending",
    )
    db_session.add(call)
    db_session.commit()

    result = scheduled_calls.send_message(call)

    assert result is True
    mock_send_slack.assert_called_once_with(call)


@patch("memory.workers.tasks.scheduled_calls.send_via_email")
def test_send_message_routes_to_email(mock_send_email, db_session, sample_user):
    """Test send_message routes email channel_type to send_via_email."""
    mock_send_email.return_value = True

    call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        scheduled_time=datetime.now(timezone.utc),
        model=None,
        message="Test",
        channel_type="email",
        channel_identifier="user@example.com",
        status="pending",
    )
    db_session.add(call)
    db_session.commit()

    result = scheduled_calls.send_message(call)

    assert result is True
    mock_send_email.assert_called_once_with(call)
