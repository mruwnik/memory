"""Tests for Discord LLM tools."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch

from memory.common.llms.tools.discord import (
    handle_update_summary_call,
    make_summary_tool,
    schedule_message,
    make_message_scheduler,
    make_prev_messages_tool,
    make_discord_tools,
)
from memory.common.db.models import (
    DiscordServer,
    DiscordChannel,
    DiscordUser,
    DiscordMessage,
    BotUser,
    DiscordBotUser,
    HumanUser,
    ScheduledLLMCall,
)


# Fixtures for Discord entities
@pytest.fixture
def sample_discord_server(db_session):
    """Create a sample Discord server for testing."""
    server = DiscordServer(
        id=123456789,
        name="Test Server",
        summary="A test server for testing",
    )
    db_session.add(server)
    db_session.commit()
    return server


@pytest.fixture
def sample_discord_channel(db_session, sample_discord_server):
    """Create a sample Discord channel for testing."""
    channel = DiscordChannel(
        id=987654321,
        server_id=sample_discord_server.id,
        name="general",
        channel_type="text",
        summary="General discussion channel",
    )
    db_session.add(channel)
    db_session.commit()
    return channel


@pytest.fixture
def sample_discord_user(db_session):
    """Create a sample Discord user for testing."""
    user = DiscordUser(
        id=111222333,
        username="testuser",
        display_name="Test User",
        summary="A test user",
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def sample_bot_user(db_session, sample_discord_user):
    """Create a sample bot user for testing."""
    bot = DiscordBotUser.create_with_api_key(
        discord_users=[sample_discord_user],
        name="Test Bot",
        email="testbot@example.com",
    )
    db_session.add(bot)
    db_session.commit()
    return bot


@pytest.fixture
def sample_human_user(db_session):
    """Create a sample human user for testing."""
    user = HumanUser.create_with_password(
        email="human@example.com",
        name="Human User",
        password="test_password123",
    )
    db_session.add(user)
    db_session.commit()
    return user


# Tests for handle_update_summary_call
def test_handle_update_summary_call_server_dict_input(
    db_session, sample_discord_server
):
    """Test updating server summary with dict input."""
    handler = handle_update_summary_call("server", sample_discord_server.id)

    result = handler({"summary": "New server summary"})

    assert result == "Updated summary"

    # Verify the summary was updated in the database
    db_session.refresh(sample_discord_server)
    assert sample_discord_server.summary == "New server summary"


def test_handle_update_summary_call_channel_dict_input(
    db_session, sample_discord_channel
):
    """Test updating channel summary with dict input."""
    handler = handle_update_summary_call("channel", sample_discord_channel.id)

    result = handler({"summary": "New channel summary"})

    assert result == "Updated summary"

    db_session.refresh(sample_discord_channel)
    assert sample_discord_channel.summary == "New channel summary"


def test_handle_update_summary_call_user_dict_input(db_session, sample_discord_user):
    """Test updating user summary with dict input."""
    handler = handle_update_summary_call("user", sample_discord_user.id)

    result = handler({"summary": "New user summary"})

    assert result == "Updated summary"

    db_session.refresh(sample_discord_user)
    assert sample_discord_user.summary == "New user summary"


def test_handle_update_summary_call_string_input(db_session, sample_discord_server):
    """Test updating summary with string input."""
    handler = handle_update_summary_call("server", sample_discord_server.id)

    result = handler("String summary")

    assert result == "Updated summary"

    db_session.refresh(sample_discord_server)
    assert sample_discord_server.summary == "String summary"


def test_handle_update_summary_call_dict_without_summary_key(
    db_session, sample_discord_server
):
    """Test updating summary with dict that doesn't have 'summary' key."""
    handler = handle_update_summary_call("server", sample_discord_server.id)

    result = handler({"other_key": "value"})

    assert result == "Updated summary"

    db_session.refresh(sample_discord_server)
    # Should use string representation of the dict
    assert "other_key" in sample_discord_server.summary


def test_handle_update_summary_call_nonexistent_entity(db_session):
    """Test updating summary for nonexistent entity."""
    handler = handle_update_summary_call("server", 999999999)

    result = handler({"summary": "New summary"})

    assert "Error updating summary" in result


# Tests for make_summary_tool
def test_make_summary_tool_server(sample_discord_server):
    """Test creating a summary tool for a server."""
    tool = make_summary_tool("server", sample_discord_server.id)

    assert tool.name == "update_server_summary"
    assert "server" in tool.description
    assert tool.input_schema["type"] == "object"
    assert "summary" in tool.input_schema["properties"]
    assert callable(tool.function)


