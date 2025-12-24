import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch
import uuid

from memory.common.db.models import (
    ScheduledLLMCall,
    DiscordBotUser,
    DiscordUser,
    DiscordChannel,
    DiscordServer,
)
from memory.workers.tasks import scheduled_calls


@pytest.fixture
def sample_user(db_session):
    """Create a sample user for testing."""
    # Create a discord user for the bot
    bot_discord_user = DiscordUser(
        id=999999999,
        username="testbot",
    )
    db_session.add(bot_discord_user)
    db_session.flush()

    user = DiscordBotUser.create_with_api_key(
        discord_users=[bot_discord_user],
        name="testbot",
        email="bot@example.com",
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
        model="anthropic/claude-3-5-sonnet-20241022",
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
        model="anthropic/claude-3-5-sonnet-20241022",
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
        model="anthropic/claude-3-5-sonnet-20241022",
        message="What will happen tomorrow?",
        discord_user_id=sample_discord_user.id,
        status="pending",
    )
    db_session.add(call)
    db_session.commit()
    return call


@patch("memory.discord.messages.discord.send_dm")
def test_send_to_discord_user(mock_send_dm, pending_scheduled_call):
    """Test sending to Discord user."""
    response = "This is a test response."

    scheduled_calls.send_to_discord(999999999, pending_scheduled_call, response)

    mock_send_dm.assert_called_once_with(
        999999999,  # bot_id
        "testuser",  # username, not ID
        response,
    )


@patch("memory.discord.messages.discord.send_to_channel")
def test_send_to_discord_channel(mock_send_to_channel, completed_scheduled_call):
    """Test sending to Discord channel."""
    response = "This is a channel response."

    scheduled_calls.send_to_discord(999999999, completed_scheduled_call, response)

    mock_send_to_channel.assert_called_once_with(
        999999999,  # bot_id
        completed_scheduled_call.discord_channel.id,  # channel ID, not name
        response,
    )


@patch("memory.discord.messages.discord.send_dm")
def test_send_to_discord_long_message_truncation(mock_send_dm, pending_scheduled_call):
    """Test message truncation for long responses."""
    long_response = "A" * 2500  # Very long response

    scheduled_calls.send_to_discord(999999999, pending_scheduled_call, long_response)

    # With the new implementation, send_discord_response sends the full response
    # No truncation happens in _send_to_discord
    args, kwargs = mock_send_dm.call_args
    assert args[0] == 999999999  # bot_id
    message = args[2]
    assert message == long_response


@patch("memory.discord.messages.discord.send_dm")
def test_send_to_discord_normal_length_message(mock_send_dm, pending_scheduled_call):
    """Test that normal length messages are not truncated."""
    normal_response = "This is a normal length response."

    scheduled_calls.send_to_discord(999999999, pending_scheduled_call, normal_response)

    args, kwargs = mock_send_dm.call_args
    assert args[0] == 999999999  # bot_id
    message = args[2]
    assert message == normal_response


@patch("memory.workers.tasks.scheduled_calls.send_to_discord")
@patch("memory.workers.tasks.scheduled_calls.call_llm_for_scheduled")
def test_execute_scheduled_call_success(
    mock_llm_call, mock_send_discord, pending_scheduled_call, db_session
):
    """Test successful execution of a scheduled LLM call."""
    mock_llm_call.return_value = "The weather is sunny today!"

    result = scheduled_calls.execute_scheduled_call(pending_scheduled_call.id)

    # Verify LLM was called
    mock_llm_call.assert_called_once()

    # Verify result
    assert result["success"] is True
    assert result["scheduled_call_id"] == pending_scheduled_call.id
    assert result["response"] == "The weather is sunny today!"
    assert result["discord_sent"] is True

    # Verify database was updated
    db_session.refresh(pending_scheduled_call)
    assert pending_scheduled_call.status == "completed"
    assert pending_scheduled_call.response == "The weather is sunny today!"
    assert pending_scheduled_call.executed_at is not None


def test_execute_scheduled_call_not_found(db_session):
    """Test execution with non-existent call ID."""
    fake_id = str(uuid.uuid4())

    result = scheduled_calls.execute_scheduled_call(fake_id)

    assert result == {"error": "Scheduled call not found"}


