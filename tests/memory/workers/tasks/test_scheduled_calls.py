import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch
import uuid

from memory.common.db.models import (
    ScheduledLLMCall,
    HumanUser,
    DiscordUser,
    DiscordChannel,
    DiscordServer,
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
def sample_discord_user(db_session):
    """Create a sample Discord user for testing."""
    discord_user = DiscordUser(
        id=123456789,
        username="testuser",
    )
    db_session.add(discord_user)
    db_session.commit()
    return discord_user


@pytest.fixture
def sample_discord_server(db_session):
    """Create a sample Discord server for testing."""
    server = DiscordServer(
        id=987654321,
        name="Test Server",
    )
    db_session.add(server)
    db_session.commit()
    return server


@pytest.fixture
def sample_discord_channel(db_session, sample_discord_server):
    """Create a sample Discord channel for testing."""
    channel = DiscordChannel(
        id=111222333,
        name="test-channel",
        channel_type="text",
        server_id=sample_discord_server.id,
    )
    db_session.add(channel)
    db_session.commit()
    return channel


@pytest.fixture
def pending_scheduled_call(db_session, sample_user, sample_discord_user):
    """Create a pending scheduled call for testing."""
    call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        topic="Test Topic",
        scheduled_time=datetime.now(timezone.utc) - timedelta(minutes=5),
        model=None,  # No model means message returned as-is
        message="What is the weather like today?",
        system_prompt="You are a helpful assistant.",
        discord_user_id=sample_discord_user.id,
        status="pending",
    )
    db_session.add(call)
    db_session.commit()
    return call


@pytest.fixture
def completed_scheduled_call(db_session, sample_user, sample_discord_channel):
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
        discord_channel_id=sample_discord_channel.id,
        status="completed",
        response="Why did the chicken cross the road? To get to the other side!",
    )
    db_session.add(call)
    db_session.commit()
    return call


@pytest.fixture
def future_scheduled_call(db_session, sample_user, sample_discord_user):
    """Create a future scheduled call for testing."""
    call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        topic="Future Topic",
        scheduled_time=datetime.now(timezone.utc) + timedelta(hours=1),
        model=None,
        message="What will happen tomorrow?",
        discord_user_id=sample_discord_user.id,
        status="pending",
    )
    db_session.add(call)
    db_session.commit()
    return call


def test_send_to_discord_user_logs_warning(pending_scheduled_call, caplog):
    """Test that send_to_discord logs a warning (currently a stub)."""
    response = "This is a test response."

    scheduled_calls.send_to_discord(999999999, pending_scheduled_call, response)

    # The current implementation is a stub that logs a warning
    assert "Discord sending not yet implemented" in caplog.text


def test_send_to_discord_channel_logs_warning(completed_scheduled_call, caplog):
    """Test that send_to_discord logs a warning for channel (currently a stub)."""
    response = "This is a channel response."

    scheduled_calls.send_to_discord(999999999, completed_scheduled_call, response)

    # The current implementation is a stub that logs a warning
    assert "Discord sending not yet implemented" in caplog.text


@patch("memory.workers.tasks.scheduled_calls.send_to_discord")
def test_execute_scheduled_call_success(
    mock_send_discord, pending_scheduled_call, db_session
):
    """Test successful execution of a scheduled LLM call."""
    result = scheduled_calls.execute_scheduled_call(pending_scheduled_call.id)

    # Verify result
    assert result["success"] is True
    assert result["scheduled_call_id"] == pending_scheduled_call.id
    # When model is None, message is returned as-is
    assert result["response"] == "What is the weather like today?"

    # Verify database was updated
    db_session.refresh(pending_scheduled_call)
    assert pending_scheduled_call.status == "completed"
    assert pending_scheduled_call.response == "What is the weather like today?"
    assert pending_scheduled_call.executed_at is not None


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


@patch("memory.workers.tasks.scheduled_calls.send_to_discord")
def test_execute_scheduled_call_with_default_system_prompt(
    mock_send_discord, db_session, sample_user, sample_discord_user
):
    """Test execution when system_prompt is None, should use default."""
    # Create call without system prompt
    call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        topic="No System Prompt",
        scheduled_time=datetime.now(timezone.utc) - timedelta(minutes=5),
        model=None,
        message="Test prompt",
        system_prompt=None,
        discord_user_id=sample_discord_user.id,
        status="pending",
    )
    db_session.add(call)
    db_session.commit()

    result = scheduled_calls.execute_scheduled_call(call.id)

    assert result["success"] is True


@patch("memory.workers.tasks.scheduled_calls.send_to_discord")
def test_execute_scheduled_call_discord_error(
    mock_send_discord, pending_scheduled_call, db_session
):
    """Test execution when Discord sending fails."""
    mock_send_discord.side_effect = Exception("Discord API error")

    result = scheduled_calls.execute_scheduled_call(pending_scheduled_call.id)

    # Should still return success since the "LLM call" succeeded
    assert result["success"] is True

    # Verify the call was marked as completed despite Discord error
    db_session.refresh(pending_scheduled_call)
    assert pending_scheduled_call.status == "completed"
    assert pending_scheduled_call.data["discord_error"] == "Discord API error"


