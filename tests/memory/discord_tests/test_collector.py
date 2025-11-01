import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, patch, AsyncMock, MagicMock

import discord

from memory.discord.collector import (
    create_or_update_server,
    determine_channel_metadata,
    create_or_update_channel,
    create_or_update_user,
    determine_message_metadata,
    should_track_message,
    should_collect_bot_message,
    sync_guild_metadata,
    MessageCollector,
)
from memory.common.db.models import (
    DiscordServer,
    DiscordChannel,
    DiscordUser,
)


# Fixtures for Discord objects
@pytest.fixture
def mock_guild():
    """Mock Discord Guild object"""
    guild = Mock(spec=discord.Guild)
    guild.id = 123456789
    guild.name = "Test Server"
    guild.description = "A test server"
    guild.member_count = 42
    return guild


@pytest.fixture
def mock_text_channel():
    """Mock Discord TextChannel object"""
    channel = Mock(spec=discord.TextChannel)
    channel.id = 987654321
    channel.name = "general"
    guild = Mock()
    guild.id = 123456789
    channel.guild = guild
    return channel


@pytest.fixture
def mock_dm_channel():
    """Mock Discord DMChannel object"""
    channel = Mock(spec=discord.DMChannel)
    channel.id = 111222333
    recipient = Mock()
    recipient.name = "TestUser"
    channel.recipient = recipient
    return channel


@pytest.fixture
def mock_user():
    """Mock Discord User object"""
    user = Mock(spec=discord.User)
    user.id = 444555666
    user.name = "testuser"
    user.display_name = "Test User"
    user.bot = False
    return user


@pytest.fixture
def mock_message(mock_text_channel, mock_user):
    """Mock Discord Message object"""
    message = Mock(spec=discord.Message)
    message.id = 777888999
    message.channel = mock_text_channel
    message.author = mock_user
    message.guild = mock_text_channel.guild
    message.content = "Test message"
    message.created_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    message.reference = None
    return message


# Tests for create_or_update_server
def test_create_or_update_server_creates_new(db_session, mock_guild):
    """Test creating a new server record"""
    result = create_or_update_server(db_session, mock_guild)

    assert result is not None
    assert result.id == mock_guild.id
    assert result.name == mock_guild.name
    assert result.description == mock_guild.description
    assert result.member_count == mock_guild.member_count


def test_create_or_update_server_updates_existing(db_session, mock_guild):
    """Test updating an existing server record"""
    # Create initial server
    server = DiscordServer(
        id=mock_guild.id,
        name="Old Name",
        description="Old Description",
        member_count=10,
    )
    db_session.add(server)
    db_session.commit()

    # Update with new data
    mock_guild.name = "New Name"
    mock_guild.description = "New Description"
    mock_guild.member_count = 50

    result = create_or_update_server(db_session, mock_guild)

    assert result.name == "New Name"
    assert result.description == "New Description"
    assert result.member_count == 50
    assert result.last_sync_at is not None


def test_create_or_update_server_none_guild(db_session):
    """Test with None guild"""
    result = create_or_update_server(db_session, None)
    assert result is None


# Tests for determine_channel_metadata
def test_determine_channel_metadata_dm():
    """Test metadata for DM channel"""
    channel = Mock(spec=discord.DMChannel)
    channel.recipient = Mock()
    channel.recipient.name = "TestUser"

    channel_type, server_id, name = determine_channel_metadata(channel)

    assert channel_type == "dm"
    assert server_id is None
    assert "DM with TestUser" in name


def test_determine_channel_metadata_dm_no_recipient():
    """Test metadata for DM channel without recipient"""
    channel = Mock(spec=discord.DMChannel)
    channel.recipient = None

    channel_type, server_id, name = determine_channel_metadata(channel)

    assert channel_type == "dm"
    assert name == "Unknown DM"


def test_determine_channel_metadata_group_dm():
    """Test metadata for group DM channel"""
    channel = Mock(spec=discord.GroupChannel)
    channel.name = "Group Chat"

    channel_type, server_id, name = determine_channel_metadata(channel)

    assert channel_type == "group_dm"
    assert server_id is None
    assert name == "Group Chat"


def test_determine_channel_metadata_group_dm_no_name():
    """Test metadata for group DM without name"""
    channel = Mock(spec=discord.GroupChannel)
    channel.name = None

    channel_type, server_id, name = determine_channel_metadata(channel)

    assert name == "Group DM"