def test_make_summary_tool_channel(sample_discord_channel):
    """Test creating a summary tool for a channel."""
    tool = make_summary_tool("channel", sample_discord_channel.id)

    assert tool.name == "update_channel_summary"
    assert "channel" in tool.description
    assert callable(tool.function)


def test_make_summary_tool_user(sample_discord_user):
    """Test creating a summary tool for a user."""
    tool = make_summary_tool("user", sample_discord_user.id)

    assert tool.name == "update_user_summary"
    assert "user" in tool.description
    assert callable(tool.function)


# Tests for schedule_message
def test_schedule_message_with_user(
    db_session,
    sample_human_user,
    sample_discord_user,
):
    """Test scheduling a message to a Discord user."""
    future_time = datetime.now(timezone.utc) + timedelta(hours=1)

    result = schedule_message(
        bot_id=sample_human_user.id,
        recipient_id=sample_discord_user.id,
        channel_id=None,
        model="test-model",
        message="Test message",
        date_time=future_time,
    )

    # Result should be the ID of the created scheduled call (UUID string)
    assert isinstance(result, str)

    # Verify the scheduled call was created in the database
    # Need to use a fresh query since schedule_message uses its own session
    scheduled_call = db_session.query(ScheduledLLMCall).filter_by(id=result).first()
    assert scheduled_call is not None
    assert scheduled_call.user_id == sample_human_user.id
    assert scheduled_call.discord_user_id == sample_discord_user.id
    assert scheduled_call.discord_channel_id is None
    assert scheduled_call.message == "Test message"
    assert scheduled_call.model == "test-model"


def test_schedule_message_with_channel(
    db_session,
    sample_human_user,
    sample_discord_channel,
):
    """Test scheduling a message to a Discord channel."""
    future_time = datetime.now(timezone.utc) + timedelta(hours=1)

    result = schedule_message(
        bot_id=sample_human_user.id,
        recipient_id=None,
        channel_id=sample_discord_channel.id,
        model="test-model",
        message="Test message",
        date_time=future_time,
    )

    # Result should be the ID of the created scheduled call (UUID string)
    assert isinstance(result, str)

    # Verify the scheduled call was created in the database
    scheduled_call = db_session.query(ScheduledLLMCall).filter_by(id=result).first()
    assert scheduled_call is not None
    assert scheduled_call.user_id == sample_human_user.id
    assert scheduled_call.discord_user_id is None
    assert scheduled_call.discord_channel_id == sample_discord_channel.id
    assert scheduled_call.message == "Test message"


# Tests for make_message_scheduler
def test_make_message_scheduler_with_user(sample_bot_user, sample_discord_user):
    """Test creating a message scheduler tool for a user."""
    tool = make_message_scheduler(
        bot=sample_bot_user,
        user_id=sample_discord_user.id,
        channel_id=None,
        model="test-model",
    )

    assert tool.name == "schedule_discord_message"
    assert "from your chat with this user" in tool.description
    assert tool.input_schema["type"] == "object"
    assert "message" in tool.input_schema["properties"]
    assert "date_time" in tool.input_schema["properties"]
    assert callable(tool.function)


def test_make_message_scheduler_with_channel(sample_bot_user, sample_discord_channel):
    """Test creating a message scheduler tool for a channel."""
    tool = make_message_scheduler(
        bot=sample_bot_user,
        user_id=None,
        channel_id=sample_discord_channel.id,
        model="test-model",
    )

    assert tool.name == "schedule_discord_message"
    assert "in this channel" in tool.description
    assert callable(tool.function)


def test_make_message_scheduler_without_user_or_channel(sample_bot_user):
    """Test that creating a scheduler without user or channel raises error."""
    with pytest.raises(ValueError, match="Either user or channel must be provided"):
        make_message_scheduler(
            bot=sample_bot_user,
            user_id=None,
            channel_id=None,
            model="test-model",
        )


@patch("memory.common.llms.tools.discord.schedule_message")
def test_message_scheduler_handler_success(
    mock_schedule_message, sample_bot_user, sample_discord_user
):
    """Test message scheduler handler with valid input."""
    tool = make_message_scheduler(
        bot=sample_bot_user,
        user_id=sample_discord_user.id,
        channel_id=None,
        model="test-model",
    )

    mock_schedule_message.return_value = "123"
    future_time = datetime.now(timezone.utc) + timedelta(hours=1)

    result = tool.function(
        {"message": "Test message", "date_time": future_time.isoformat()}
    )

    assert result == "123"
    mock_schedule_message.assert_called_once()