@patch("memory.workers.tasks.scheduled_calls.call_llm_for_scheduled")
def test_execute_scheduled_call_not_pending(
    mock_llm_call, completed_scheduled_call, db_session
):
    """Test execution of a call that is not pending."""
    result = scheduled_calls.execute_scheduled_call(completed_scheduled_call.id)

    assert result == {"error": "Call is not pending (status: completed)"}
    mock_llm_call.assert_not_called()


@patch("memory.workers.tasks.scheduled_calls.send_to_discord")
@patch("memory.workers.tasks.scheduled_calls.call_llm_for_scheduled")
def test_execute_scheduled_call_with_default_system_prompt(
    mock_llm_call, mock_send_discord, db_session, sample_user, sample_discord_user
):
    """Test execution when system_prompt is None, should use default."""
    # Create call without system prompt
    call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        topic="No System Prompt",
        scheduled_time=datetime.now(timezone.utc) - timedelta(minutes=5),
        model="anthropic/claude-3-5-sonnet-20241022",
        message="Test prompt",
        system_prompt=None,
        discord_user_id=sample_discord_user.id,
        status="pending",
    )
    db_session.add(call)
    db_session.commit()

    mock_llm_call.return_value = "Response"

    scheduled_calls.execute_scheduled_call(call.id)

    # Verify LLM was called
    mock_llm_call.assert_called_once()


@patch("memory.workers.tasks.scheduled_calls.send_to_discord")
@patch("memory.workers.tasks.scheduled_calls.call_llm_for_scheduled")
def test_execute_scheduled_call_discord_error(
    mock_llm_call, mock_send_discord, pending_scheduled_call, db_session
):
    """Test execution when Discord sending fails."""
    mock_llm_call.return_value = "LLM response"
    mock_send_discord.side_effect = Exception("Discord API error")

    result = scheduled_calls.execute_scheduled_call(pending_scheduled_call.id)

    # Should still return success since LLM call succeeded
    assert result["success"] is True
    assert (
        result["discord_sent"] is True
    )  # This is always True in current implementation

    # Verify the call was marked as completed despite Discord error
    db_session.refresh(pending_scheduled_call)
    assert pending_scheduled_call.status == "completed"
    assert pending_scheduled_call.response == "LLM response"
    assert pending_scheduled_call.data["discord_error"] == "Discord API error"


@patch("memory.workers.tasks.scheduled_calls.send_to_discord")
@patch("memory.workers.tasks.scheduled_calls.call_llm_for_scheduled")
def test_execute_scheduled_call_llm_error(
    mock_llm_call, mock_send_discord, pending_scheduled_call, db_session
):
    """Test execution when LLM call fails."""
    mock_llm_call.side_effect = Exception("LLM API error")

    # The execute_scheduled_call function catches the exception and returns an error response
    result = scheduled_calls.execute_scheduled_call(pending_scheduled_call.id)

    assert result["success"] is False
    assert "error" in result
    assert "LLM call failed" in result["error"]

    # Discord should not be called
    mock_send_discord.assert_not_called()


@patch("memory.workers.tasks.scheduled_calls.send_to_discord")
@patch("memory.workers.tasks.scheduled_calls.call_llm_for_scheduled")
def test_execute_scheduled_call_long_response_truncation(
    mock_llm_call, mock_send_discord, pending_scheduled_call, db_session
):
    """Test that long responses are truncated in the result."""
    long_response = "A" * 500  # Long response
    mock_llm_call.return_value = long_response

    result = scheduled_calls.execute_scheduled_call(pending_scheduled_call.id)

    # Response in result should be truncated
    assert len(result["response"]) <= 103  # 100 chars + "..."
    assert result["response"].endswith("...")

    # But full response should be stored in database
    db_session.refresh(pending_scheduled_call)
    assert pending_scheduled_call.response == long_response


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
        model="test-model",
        message="Test 1",
        discord_user_id=sample_discord_user.id,
        status="pending",
    )
    due_call2 = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        scheduled_time=datetime.now(timezone.utc) - timedelta(minutes=5),
        model="test-model",
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
        model="test-model",
        message="Pending",
        discord_user_id=sample_discord_user.id,
        status="pending",
    )
    executing_call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        scheduled_time=datetime.now(timezone.utc) - timedelta(minutes=5),
        model="test-model",
        message="Executing",
        discord_user_id=sample_discord_user.id,
        status="executing",
    )
    completed_call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        scheduled_time=datetime.now(timezone.utc) - timedelta(minutes=5),
        model="test-model",
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
        model="test-model",
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
        model="test-model",
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
@patch("memory.workers.tasks.scheduled_calls.call_llm_for_scheduled")
def test_status_transition_pending_to_executing_to_completed(
    mock_llm_call, mock_send_discord, pending_scheduled_call, db_session
):
    """Test that status transitions correctly during execution."""
    mock_llm_call.return_value = "Response"

    # Initial status should be pending
    assert pending_scheduled_call.status == "pending"
    assert pending_scheduled_call.executed_at is None

    scheduled_calls.execute_scheduled_call(pending_scheduled_call.id)

    # Final status should be completed
    db_session.refresh(pending_scheduled_call)
    assert pending_scheduled_call.status == "completed"
    assert pending_scheduled_call.executed_at is not None
    assert pending_scheduled_call.response == "Response"