def test_determine_channel_metadata_text_channel():
    """Test metadata for text channel"""
    channel = Mock(spec=discord.TextChannel)
    channel.name = "general"
    channel.guild = Mock()
    channel.guild.id = 123

    channel_type, server_id, name = determine_channel_metadata(channel)

    assert channel_type == "text"
    assert server_id == 123
    assert name == "general"


def test_determine_channel_metadata_voice_channel():
    """Test metadata for voice channel"""
    channel = Mock(spec=discord.VoiceChannel)
    channel.name = "voice-chat"
    channel.guild = Mock()
    channel.guild.id = 456

    channel_type, server_id, name = determine_channel_metadata(channel)

    assert channel_type == "voice"
    assert server_id == 456
    assert name == "voice-chat"


def test_determine_channel_metadata_thread():
    """Test metadata for thread"""
    channel = Mock(spec=discord.Thread)
    channel.name = "thread-1"
    channel.guild = Mock()
    channel.guild.id = 789

    channel_type, server_id, name = determine_channel_metadata(channel)

    assert channel_type == "thread"
    assert server_id == 789
    assert name == "thread-1"


def test_determine_channel_metadata_unknown():
    """Test metadata for unknown channel type"""
    channel = Mock()
    channel.id = 999
    # Ensure the mock doesn't have a 'name' attribute
    del channel.name

    channel_type, server_id, name = determine_channel_metadata(channel)

    assert channel_type == "unknown"
    assert name == "Unknown-999"


# Tests for create_or_update_channel
def test_create_or_update_channel_creates_new(
    db_session, mock_text_channel, mock_guild
):
    """Test creating a new channel record"""
    # Create the server first to satisfy foreign key constraint
    create_or_update_server(db_session, mock_guild)

    result = create_or_update_channel(db_session, mock_text_channel)

    assert result is not None
    assert result.id == mock_text_channel.id
    assert result.name == mock_text_channel.name
    assert result.channel_type == "text"


def test_create_or_update_channel_updates_existing(db_session, mock_text_channel):
    """Test updating an existing channel record"""
    # Create initial channel
    channel = DiscordChannel(
        id=mock_text_channel.id,
        name="old-name",
        channel_type="text",
    )
    db_session.add(channel)
    db_session.commit()

    # Update with new name
    mock_text_channel.name = "new-name"

    result = create_or_update_channel(db_session, mock_text_channel)

    assert result.name == "new-name"


def test_create_or_update_channel_none_channel(db_session):
    """Test with None channel"""
    result = create_or_update_channel(db_session, None)
    assert result is None


# Tests for create_or_update_user
def test_create_or_update_user_creates_new(db_session, mock_user):
    """Test creating a new user record"""
    result = create_or_update_user(db_session, mock_user)

    assert result is not None
    assert result.id == mock_user.id
    assert result.username == mock_user.name
    assert result.display_name == mock_user.display_name


def test_create_or_update_user_updates_existing(db_session, mock_user):
    """Test updating an existing user record"""
    # Create initial user
    user = DiscordUser(
        id=mock_user.id,
        username="oldname",
        display_name="Old Display Name",
    )
    db_session.add(user)
    db_session.commit()

    # Update with new data
    mock_user.name = "newname"
    mock_user.display_name = "New Display Name"

    result = create_or_update_user(db_session, mock_user)

    assert result.username == "newname"
    assert result.display_name == "New Display Name"


def test_create_or_update_user_none_user(db_session):
    """Test with None user"""
    result = create_or_update_user(db_session, None)
    assert result is None


# Tests for determine_message_metadata
def test_determine_message_metadata_default():
    """Test metadata for default message"""
    message = Mock()
    message.reference = None
    message.channel = Mock()
    # Ensure channel doesn't have parent attribute
    del message.channel.parent

    message_type, reply_to_id, thread_id = determine_message_metadata(message)

    assert message_type == "default"
    assert reply_to_id is None
    assert thread_id is None


def test_determine_message_metadata_reply():
    """Test metadata for reply message"""
    message = Mock()
    message.reference = Mock()
    message.reference.message_id = 123456
    message.channel = Mock()

    message_type, reply_to_id, thread_id = determine_message_metadata(message)

    assert message_type == "reply"
    assert reply_to_id == 123456


def test_determine_message_metadata_thread():
    """Test metadata for message in thread"""
    message = Mock()
    message.reference = None
    message.channel = Mock()
    message.channel.id = 999
    message.channel.parent = Mock()  # Has parent means it's a thread

    message_type, reply_to_id, thread_id = determine_message_metadata(message)

    assert thread_id == 999


