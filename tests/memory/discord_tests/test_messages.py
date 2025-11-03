"""Tests for Discord message helper functions."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from datetime import datetime, timedelta, timezone
from memory.discord.messages import (
    resolve_discord_user,
    resolve_discord_channel,
    schedule_discord_message,
    upsert_scheduled_message,
    previous_messages,
    comm_channel_prompt,
    call_llm,
)
from memory.common.db.models import (
    DiscordUser,
    DiscordChannel,
    DiscordServer,
    DiscordMessage,
    HumanUser,
    ScheduledLLMCall,
)
from memory.common.llms.tools import MCPServer as MCPServerDefinition


@pytest.fixture
def sample_discord_user(db_session):
    """Create a sample Discord user."""
    user = DiscordUser(id=123456789, username="testuser")
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def sample_discord_channel(db_session):
    """Create a sample Discord channel."""
    server = DiscordServer(id=987654321, name="Test Server")
    channel = DiscordChannel(
        id=111222333, name="general", channel_type="text", server_id=server.id
    )
    db_session.add_all([server, channel])
    db_session.commit()
    return channel


@pytest.fixture
def sample_system_user(db_session):
    """Create a sample system user."""
    user = HumanUser.create_with_password(
        email="user@example.com", name="Test User", password="password123"
    )
    db_session.add(user)
    db_session.commit()
    return user


# Test resolve_discord_user


def test_resolve_discord_user_with_none(db_session):
    """Test resolving None returns None."""
    result = resolve_discord_user(db_session, None)
    assert result is None


def test_resolve_discord_user_with_discord_user_object(
    db_session, sample_discord_user
):
    """Test resolving a DiscordUser object returns it unchanged."""
    result = resolve_discord_user(db_session, sample_discord_user)
    assert result == sample_discord_user
    assert result.id == 123456789


def test_resolve_discord_user_with_id(db_session, sample_discord_user):
    """Test resolving by integer ID."""
    result = resolve_discord_user(db_session, 123456789)
    assert result is not None
    assert result.id == 123456789
    assert result.username == "testuser"


def test_resolve_discord_user_with_username(db_session, sample_discord_user):
    """Test resolving by username string."""
    result = resolve_discord_user(db_session, "testuser")
    assert result is not None
    assert result.username == "testuser"


def test_resolve_discord_user_with_nonexistent_username_returns_none(db_session):
    """Test that resolving a non-existent username returns None."""
    result = resolve_discord_user(db_session, "nonexistent")
    assert result is None


# Test resolve_discord_channel


def test_resolve_discord_channel_with_none(db_session):
    """Test resolving None returns None."""
    result = resolve_discord_channel(db_session, None)
    assert result is None


def test_resolve_discord_channel_with_channel_object(
    db_session, sample_discord_channel
):
    """Test resolving a DiscordChannel object returns it unchanged."""
    result = resolve_discord_channel(db_session, sample_discord_channel)
    assert result == sample_discord_channel
    assert result.id == 111222333


def test_resolve_discord_channel_with_id(db_session, sample_discord_channel):
    """Test resolving by integer ID."""
    result = resolve_discord_channel(db_session, 111222333)
    assert result is not None
    assert result.id == 111222333
    assert result.name == "general"


def test_resolve_discord_channel_with_name(db_session, sample_discord_channel):
    """Test resolving by channel name string."""
    result = resolve_discord_channel(db_session, "general")
    assert result is not None
    assert result.name == "general"


def test_resolve_discord_channel_returns_none_if_not_found(db_session):
    """Test that resolving a non-existent channel returns None."""
    result = resolve_discord_channel(db_session, "nonexistent")
    assert result is None


# Test schedule_discord_message


def test_schedule_discord_message_with_user(
    db_session, sample_discord_user, sample_system_user
):
    """Test scheduling a message to a Discord user."""
    future_time = datetime.now(timezone.utc) + timedelta(hours=1)

    result = schedule_discord_message(
        db_session,
        scheduled_time=future_time,
        message="Test message",
        user_id=sample_system_user.id,
        discord_user=sample_discord_user,
        model="test-model",
        topic="Test Topic",
    )
    db_session.flush()  # Flush to populate the foreign key IDs

    assert result is not None
    assert isinstance(result, ScheduledLLMCall)
    assert result.message == "Test message"
    assert result.discord_user_id == sample_discord_user.id
    assert result.user_id == sample_system_user.id


def test_schedule_discord_message_with_channel(
    db_session, sample_discord_channel, sample_system_user
):
    """Test scheduling a message to a Discord channel."""
    future_time = datetime.now(timezone.utc) + timedelta(hours=1)

    result = schedule_discord_message(
        db_session,
        scheduled_time=future_time,
        message="Channel message",
        user_id=sample_system_user.id,
        discord_channel=sample_discord_channel,
    )
    db_session.flush()  # Flush to populate the foreign key IDs

    assert result is not None
    assert result.discord_channel_id == sample_discord_channel.id


def test_schedule_discord_message_requires_user_or_channel(
    db_session, sample_system_user
):
    """Test that scheduling requires either user or channel."""
    future_time = datetime.now(timezone.utc) + timedelta(hours=1)

    with pytest.raises(ValueError, match="Either discord_user or discord_channel must be provided"):
        schedule_discord_message(
            db_session,
            scheduled_time=future_time,
            message="Test",
            user_id=sample_system_user.id,
        )


def test_schedule_discord_message_requires_future_time(
    db_session, sample_discord_user, sample_system_user
):
    """Test that scheduling requires a future time."""
    past_time = datetime.now(timezone.utc) - timedelta(hours=1)

    with pytest.raises(ValueError, match="Scheduled time must be in the future"):
        schedule_discord_message(
            db_session,
            scheduled_time=past_time,
            message="Test",
            user_id=sample_system_user.id,
            discord_user=sample_discord_user,
        )


def test_schedule_discord_message_with_metadata(
    db_session, sample_discord_user, sample_system_user
):
    """Test scheduling with custom metadata."""
    future_time = datetime.now(timezone.utc) + timedelta(hours=1)
    metadata = {"priority": "high", "tags": ["urgent"]}

    result = schedule_discord_message(
        db_session,
        scheduled_time=future_time,
        message="Urgent message",
        user_id=sample_system_user.id,
        discord_user=sample_discord_user,
        metadata=metadata,
    )

    assert result.data == metadata


# Test upsert_scheduled_message


def test_upsert_scheduled_message_creates_new(
    db_session, sample_discord_user, sample_system_user
):
    """Test upserting creates a new message if none exists."""
    future_time = datetime.now(timezone.utc) + timedelta(hours=1)

    result = upsert_scheduled_message(
        db_session,
        scheduled_time=future_time,
        message="New message",
        user_id=sample_system_user.id,
        discord_user=sample_discord_user,
        model="test-model",
    )

    assert result is not None
    assert result.message == "New message"


def test_upsert_scheduled_message_cancels_earlier_call(
    db_session, sample_discord_user, sample_system_user
):
    """Test upserting cancels an earlier scheduled call for the same user/channel."""
    future_time1 = datetime.now(timezone.utc) + timedelta(hours=2)
    future_time2 = datetime.now(timezone.utc) + timedelta(hours=1)

    # Create first scheduled message
    first_call = schedule_discord_message(
        db_session,
        scheduled_time=future_time1,
        message="First message",
        user_id=sample_system_user.id,
        discord_user=sample_discord_user,
        model="test-model",
    )
    db_session.commit()

    # Upsert with earlier time should cancel the first
    second_call = upsert_scheduled_message(
        db_session,
        scheduled_time=future_time2,
        message="Second message",
        user_id=sample_system_user.id,
        discord_user=sample_discord_user,
        model="test-model",
    )
    db_session.commit()

    db_session.refresh(first_call)
    assert first_call.status == "cancelled"
    assert second_call.status == "pending"


# Test previous_messages


def test_previous_messages_empty(db_session):
    """Test getting previous messages when none exist."""
    result = previous_messages(db_session, user_id=123, channel_id=456)
    assert result == []


def test_previous_messages_filters_by_user(db_session, sample_discord_user, sample_discord_channel):
    """Test filtering messages by recipient user."""
    # Create some messages
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

    result = previous_messages(db_session, user_id=sample_discord_user.id, channel_id=None)
    assert len(result) == 2
    # Should be in chronological order (oldest first)
    assert result[0].message_id == 1
    assert result[1].message_id == 2


def test_previous_messages_limits_results(db_session, sample_discord_user, sample_discord_channel):
    """Test limiting the number of previous messages."""
    # Create 15 messages
    for i in range(15):
        msg = DiscordMessage(
            message_id=i,
            channel_id=sample_discord_channel.id,
            from_id=sample_discord_user.id,
            recipient_id=sample_discord_user.id,
            content=f"Message {i}",
            sent_at=datetime.now(timezone.utc) - timedelta(minutes=15 - i),
            modality="text",
            sha256=f"hash{i}".encode() + bytes(26),
        )
        db_session.add(msg)
    db_session.commit()

    result = previous_messages(
        db_session, user_id=sample_discord_user.id, channel_id=None, max_messages=5
    )
    assert len(result) == 5


# Test comm_channel_prompt


def test_comm_channel_prompt_basic(db_session, sample_discord_user, sample_discord_channel):
    """Test generating a basic communication channel prompt."""
    result = comm_channel_prompt(
        db_session, user=sample_discord_user, channel=sample_discord_channel
    )

    assert "You are a bot communicating on Discord" in result
    assert isinstance(result, str)
    assert len(result) > 0


def test_comm_channel_prompt_includes_server_context(db_session, sample_discord_channel):
    """Test that prompt includes server context when available."""
    server = sample_discord_channel.server
    server.summary = "Gaming community server"
    db_session.commit()

    result = comm_channel_prompt(db_session, user=None, channel=sample_discord_channel)

    assert "server_context" in result.lower()
    assert "Gaming community server" in result


def test_comm_channel_prompt_includes_channel_context(db_session, sample_discord_channel):
    """Test that prompt includes channel context."""
    sample_discord_channel.summary = "General discussion channel"
    db_session.commit()

    result = comm_channel_prompt(db_session, user=None, channel=sample_discord_channel)

    assert "channel_context" in result.lower()
    assert "General discussion channel" in result


def test_comm_channel_prompt_includes_user_notes(
    db_session, sample_discord_user, sample_discord_channel
):
    """Test that prompt includes user notes from previous messages."""
    sample_discord_user.summary = "Helpful community member"
    db_session.commit()

    # Create a message from this user
    msg = DiscordMessage(
        message_id=1,
        from_id=sample_discord_user.id,
        recipient_id=sample_discord_user.id,
        channel_id=sample_discord_channel.id,
        content="Hello",
        sent_at=datetime.now(timezone.utc),
        modality="text",
        sha256=b"hash" + bytes(27),
    )
    db_session.add(msg)
    db_session.commit()

    result = comm_channel_prompt(
        db_session, user=sample_discord_user, channel=sample_discord_channel
    )

    assert "user_notes" in result.lower()
    assert "testuser" in result  # username should appear


@patch("memory.discord.messages.create_provider")
@patch("memory.discord.messages.previous_messages")
@patch("memory.common.llms.tools.discord.make_discord_tools")
@patch("memory.common.llms.tools.base.WebSearchTool")
def test_call_llm_includes_web_search_and_mcp_servers(
    mock_web_search,
    mock_make_tools,
    mock_prev_messages,
    mock_create_provider,
):
    provider = MagicMock()
    provider.usage_tracker.is_rate_limited.return_value = False
    provider.as_messages.return_value = ["converted"]
    provider.run_with_tools.return_value = SimpleNamespace(response="llm-output")
    mock_create_provider.return_value = provider

    mock_prev_messages.return_value = [SimpleNamespace(as_content=lambda: "prev")]

    existing_tool = MagicMock(name="existing_tool")
    mock_make_tools.return_value = {"existing": existing_tool}

    web_tool_instance = MagicMock(name="web_tool")
    mock_web_search.return_value = web_tool_instance

    bot_user = SimpleNamespace(system_user="system-user", system_prompt="bot prompt")
    from_user = SimpleNamespace(id=123)
    mcp_model = SimpleNamespace(
        name="Server",
        mcp_server_url="https://mcp.example.com",
        access_token="token123",
    )

    result = call_llm(
        session=MagicMock(),
        bot_user=bot_user,
        from_user=from_user,
        channel=None,
        model="gpt-test",
        messages=["hi"],
        mcp_servers=[mcp_model],
    )

    assert result == "llm-output"

    kwargs = provider.run_with_tools.call_args.kwargs
    tools = kwargs["tools"]
    assert tools["existing"] is existing_tool
    assert tools["web_search"] is web_tool_instance

    mcp_servers = kwargs["mcp_servers"]
    assert mcp_servers == [
        MCPServerDefinition(
            name="Server", url="https://mcp.example.com", token="token123"
        )
    ]


@patch("memory.discord.messages.create_provider")
@patch("memory.discord.messages.previous_messages")
@patch("memory.common.llms.tools.discord.make_discord_tools")
@patch("memory.common.llms.tools.base.WebSearchTool")
def test_call_llm_filters_disallowed_tools(
    mock_web_search,
    mock_make_tools,
    mock_prev_messages,
    mock_create_provider,
):
    provider = MagicMock()
    provider.usage_tracker.is_rate_limited.return_value = False
    provider.as_messages.return_value = ["converted"]
    provider.run_with_tools.return_value = SimpleNamespace(response="filtered-output")
    mock_create_provider.return_value = provider

    mock_prev_messages.return_value = []

    allowed_tool = MagicMock(name="allowed")
    blocked_tool = MagicMock(name="blocked")
    mock_make_tools.return_value = {
        "allowed": allowed_tool,
        "blocked": blocked_tool,
    }

    mock_web_search.return_value = MagicMock(name="web_tool")

    bot_user = SimpleNamespace(system_user="system-user", system_prompt=None)
    from_user = SimpleNamespace(id=1)

    call_llm(
        session=MagicMock(),
        bot_user=bot_user,
        from_user=from_user,
        channel=None,
        model="gpt-test",
        messages=[],
        allowed_tools={"allowed"},
        mcp_servers=None,
    )

    tools = provider.run_with_tools.call_args.kwargs["tools"]
    assert "allowed" in tools
    assert "blocked" not in tools
    assert "web_search" not in tools
