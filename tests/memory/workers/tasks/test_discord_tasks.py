import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from memory.common.db.models import (
    DiscordMessage,
    DiscordUser,
    DiscordServer,
    DiscordChannel,
)
from memory.workers.tasks import discord


@pytest.fixture
def mock_discord_user(db_session):
    """Create a Discord user for testing."""
    user = DiscordUser(
        id=123456789,
        username="testuser",
        ignore_messages=False,
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def mock_discord_server(db_session):
    """Create a Discord server for testing."""
    server = DiscordServer(
        id=987654321,
        name="Test Server",
        ignore_messages=False,
    )
    db_session.add(server)
    db_session.commit()
    return server


@pytest.fixture
def mock_discord_channel(db_session, mock_discord_server):
    """Create a Discord channel for testing."""
    channel = DiscordChannel(
        id=111222333,
        name="test-channel",
        channel_type="text",
        server_id=mock_discord_server.id,
        ignore_messages=False,
    )
    db_session.add(channel)
    db_session.commit()
    return channel


@pytest.fixture
def sample_message_data(mock_discord_user, mock_discord_channel):
    """Sample message data for testing."""
    return {
        "message_id": 999888777,
        "channel_id": mock_discord_channel.id,
        "author_id": mock_discord_user.id,
        "recipient_id": mock_discord_user.id,
        "content": "This is a test Discord message with enough content to be processed.",
        "sent_at": "2024-01-01T12:00:00Z",
        "server_id": None,
        "message_reference_id": None,
    }


def test_get_prev_returns_previous_messages(
    db_session, mock_discord_user, mock_discord_channel
):
    """Test that get_prev returns previous messages in order."""
    # Create previous messages
    msg1 = DiscordMessage(
        message_id=1,
        channel_id=mock_discord_channel.id,
        from_id=mock_discord_user.id,
        recipient_id=mock_discord_user.id,
        content="First message",
        sent_at=datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
        modality="text",
        sha256=b"hash1" + bytes(26),
    )
    msg2 = DiscordMessage(
        message_id=2,
        channel_id=mock_discord_channel.id,
        from_id=mock_discord_user.id,
        recipient_id=mock_discord_user.id,
        content="Second message",
        sent_at=datetime(2024, 1, 1, 10, 5, 0, tzinfo=timezone.utc),
        modality="text",
        sha256=b"hash2" + bytes(26),
    )
    msg3 = DiscordMessage(
        message_id=3,
        channel_id=mock_discord_channel.id,
        from_id=mock_discord_user.id,
        recipient_id=mock_discord_user.id,
        content="Third message",
        sent_at=datetime(2024, 1, 1, 10, 10, 0, tzinfo=timezone.utc),
        modality="text",
        sha256=b"hash3" + bytes(26),
    )
    db_session.add_all([msg1, msg2, msg3])
    db_session.commit()

    # Get previous messages before 10:15
    result = discord.get_prev(
        db_session,
        mock_discord_channel.id,
        datetime(2024, 1, 1, 10, 15, 0, tzinfo=timezone.utc),
    )

    assert len(result) == 3
    assert result[0] == "testuser: First message"
    assert result[1] == "testuser: Second message"
    assert result[2] == "testuser: Third message"


def test_get_prev_limits_context_window(
    db_session, mock_discord_user, mock_discord_channel
):
    """Test that get_prev respects DISCORD_CONTEXT_WINDOW setting."""
    # Create 15 messages (more than the default context window of 10)
    for i in range(15):
        msg = DiscordMessage(
            message_id=i,
            channel_id=mock_discord_channel.id,
            from_id=mock_discord_user.id,
        recipient_id=mock_discord_user.id,
            content=f"Message {i}",
            sent_at=datetime(2024, 1, 1, 10, i, 0, tzinfo=timezone.utc),
            modality="text",
            sha256=f"hash{i}".encode() + bytes(27),
        )
        db_session.add(msg)
    db_session.commit()

    result = discord.get_prev(
        db_session,
        mock_discord_channel.id,
        datetime(2024, 1, 1, 11, 0, 0, tzinfo=timezone.utc),
    )

    # Should only return last 10 messages
    assert len(result) == 10
    assert result[0] == "testuser: Message 5"  # Oldest in window
    assert result[-1] == "testuser: Message 14"  # Most recent


def test_get_prev_empty_channel(db_session, mock_discord_channel):
    """Test get_prev with no previous messages."""
    result = discord.get_prev(
        db_session,
        mock_discord_channel.id,
        datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
    )

    assert result == []


@patch("memory.common.settings.DISCORD_PROCESS_MESSAGES", True)
@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", True)
@patch("memory.workers.tasks.discord.create_provider")
def test_should_process_normal_message(
    mock_create_provider,
    db_session,
    mock_discord_user,
    mock_discord_server,
    mock_discord_channel,
):
    """Test should_process returns True for normal messages."""
    # Mock the LLM provider to return "yes"
    mock_provider = Mock()
    mock_provider.generate.return_value = "<response>yes</response>"
    mock_provider.as_messages.return_value = []
    mock_create_provider.return_value = mock_provider

    message = DiscordMessage(
        message_id=1,
        channel_id=mock_discord_channel.id,
        from_id=mock_discord_user.id,
        recipient_id=mock_discord_user.id,
        server_id=mock_discord_server.id,
        content="Test",
        sent_at=datetime.now(timezone.utc),
        modality="text",
        sha256=b"hash" + bytes(27),
    )
    db_session.add(message)
    db_session.commit()
    db_session.refresh(message)

    assert discord.should_process(message) is True


@patch("memory.common.settings.DISCORD_PROCESS_MESSAGES", False)
def test_should_process_disabled():
    """Test should_process returns False when processing is disabled."""
    message = Mock()
    assert discord.should_process(message) is False


@patch("memory.common.settings.DISCORD_PROCESS_MESSAGES", True)
@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", False)
def test_should_process_notifications_disabled():
    """Test should_process returns False when notifications are disabled."""
    message = Mock()
    assert discord.should_process(message) is False


@patch("memory.common.settings.DISCORD_PROCESS_MESSAGES", True)
@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", True)
def test_should_process_server_ignored(
    db_session, mock_discord_user, mock_discord_channel
):
    """Test should_process returns False when server has ignore_messages=True."""
    server = DiscordServer(
        id=123,
        name="Ignored Server",
        ignore_messages=True,
    )
    db_session.add(server)
    db_session.commit()

    message = DiscordMessage(
        message_id=1,
        channel_id=mock_discord_channel.id,
        from_id=mock_discord_user.id,
        recipient_id=mock_discord_user.id,
        server_id=server.id,
        content="Test",
        sent_at=datetime.now(timezone.utc),
        modality="text",
        sha256=b"hash" + bytes(27),
    )
    db_session.add(message)
    db_session.commit()
    db_session.refresh(message)

    assert discord.should_process(message) is False


@patch("memory.common.settings.DISCORD_PROCESS_MESSAGES", True)
@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", True)
def test_should_process_channel_ignored(
    db_session, mock_discord_user, mock_discord_server
):
    """Test should_process returns False when channel has ignore_messages=True."""
    channel = DiscordChannel(
        id=456,
        name="ignored-channel",
        channel_type="text",
        server_id=mock_discord_server.id,
        ignore_messages=True,
    )
    db_session.add(channel)
    db_session.commit()

    message = DiscordMessage(
        message_id=1,
        channel_id=channel.id,
        from_id=mock_discord_user.id,
        recipient_id=mock_discord_user.id,
        server_id=mock_discord_server.id,
        content="Test",
        sent_at=datetime.now(timezone.utc),
        modality="text",
        sha256=b"hash" + bytes(27),
    )
    db_session.add(message)
    db_session.commit()
    db_session.refresh(message)

    assert discord.should_process(message) is False


@patch("memory.common.settings.DISCORD_PROCESS_MESSAGES", True)
@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", True)
def test_should_process_user_ignored(
    db_session, mock_discord_server, mock_discord_channel
):
    """Test should_process returns False when user has ignore_messages=True."""
    user = DiscordUser(
        id=789,
        username="ignoreduser",
        ignore_messages=True,
    )
    db_session.add(user)
    db_session.commit()

    message = DiscordMessage(
        message_id=1,
        channel_id=mock_discord_channel.id,
        from_id=user.id,
        recipient_id=user.id,
        server_id=mock_discord_server.id,
        content="Test",
        sent_at=datetime.now(timezone.utc),
        modality="text",
        sha256=b"hash" + bytes(27),
    )
    db_session.add(message)
    db_session.commit()
    db_session.refresh(message)

    assert discord.should_process(message) is False


def test_add_discord_message_success(db_session, sample_message_data, qdrant):
    """Test successful Discord message addition."""
    result = discord.add_discord_message(**sample_message_data)

    assert result["status"] == "processed"
    assert "discordmessage_id" in result

    # Verify the message was created in the database
    message = (
        db_session.query(DiscordMessage)
        .filter_by(message_id=sample_message_data["message_id"])
        .first()
    )
    assert message is not None
    assert message.content == sample_message_data["content"]
    assert message.message_type == "default"
    assert message.reply_to_message_id is None


def test_add_discord_message_with_reply(db_session, sample_message_data, qdrant):
    """Test adding a Discord message that is a reply."""
    sample_message_data["message_reference_id"] = 111222333

    discord.add_discord_message(**sample_message_data)

    message = (
        db_session.query(DiscordMessage)
        .filter_by(message_id=sample_message_data["message_id"])
        .first()
    )
    assert message.message_type == "reply"
    assert message.reply_to_message_id == 111222333


def test_add_discord_message_already_exists(db_session, sample_message_data, qdrant):
    """Test adding a message that already exists."""
    # Add the message once
    discord.add_discord_message(**sample_message_data)

    # Try to add it again
    result = discord.add_discord_message(**sample_message_data)

    assert result["status"] == "already_exists"
    assert result["message_id"] == sample_message_data["message_id"]

    # Verify no duplicate was created
    messages = (
        db_session.query(DiscordMessage)
        .filter_by(message_id=sample_message_data["message_id"])
        .all()
    )
    assert len(messages) == 1


def test_add_discord_message_with_context(
    db_session, sample_message_data, mock_discord_user, qdrant
):
    """Test that message is added successfully when previous messages exist."""
    # Add a previous message
    prev_msg = DiscordMessage(
        message_id=111111,
        channel_id=sample_message_data["channel_id"],
        from_id=mock_discord_user.id,
        recipient_id=mock_discord_user.id,
        content="Previous message",
        sent_at=datetime(2024, 1, 1, 11, 0, 0, tzinfo=timezone.utc),
        modality="text",
        sha256=b"prev" + bytes(28),
    )
    db_session.add(prev_msg)
    db_session.commit()

    result = discord.add_discord_message(**sample_message_data)

    message = (
        db_session.query(DiscordMessage)
        .filter_by(message_id=sample_message_data["message_id"])
        .first()
    )
    assert message is not None
    assert result["status"] == "processed"


@patch("memory.workers.tasks.discord.should_process")
@patch("memory.workers.tasks.discord.process_discord_message")
@patch("memory.common.settings.DISCORD_PROCESS_MESSAGES", True)
@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", True)
def test_add_discord_message_triggers_processing(
    mock_process,
    mock_should_process,
    db_session,
    sample_message_data,
    mock_discord_server,
    mock_discord_channel,
    qdrant,
):
    """Test that add_discord_message triggers process_discord_message when conditions are met."""
    mock_should_process.return_value = True
    mock_process.delay = Mock()
    sample_message_data["server_id"] = mock_discord_server.id

    discord.add_discord_message(**sample_message_data)

    # Verify process_discord_message.delay was called
    mock_process.delay.assert_called_once()


@patch("memory.workers.tasks.discord.process_discord_message")
@patch("memory.common.settings.DISCORD_PROCESS_MESSAGES", False)
def test_add_discord_message_no_processing_when_disabled(
    mock_process, db_session, sample_message_data, qdrant
):
    """Test that process_discord_message is not called when processing is disabled."""
    mock_process.delay = Mock()

    discord.add_discord_message(**sample_message_data)

    mock_process.delay.assert_not_called()


def test_edit_discord_message_success(db_session, sample_message_data, qdrant):
    """Test successful Discord message edit."""
    # First add the message
    discord.add_discord_message(**sample_message_data)

    # Edit it
    new_content = (
        "This is the edited content with enough text to be meaningful and processed."
    )
    edited_at = "2024-01-01T13:00:00Z"

    result = discord.edit_discord_message(
        sample_message_data["message_id"],
        new_content,
        edited_at,
    )

    assert result["status"] == "processed"

    # Verify the message was updated
    message = (
        db_session.query(DiscordMessage)
        .filter_by(message_id=sample_message_data["message_id"])
        .first()
    )
    assert message.content == new_content
    assert message.edited_at is not None


def test_edit_discord_message_not_found(db_session):
    """Test editing a message that doesn't exist."""
    result = discord.edit_discord_message(
        999999,
        "New content",
        "2024-01-01T13:00:00Z",
    )

    assert result["status"] == "error"
    assert result["error"] == "Message not found"
    assert result["message_id"] == 999999


def test_edit_discord_message_updates_context(
    db_session, sample_message_data, mock_discord_user, qdrant
):
    """Test that editing a message works correctly."""
    # Add previous message and the message to be edited
    prev_msg = DiscordMessage(
        message_id=111111,
        channel_id=sample_message_data["channel_id"],
        from_id=mock_discord_user.id,
        recipient_id=mock_discord_user.id,
        content="Previous message",
        sent_at=datetime(2024, 1, 1, 11, 0, 0, tzinfo=timezone.utc),
        modality="text",
        sha256=b"prev" + bytes(28),
    )
    db_session.add(prev_msg)
    db_session.commit()

    discord.add_discord_message(**sample_message_data)

    # Edit the message
    result = discord.edit_discord_message(
        sample_message_data["message_id"],
        "Edited content that should have context updated properly.",
        "2024-01-01T13:00:00Z",
    )

    # Verify message was updated
    message = (
        db_session.query(DiscordMessage)
        .filter_by(message_id=sample_message_data["message_id"])
        .first()
    )
    assert (
        message.content == "Edited content that should have context updated properly."
    )
    assert result["status"] == "processed"


def test_process_discord_message_success(db_session, sample_message_data, qdrant):
    """Test processing a Discord message."""
    # Add a message first
    add_result = discord.add_discord_message(**sample_message_data)
    message_id = add_result["discordmessage_id"]

    # Process it
    result = discord.process_discord_message(message_id)

    assert result["status"] == "processed"
    assert result["message_id"] == message_id


def test_process_discord_message_not_found(db_session):
    """Test processing a message that doesn't exist."""
    result = discord.process_discord_message(999999)

    assert result["status"] == "error"
    assert result["error"] == "Message not found"
    assert result["message_id"] == 999999


@pytest.mark.parametrize(
    "sent_at_str,expected_hour",
    [
        ("2024-01-01T12:00:00Z", 12),
        ("2024-01-01T00:00:00+00:00", 0),
        ("2024-01-01T23:59:59Z", 23),
    ],
)
def test_add_discord_message_datetime_parsing(
    db_session, sample_message_data, sent_at_str, expected_hour, qdrant
):
    """Test that various datetime formats are parsed correctly."""
    sample_message_data["sent_at"] = sent_at_str

    discord.add_discord_message(**sample_message_data)

    message = (
        db_session.query(DiscordMessage)
        .filter_by(message_id=sample_message_data["message_id"])
        .first()
    )
    assert message.sent_at.hour == expected_hour


def test_add_discord_message_unique_hash(db_session, sample_message_data, qdrant):
    """Test that message hash includes message_id for uniqueness."""
    # Add first message
    discord.add_discord_message(**sample_message_data)

    # Try to add another message with same content but different message_id
    sample_message_data["message_id"] = 888777666

    result = discord.add_discord_message(**sample_message_data)

    # Should succeed because hash includes message_id
    assert result["status"] == "processed"

    # Verify both messages exist
    messages = (
        db_session.query(DiscordMessage)
        .filter_by(content=sample_message_data["content"])
        .all()
    )
    assert len(messages) == 2


def test_get_prev_only_returns_messages_from_same_channel(
    db_session, mock_discord_user, mock_discord_server
):
    """Test that get_prev only returns messages from the specified channel."""
    # Create two channels
    channel1 = DiscordChannel(
        id=111,
        name="channel-1",
        channel_type="text",
        server_id=mock_discord_server.id,
    )
    channel2 = DiscordChannel(
        id=222,
        name="channel-2",
        channel_type="text",
        server_id=mock_discord_server.id,
    )
    db_session.add_all([channel1, channel2])
    db_session.commit()

    # Add messages to both channels
    msg1 = DiscordMessage(
        message_id=1,
        channel_id=channel1.id,
        from_id=mock_discord_user.id,
        recipient_id=mock_discord_user.id,
        content="Message in channel 1",
        sent_at=datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
        modality="text",
        sha256=b"hash1" + bytes(26),
    )
    msg2 = DiscordMessage(
        message_id=2,
        channel_id=channel2.id,
        from_id=mock_discord_user.id,
        recipient_id=mock_discord_user.id,
        content="Message in channel 2",
        sent_at=datetime(2024, 1, 1, 10, 5, 0, tzinfo=timezone.utc),
        modality="text",
        sha256=b"hash2" + bytes(26),
    )
    db_session.add_all([msg1, msg2])
    db_session.commit()

    # Get previous messages for channel 1
    result = discord.get_prev(
        db_session,
        channel1.id,  # type: ignore
        datetime(2024, 1, 1, 11, 0, 0, tzinfo=timezone.utc),
    )

    # Should only return message from channel 1
    assert len(result) == 1
    assert "Message in channel 1" in result[0]
    assert "Message in channel 2" not in result[0]