# Tests for should_track_message
def test_should_track_message_server_disabled(db_session):
    """Test when server has tracking disabled"""
    server = DiscordServer(id=1, name="Server", track_messages=False)
    channel = DiscordChannel(id=2, name="Channel", channel_type="text")
    user = DiscordUser(id=3, username="User")

    result = should_track_message(server, channel, user)

    assert result is False


def test_should_track_message_channel_disabled(db_session):
    """Test when channel has tracking disabled"""
    server = DiscordServer(id=1, name="Server", track_messages=True)
    channel = DiscordChannel(
        id=2, name="Channel", channel_type="text", track_messages=False
    )
    user = DiscordUser(id=3, username="User")

    result = should_track_message(server, channel, user)

    assert result is False


def test_should_track_message_dm_allowed(db_session):
    """Test DM tracking when user allows it"""
    channel = DiscordChannel(id=2, name="DM", channel_type="dm", track_messages=True)
    user = DiscordUser(id=3, username="User", track_messages=True)

    result = should_track_message(None, channel, user)

    assert result is True


def test_should_track_message_dm_not_allowed(db_session):
    """Test DM tracking when user doesn't allow it"""
    channel = DiscordChannel(id=2, name="DM", channel_type="dm", track_messages=True)
    user = DiscordUser(id=3, username="User", track_messages=False)

    result = should_track_message(None, channel, user)

    assert result is False


def test_should_track_message_default_true(db_session):
    """Test default tracking behavior"""
    server = DiscordServer(id=1, name="Server", track_messages=True)
    channel = DiscordChannel(
        id=2, name="Channel", channel_type="text", track_messages=True
    )
    user = DiscordUser(id=3, username="User")

    result = should_track_message(server, channel, user)

    assert result is True


# Tests for should_collect_bot_message
@patch("memory.common.settings.DISCORD_COLLECT_BOTS", False)
def test_should_collect_bot_message_bot_not_allowed():
    """Test bot message collection when disabled"""
    message = Mock()
    message.author = Mock()
    message.author.bot = True

    result = should_collect_bot_message(message)

    assert result is False


@patch("memory.common.settings.DISCORD_COLLECT_BOTS", True)
def test_should_collect_bot_message_bot_allowed():
    """Test bot message collection when enabled"""
    message = Mock()
    message.author = Mock()
    message.author.bot = True

    result = should_collect_bot_message(message)

    assert result is True


def test_should_collect_bot_message_human():
    """Test human message collection"""
    message = Mock()
    message.author = Mock()
    message.author.bot = False

    result = should_collect_bot_message(message)

    assert result is True


# Tests for sync_guild_metadata
@patch("memory.discord.collector.make_session")
def test_sync_guild_metadata(mock_make_session, mock_guild):
    """Test syncing guild metadata"""
    mock_session = Mock()
    mock_make_session.return_value.__enter__ = Mock(return_value=mock_session)
    mock_make_session.return_value.__exit__ = Mock(return_value=None)

    # Mock session.query().get() to return None (new server)
    mock_session.query.return_value.get.return_value = None

    # Mock channels
    text_channel = Mock(spec=discord.TextChannel)
    text_channel.id = 1
    text_channel.name = "general"
    text_channel.guild = mock_guild

    voice_channel = Mock(spec=discord.VoiceChannel)
    voice_channel.id = 2
    voice_channel.name = "voice"
    voice_channel.guild = mock_guild

    mock_guild.channels = [text_channel, voice_channel]

    sync_guild_metadata(mock_guild)

    # Verify session.commit was called
    mock_session.commit.assert_called_once()


# Tests for MessageCollector class
def test_message_collector_init():
    """Test MessageCollector initialization"""
    collector = MessageCollector()

    assert collector.command_prefix == "!memory_"
    assert collector.help_command is None
    assert collector.intents.message_content is True
    assert collector.intents.guilds is True
    assert collector.intents.members is True
    assert collector.intents.dm_messages is True


@pytest.mark.asyncio
async def test_on_ready():
    """Test on_ready event handler"""
    collector = MessageCollector()
    collector.user = Mock()
    collector.user.name = "TestBot"
    collector.guilds = [Mock(), Mock()]
    collector.sync_servers_and_channels = AsyncMock()
    collector.tree.sync = AsyncMock()

    await collector.on_ready()

    collector.sync_servers_and_channels.assert_called_once()
    collector.tree.sync.assert_awaited()