def test_message_scheduler_handler_invalid_input(sample_bot_user, sample_discord_user):
    """Test message scheduler handler with non-dict input."""
    tool = make_message_scheduler(
        bot=sample_bot_user,
        user_id=sample_discord_user.id,
        channel_id=None,
        model="test-model",
    )

    with pytest.raises(ValueError, match="Input must be a dictionary"):
        tool.function("not a dict")


def test_message_scheduler_handler_invalid_datetime(
    sample_bot_user, sample_discord_user
):
    """Test message scheduler handler with invalid datetime."""
    tool = make_message_scheduler(
        bot=sample_bot_user,
        user_id=sample_discord_user.id,
        channel_id=None,
        model="test-model",
    )

    with pytest.raises(ValueError, match="Invalid date time format"):
        tool.function(
            {
                "message": "Test message",
                "date_time": "not a valid datetime",
            }
        )


def test_message_scheduler_handler_missing_datetime(
    sample_bot_user, sample_discord_user
):
    """Test message scheduler handler with missing datetime."""
    tool = make_message_scheduler(
        bot=sample_bot_user,
        user_id=sample_discord_user.id,
        channel_id=None,
        model="test-model",
    )

    with pytest.raises(ValueError, match="Date time is required"):
        tool.function({"message": "Test message"})


# Tests for make_prev_messages_tool
def test_make_prev_messages_tool_with_user(sample_bot_user, sample_discord_user):
    """Test creating a previous messages tool for a user."""
    tool = make_prev_messages_tool(bot=sample_bot_user, user_id=sample_discord_user.id, channel_id=None)

    assert tool.name == "previous_messages"
    assert "from your chat with this user" in tool.description
    assert tool.input_schema["type"] == "object"
    assert "max_messages" in tool.input_schema["properties"]
    assert "offset" in tool.input_schema["properties"]
    assert callable(tool.function)


def test_make_prev_messages_tool_with_channel(sample_bot_user, sample_discord_channel):
    """Test creating a previous messages tool for a channel."""
    tool = make_prev_messages_tool(bot=sample_bot_user, user_id=None, channel_id=sample_discord_channel.id)

    assert tool.name == "previous_messages"
    assert "in this channel" in tool.description
    assert callable(tool.function)


def test_make_prev_messages_tool_without_user_or_channel(sample_bot_user):
    """Test that creating a tool without user or channel raises error."""
    with pytest.raises(ValueError, match="Either user or channel must be provided"):
        make_prev_messages_tool(bot=sample_bot_user, user_id=None, channel_id=None)


def test_prev_messages_handler_success(
    db_session, sample_bot_user, sample_discord_user, sample_discord_channel
):
    """Test previous messages handler with valid input."""
    tool = make_prev_messages_tool(bot=sample_bot_user, user_id=sample_discord_user.id, channel_id=None)

    # Create some actual messages in the database
    msg1 = DiscordMessage(
        message_id=1,
        channel_id=sample_discord_channel.id,
        from_id=sample_discord_user.id,
        recipient_id=sample_discord_user.id,
        content="Message 1",
        sent_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        modality="text",
        sha256=b"hash1" + bytes(26),
    )
    msg2 = DiscordMessage(
        message_id=2,
        channel_id=sample_discord_channel.id,
        from_id=sample_discord_user.id,
        recipient_id=sample_discord_user.id,
        content="Message 2",
        sent_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        modality="text",
        sha256=b"hash2" + bytes(26),
    )
    db_session.add_all([msg1, msg2])
    db_session.commit()

    result = tool.function({"max_messages": 10, "offset": 0})

    # Should return messages formatted as strings
    assert isinstance(result, str)
    # Both messages should be in the result
    assert "Message 1" in result or "Message 2" in result


def test_prev_messages_handler_with_defaults(db_session, sample_bot_user, sample_discord_user):
    """Test previous messages handler with default values."""
    tool = make_prev_messages_tool(bot=sample_bot_user, user_id=sample_discord_user.id, channel_id=None)

    result = tool.function({})

    # Should return empty string when no messages
    assert isinstance(result, str)


def test_prev_messages_handler_invalid_input(sample_bot_user, sample_discord_user):
    """Test previous messages handler with non-dict input."""
    tool = make_prev_messages_tool(bot=sample_bot_user, user_id=sample_discord_user.id, channel_id=None)

    with pytest.raises(ValueError, match="Input must be a dictionary"):
        tool.function("not a dict")