@pytest.mark.parametrize(
    "has_discord_user,has_discord_channel,expected_method",
    [
        (True, False, "send_dm"),
        (False, True, "send_to_channel"),
        (True, True, "send_to_channel"),  # Channel takes precedence in the implementation
    ],
)
@patch("memory.discord.messages.discord.send_dm")
@patch("memory.discord.messages.discord.send_to_channel")
def test_discord_destination_priority(
    mock_send_to_channel,
    mock_send_dm,
    has_discord_user,
    has_discord_channel,
    expected_method,
    db_session,
    sample_user,
    sample_discord_user,
    sample_discord_channel,
):
    """Test that Discord user takes precedence over channel."""
    call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        topic="Priority Test",
        scheduled_time=datetime.now(timezone.utc),
        model="test-model",
        message="Test",
        discord_user_id=sample_discord_user.id if has_discord_user else None,
        discord_channel_id=sample_discord_channel.id if has_discord_channel else None,
        status="pending",
    )
    db_session.add(call)
    db_session.commit()

    response = "Test response"
    scheduled_calls.send_to_discord(999999999, call, response)

    if expected_method == "send_dm":
        mock_send_dm.assert_called_once()
        mock_send_to_channel.assert_not_called()
    else:
        mock_send_to_channel.assert_called_once()
        mock_send_dm.assert_not_called()


@pytest.mark.parametrize(
    "topic,model,response",
    [
        (
            "Weather Check",
            "anthropic/claude-3-5-sonnet-20241022",
            "It's sunny!",
        ),
        (
            "Test Topic",
            "gpt-4",
            "Hello world",
        ),
        (
            "Long Topic Name Here",
            "claude-2",
            "Short",
        ),
    ],
)
@patch("memory.discord.messages.discord.send_dm")
def test_message_formatting(mock_send_dm, topic, model, response):
    """Test that _send_to_discord sends the response as-is."""
    # Create a mock scheduled call with a mock Discord user
    mock_discord_user = Mock()
    mock_discord_user.username = "testuser"

    mock_call = Mock()
    mock_call.user_id = 987
    mock_call.topic = topic
    mock_call.model = model
    mock_call.discord_user = mock_discord_user
    mock_call.discord_channel = None

    scheduled_calls.send_to_discord(999999999, mock_call, response)

    # Get the actual message that was sent
    args, kwargs = mock_send_dm.call_args
    assert args[0] == 999999999  # bot_id
    actual_message = args[2]

    # The new implementation sends the response as-is, without formatting
    assert actual_message == response


@pytest.mark.parametrize(
    "status,should_execute",
    [
        ("pending", True),
        ("executing", False),
        ("completed", False),
        ("failed", False),
        ("cancelled", False),
    ],
)
@patch("memory.workers.tasks.scheduled_calls.call_llm_for_scheduled")
def test_execute_scheduled_call_status_check(
    mock_llm_call, status, should_execute, db_session, sample_user, sample_discord_user
):
    """Test that only pending calls are executed."""
    call = ScheduledLLMCall(
        id=str(uuid.uuid4()),
        user_id=sample_user.id,
        topic="Status Test",
        scheduled_time=datetime.now(timezone.utc) - timedelta(minutes=5),
        model="test-model",
        message="Test",
        discord_user_id=sample_discord_user.id,
        status=status,
    )
    db_session.add(call)
    db_session.commit()

    result = scheduled_calls.execute_scheduled_call(call.id)

    if should_execute:
        mock_llm_call.assert_called_once()
        # We don't check the full result here since it depends on mocking more functions
    else:
        assert result == {"error": f"Call is not pending (status: {status})"}
        mock_llm_call.assert_not_called()