@pytest.mark.asyncio
@patch("memory.discord.collector.make_session")
@patch("memory.discord.collector.add_discord_message")
async def test_on_message_success(mock_add_task, mock_make_session, mock_message):
    """Test successful message handling"""
    mock_session = Mock()
    mock_make_session.return_value.__enter__ = Mock(return_value=mock_session)
    mock_make_session.return_value.__exit__ = Mock(return_value=None)
    mock_session.query.return_value.get.return_value = None  # New entities

    collector = MessageCollector()
    await collector.on_message(mock_message)

    # Verify task was queued
    mock_add_task.delay.assert_called_once()
    call_kwargs = mock_add_task.delay.call_args[1]
    assert call_kwargs["message_id"] == mock_message.id
    assert call_kwargs["channel_id"] == mock_message.channel.id
    assert call_kwargs["author_id"] == mock_message.author.id
    assert call_kwargs["content"] == mock_message.content


@pytest.mark.asyncio
@patch("memory.discord.collector.make_session")
async def test_on_message_bot_message_filtered(mock_make_session, mock_message):
    """Test bot message filtering"""
    mock_message.author.bot = True

    with patch(
        "memory.discord.collector.should_collect_bot_message", return_value=False
    ):
        collector = MessageCollector()
        await collector.on_message(mock_message)

        # Should not create session or queue task
        mock_make_session.assert_not_called()


@pytest.mark.asyncio
@patch("memory.discord.collector.make_session")
async def test_on_message_error_handling(mock_make_session, mock_message):
    """Test error handling in on_message"""
    mock_make_session.side_effect = Exception("Database error")

    collector = MessageCollector()
    # Should not raise
    await collector.on_message(mock_message)


@pytest.mark.asyncio
@patch("memory.discord.collector.edit_discord_message")
async def test_on_message_edit(mock_edit_task):
    """Test message edit handler"""
    before = Mock()
    after = Mock()
    after.id = 123
    after.content = "Edited content"
    after.edited_at = datetime(2024, 1, 1, 13, 0, 0, tzinfo=timezone.utc)

    collector = MessageCollector()
    await collector.on_message_edit(before, after)

    mock_edit_task.delay.assert_called_once()
    call_kwargs = mock_edit_task.delay.call_args[1]
    assert call_kwargs["message_id"] == 123
    assert call_kwargs["content"] == "Edited content"


@pytest.mark.asyncio
async def test_on_message_edit_error_handling():
    """Test error handling in on_message_edit"""
    before = Mock()
    after = Mock()
    after.id = 123
    after.content = "Edited"
    after.edited_at = None  # Will trigger datetime.now

    with patch("memory.discord.collector.edit_discord_message") as mock_edit:
        mock_edit.delay.side_effect = Exception("Task error")

        collector = MessageCollector()
        # Should not raise
        await collector.on_message_edit(before, after)


@pytest.mark.asyncio
async def test_sync_servers_and_channels():
    """Test syncing servers and channels"""
    guild1 = Mock()
    guild2 = Mock()

    collector = MessageCollector()
    collector.guilds = [guild1, guild2]

    with patch("memory.discord.collector.sync_guild_metadata") as mock_sync:
        await collector.sync_servers_and_channels()

        assert mock_sync.call_count == 2
        mock_sync.assert_any_call(guild1)
        mock_sync.assert_any_call(guild2)


@pytest.mark.asyncio
@patch("memory.discord.collector.make_session")
async def test_refresh_metadata(mock_make_session):
    """Test metadata refresh"""
    mock_session = Mock()
    mock_make_session.return_value.__enter__ = Mock(return_value=mock_session)
    mock_make_session.return_value.__exit__ = Mock(return_value=None)
    mock_session.query.return_value.get.return_value = None

    guild = Mock()
    guild.id = 123
    guild.name = "Test"
    guild.channels = []
    guild.members = []

    collector = MessageCollector()
    collector.guilds = [guild]
    collector.intents = Mock()
    collector.intents.members = False

    result = await collector.refresh_metadata()

    assert result["servers_updated"] == 1
    assert result["channels_updated"] == 0
    assert result["users_updated"] == 0


@pytest.mark.asyncio
async def test_get_user_by_id():
    """Test getting user by ID"""
    user = Mock()
    user.id = 123

    collector = MessageCollector()
    collector.get_user = Mock(return_value=user)

    result = await collector.get_user(123)

    assert result == user


@pytest.mark.asyncio
async def test_get_user_by_username():
    """Test getting user by username"""
    member = Mock()
    member.name = "testuser"
    member.display_name = "Test User"
    member.discriminator = "1234"

    guild = Mock()
    guild.members = [member]

    collector = MessageCollector()
    collector.guilds = [guild]

    result = await collector.get_user("testuser")

    assert result == member