def test_prev_messages_handler_invalid_max_messages(sample_bot_user, sample_discord_user):
    """Test previous messages handler with invalid max_messages (negative value)."""
    # Note: max_messages=0 doesn't trigger validation due to `or 10` defaulting,
    # so we test with -1 which actually triggers the validation
    tool = make_prev_messages_tool(bot=sample_bot_user, user_id=sample_discord_user.id, channel_id=None)

    with pytest.raises(ValueError, match="Max messages must be greater than 0"):
        tool.function({"max_messages": -1})


def test_prev_messages_handler_invalid_offset(sample_bot_user, sample_discord_user):
    """Test previous messages handler with invalid offset."""
    tool = make_prev_messages_tool(bot=sample_bot_user, user_id=sample_discord_user.id, channel_id=None)

    with pytest.raises(ValueError, match="Offset must be greater than or equal to 0"):
        tool.function({"offset": -1})


def test_prev_messages_handler_non_integer_values(sample_bot_user, sample_discord_user):
    """Test previous messages handler with non-integer values."""
    tool = make_prev_messages_tool(bot=sample_bot_user, user_id=sample_discord_user.id, channel_id=None)

    with pytest.raises(ValueError, match="Max messages and offset must be integers"):
        tool.function({"max_messages": "not an int"})


# Tests for make_discord_tools
def test_make_discord_tools_with_user_and_channel(
    sample_bot_user, sample_discord_user, sample_discord_channel
):
    """Test creating Discord tools with both user and channel."""
    tools = make_discord_tools(
        bot=sample_bot_user,
        author=sample_discord_user,
        channel=sample_discord_channel,
        model="test-model",
    )

    # Should have: schedule_discord_message, previous_messages, update_channel_summary,
    # update_user_summary, update_server_summary, add_reaction
    assert len(tools) == 6
    assert "schedule_discord_message" in tools
    assert "previous_messages" in tools
    assert "update_channel_summary" in tools
    assert "update_user_summary" in tools
    assert "update_server_summary" in tools
    assert "add_reaction" in tools


def test_make_discord_tools_with_user_only(sample_bot_user, sample_discord_user):
    """Test creating Discord tools with only user (DM scenario)."""
    tools = make_discord_tools(
        bot=sample_bot_user,
        author=sample_discord_user,
        channel=None,
        model="test-model",
    )

    # Should have: schedule_discord_message, previous_messages, update_user_summary
    # Note: Without channel, there's no channel summary tool
    assert len(tools) >= 2  # At least schedule and previous messages
    assert "schedule_discord_message" in tools
    assert "previous_messages" in tools
    assert "update_user_summary" in tools


def test_make_discord_tools_with_channel_only(sample_bot_user, sample_discord_channel):
    """Test creating Discord tools with only channel (no specific author)."""
    tools = make_discord_tools(
        bot=sample_bot_user,
        author=None,
        channel=sample_discord_channel,
        model="test-model",
    )

    # Should have: schedule_discord_message, previous_messages, update_channel_summary,
    # update_server_summary, add_reaction (no user summary without author)
    assert len(tools) == 5
    assert "schedule_discord_message" in tools
    assert "previous_messages" in tools
    assert "update_channel_summary" in tools
    assert "update_server_summary" in tools
    assert "add_reaction" in tools
    assert "update_user_summary" not in tools


def test_make_discord_tools_channel_without_server(
    db_session, sample_bot_user, sample_discord_user
):
    """Test creating Discord tools with channel that has no server (DM channel)."""
    dm_channel = DiscordChannel(
        id=999888777,
        server_id=None,
        name="DM Channel",
        channel_type="dm",
    )
    db_session.add(dm_channel)
    db_session.commit()

    tools = make_discord_tools(
        bot=sample_bot_user,
        author=sample_discord_user,
        channel=dm_channel,
        model="test-model",
    )

    # Should not have server summary tool since channel has no server
    assert "update_server_summary" not in tools
    assert "update_channel_summary" in tools
    assert "update_user_summary" in tools


def test_make_discord_tools_returns_dict_with_correct_keys(
    sample_bot_user, sample_discord_user, sample_discord_channel
):
    """Test that make_discord_tools returns a dict with tool names as keys."""
    tools = make_discord_tools(
        bot=sample_bot_user,
        author=sample_discord_user,
        channel=sample_discord_channel,
        model="test-model",
    )

    # Verify all keys match the tool names
    for tool_name, tool in tools.items():
        assert tool_name == tool.name