@patch("memory.workers.tasks.scheduled_calls.call_llm_for_scheduled")
def test_execute_scheduled_call_llm_error(
    mock_llm_call, db_session, sample_user, sample_discord_user
):
    """Test execution when LLM call fails."""
    # Create a call with a model specified (so it actually calls the LLM function)
    call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        topic="LLM Call",
        scheduled_time=datetime.now(timezone.utc) - timedelta(minutes=5),
        model="test-model",  # Specify a model to trigger LLM call
        message="Test message",
        discord_user_id=sample_discord_user.id,
        status="pending",
    )
    db_session.add(call)
    db_session.commit()

    mock_llm_call.side_effect = Exception("LLM API error")

    result = scheduled_calls.execute_scheduled_call(call.id)

    assert result["success"] is False
    assert "error" in result
    assert "LLM call failed" in result["error"]


@patch("memory.workers.tasks.scheduled_calls.send_to_discord")
def test_execute_scheduled_call_long_response_truncation(
    mock_send_discord, db_session, sample_user, sample_discord_user
):
    """Test that long responses are truncated in the result."""
    long_message = "A" * 500  # Long message
    call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        topic="Long Response",
        scheduled_time=datetime.now(timezone.utc) - timedelta(minutes=5),
        model=None,
        message=long_message,
        discord_user_id=sample_discord_user.id,
        status="pending",
    )
    db_session.add(call)
    db_session.commit()

    result = scheduled_calls.execute_scheduled_call(call.id)

    # Response in result should be truncated
    assert len(result["response"]) <= 103  # 100 chars + "..."
    assert result["response"].endswith("...")

    # But full response should be stored in database
    db_session.refresh(call)
    assert call.response == long_message


@patch("memory.workers.tasks.scheduled_calls.execute_scheduled_call")
def test_run_scheduled_calls_with_due_calls(
    mock_execute_delay, db_session, sample_user, sample_discord_user
):
    """Test running scheduled calls with due calls."""
    # Create multiple due calls
    due_call1 = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        scheduled_time=datetime.now(timezone.utc) - timedelta(minutes=10),
        model=None,
        message="Test 1",
        discord_user_id=sample_discord_user.id,
        status="pending",
    )
    due_call2 = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        scheduled_time=datetime.now(timezone.utc) - timedelta(minutes=5),
        model=None,
        message="Test 2",
        discord_user_id=sample_discord_user.id,
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
    mock_execute_delay, db_session, sample_user, sample_discord_user
):
    """Test that only pending calls are processed."""
    # Create calls with different statuses
    pending_call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        scheduled_time=datetime.now(timezone.utc) - timedelta(minutes=5),
        model=None,
        message="Pending",
        discord_user_id=sample_discord_user.id,
        status="pending",
    )
    executing_call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        scheduled_time=datetime.now(timezone.utc) - timedelta(minutes=5),
        model=None,
        message="Executing",
        discord_user_id=sample_discord_user.id,
        status="executing",
    )
    completed_call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        scheduled_time=datetime.now(timezone.utc) - timedelta(minutes=5),
        model=None,
        message="Completed",
        discord_user_id=sample_discord_user.id,
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
    mock_execute_delay, db_session, sample_user, sample_discord_user
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
        discord_user_id=sample_discord_user.id,
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
        discord_user_id=sample_discord_user.id,
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


@patch("memory.workers.tasks.scheduled_calls.send_to_discord")
def test_status_transition_pending_to_executing_to_completed(
    mock_send_discord, pending_scheduled_call, db_session
):
    """Test that status transitions correctly during execution."""
    # Initial status should be pending
    assert pending_scheduled_call.status == "pending"
    assert pending_scheduled_call.executed_at is None

    scheduled_calls.execute_scheduled_call(pending_scheduled_call.id)

    # Final status should be completed
    db_session.refresh(pending_scheduled_call)
    assert pending_scheduled_call.status == "completed"
    assert pending_scheduled_call.executed_at is not None
    assert pending_scheduled_call.response == "What is the weather like today?"


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
def test_execute_scheduled_call_status_check(
    status, should_execute, db_session, sample_user, sample_discord_user
):
    """Test that only pending or queued calls are executed."""
    call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        topic="Status Test",
        scheduled_time=datetime.now(timezone.utc) - timedelta(minutes=5),
        model=None,
        message="Test",
        discord_user_id=sample_discord_user.id,
        status=status,
    )
    db_session.add(call)
    db_session.commit()

    result = scheduled_calls.execute_scheduled_call(call.id)

    if should_execute:
        assert result["success"] is True
    else:
        assert result == {"error": f"Call is not ready (status: {status})"}