@pytest.mark.asyncio
async def test_get_user_not_found():
    """Test getting non-existent user"""
    collector = MessageCollector()
    collector.guilds = []

    with patch.object(collector, "get_user", return_value=None):
        with patch.object(
            collector, "fetch_user", side_effect=discord.NotFound(Mock(), Mock())
        ):
            result = await collector.get_user(999)
            assert result is None


@pytest.mark.asyncio
async def test_get_channel_by_name():
    """Test getting channel by name"""
    channel = Mock(spec=discord.TextChannel)
    channel.name = "general"

    guild = Mock()
    guild.channels = [channel]

    collector = MessageCollector()
    collector.guilds = [guild]

    result = await collector.get_channel_by_name("general")

    assert result == channel


@pytest.mark.asyncio
async def test_get_channel_by_name_not_found():
    """Test getting non-existent channel"""
    guild = Mock()
    guild.channels = []

    collector = MessageCollector()
    collector.guilds = [guild]

    result = await collector.get_channel_by_name("nonexistent")

    assert result is None


@pytest.mark.asyncio
async def test_create_channel():
    """Test creating a channel"""
    guild = Mock()
    guild.name = "Test Server"
    new_channel = Mock()
    guild.create_text_channel = AsyncMock(return_value=new_channel)

    collector = MessageCollector()
    collector.get_guild = Mock(return_value=guild)

    result = await collector.create_channel("new-channel", guild_id=123)

    assert result == new_channel
    guild.create_text_channel.assert_called_once_with("new-channel")


@pytest.mark.asyncio
async def test_create_channel_no_guild():
    """Test creating channel when no guild available"""
    collector = MessageCollector()
    collector.get_guild = Mock(return_value=None)
    collector.guilds = []

    result = await collector.create_channel("new-channel")

    assert result is None


@pytest.mark.asyncio
async def test_send_dm_success():
    """Test sending DM successfully"""
    user = Mock()
    user.send = AsyncMock()

    collector = MessageCollector()
    collector.get_user = AsyncMock(return_value=user)

    result = await collector.send_dm(123, "Hello!")

    assert result is True
    user.send.assert_called_once_with("Hello!")


@pytest.mark.asyncio
async def test_send_dm_user_not_found():
    """Test sending DM when user not found"""
    collector = MessageCollector()
    collector.get_user = AsyncMock(return_value=None)

    result = await collector.send_dm(123, "Hello!")

    assert result is False


@pytest.mark.asyncio
async def test_send_dm_exception():
    """Test sending DM with exception"""
    user = Mock()
    user.send = AsyncMock(side_effect=Exception("Send failed"))

    collector = MessageCollector()
    collector.get_user = AsyncMock(return_value=user)

    result = await collector.send_dm(123, "Hello!")

    assert result is False


@pytest.mark.asyncio
@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", True)
async def test_send_to_channel_success():
    """Test sending to channel successfully"""
    channel = Mock()
    channel.send = AsyncMock()

    collector = MessageCollector()
    collector.get_channel_by_name = AsyncMock(return_value=channel)

    result = await collector.send_to_channel("general", "Announcement!")

    assert result is True
    channel.send.assert_called_once_with("Announcement!")


@pytest.mark.asyncio
@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", False)
async def test_send_to_channel_notifications_disabled():
    """Test sending to channel when notifications disabled"""
    collector = MessageCollector()

    result = await collector.send_to_channel("general", "Announcement!")

    assert result is False


@pytest.mark.asyncio
@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", True)
async def test_send_to_channel_not_found():
    """Test sending to non-existent channel"""
    collector = MessageCollector()
    collector.get_channel_by_name = AsyncMock(return_value=None)

    result = await collector.send_to_channel("nonexistent", "Message")

    assert result is False


@pytest.mark.asyncio
@patch("memory.common.settings.DISCORD_BOT_TOKEN", "test_token")
async def test_run_collector():
    """Test running the collector"""
    from memory.discord.collector import run_collector

    with patch("memory.discord.collector.MessageCollector") as mock_collector_class:
        mock_collector = Mock()
        mock_collector.start = AsyncMock()
        mock_collector_class.return_value = mock_collector

        await run_collector()

        mock_collector.start.assert_called_once_with("test_token")


@pytest.mark.asyncio
@patch("memory.common.settings.DISCORD_BOT_TOKEN", None)
async def test_run_collector_no_token():
    """Test running collector without token"""
    from memory.discord.collector import run_collector

    # Should return early without raising
    await run_collector()
