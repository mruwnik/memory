"""Tests for Discord database models."""

from memory.common.db.models import (
    DiscordBot,
    DiscordChannel,
    DiscordServer,
    DiscordUser,
    HumanUser,
)


def test_create_discord_server(db_session):
    """Test creating a Discord server."""
    server = DiscordServer(
        id=123456789,
        name="Test Server",
        description="A test Discord server",
        member_count=100,
    )
    db_session.add(server)
    db_session.commit()

    assert server.id == 123456789
    assert server.name == "Test Server"
    assert server.description == "A test Discord server"
    assert server.member_count == 100
    assert server.collect_messages is False  # default value


def test_discord_server_collect_messages(db_session):
    """Test Discord server message collection flag."""
    server = DiscordServer(
        id=123456789,
        name="Test Server",
        collect_messages=True,
    )
    db_session.add(server)
    db_session.commit()

    assert server.collect_messages is True


def test_create_discord_channel(db_session):
    """Test creating a Discord channel."""
    server = DiscordServer(id=987654321, name="Parent Server")
    db_session.add(server)
    db_session.commit()

    channel = DiscordChannel(
        id=111222333,
        server_id=server.id,
        name="general",
        channel_type="text",
    )
    db_session.add(channel)
    db_session.commit()

    assert channel.id == 111222333
    assert channel.server_id == server.id
    assert channel.name == "general"
    assert channel.channel_type == "text"
    assert channel.server is not None
    assert channel.server.name == "Parent Server"


def test_discord_channel_without_server(db_session):
    """Test creating a Discord DM channel without a server."""
    channel = DiscordChannel(
        id=111222333,
        name="dm-channel",
        channel_type="dm",
        server_id=None,
    )
    db_session.add(channel)
    db_session.commit()

    assert channel.id == 111222333
    assert channel.server_id is None
    assert channel.channel_type == "dm"


def test_discord_channel_should_collect_inherits_from_server(db_session):
    """Test that channel inherits collect_messages from server when not set."""
    server = DiscordServer(id=987654321, name="Server", collect_messages=True)
    channel = DiscordChannel(
        id=111222333,
        server_id=server.id,
        name="general",
        channel_type="text",
        collect_messages=None,  # Inherit from server
    )
    db_session.add_all([server, channel])
    db_session.commit()

    assert channel.should_collect is True


def test_discord_channel_should_collect_explicit_override(db_session):
    """Test that channel can override server's collect_messages setting."""
    server = DiscordServer(id=987654321, name="Server", collect_messages=True)
    channel = DiscordChannel(
        id=111222333,
        server_id=server.id,
        name="private",
        channel_type="text",
        collect_messages=False,  # Explicit override
    )
    db_session.add_all([server, channel])
    db_session.commit()

    assert server.collect_messages is True
    assert channel.should_collect is False


def test_discord_channel_dm_defaults_to_not_collect(db_session):
    """Test that DM channels (no server) default to not collecting."""
    channel = DiscordChannel(
        id=111222333,
        name="dm-channel",
        channel_type="dm",
        server_id=None,
        collect_messages=None,
    )
    db_session.add(channel)
    db_session.commit()

    assert channel.should_collect is False


def test_discord_channel_dm_can_be_explicitly_enabled(db_session):
    """Test that DM channels can be explicitly enabled for collection."""
    channel = DiscordChannel(
        id=111222333,
        name="dm-channel",
        channel_type="dm",
        server_id=None,
        collect_messages=True,
    )
    db_session.add(channel)
    db_session.commit()

    assert channel.should_collect is True


def test_create_discord_user(db_session):
    """Test creating a Discord user."""
    user = DiscordUser(
        id=555666777,
        username="testuser",
        display_name="Test User",
    )
    db_session.add(user)
    db_session.commit()

    assert user.id == 555666777
    assert user.username == "testuser"
    assert user.display_name == "Test User"
    assert user.system_user_id is None


def test_discord_user_name_property(db_session):
    """Test DiscordUser.name property returns display_name or username."""
    user_with_display = DiscordUser(
        id=555666777,
        username="testuser",
        display_name="Test User",
    )
    user_without_display = DiscordUser(
        id=555666778,
        username="another_user",
        display_name=None,
    )
    db_session.add_all([user_with_display, user_without_display])
    db_session.commit()

    assert user_with_display.name == "Test User"
    assert user_without_display.name == "another_user"


def test_discord_user_with_system_user(db_session):
    """Test Discord user linked to a system user."""
    system_user = HumanUser.create_with_password(
        email="user@example.com", name="System User", password="password123"
    )
    db_session.add(system_user)
    db_session.commit()

    discord_user = DiscordUser(
        id=555666777,
        username="testuser",
        system_user_id=system_user.id,
    )
    db_session.add(discord_user)
    db_session.commit()

    assert discord_user.system_user_id == system_user.id
    assert discord_user.system_user is not None
    assert discord_user.system_user.email == "user@example.com"


def test_discord_server_channel_relationship(db_session):
    """Test the relationship between servers and channels."""
    server = DiscordServer(id=987654321, name="Test Server")
    channel1 = DiscordChannel(
        id=111222333, server_id=server.id, name="general", channel_type="text"
    )
    channel2 = DiscordChannel(
        id=111222334, server_id=server.id, name="off-topic", channel_type="text"
    )
    db_session.add_all([server, channel1, channel2])
    db_session.commit()

    assert len(server.channels) == 2
    assert channel1 in server.channels
    assert channel2 in server.channels


def test_discord_server_cascade_delete(db_session):
    """Test that deleting a server cascades to channels."""
    server = DiscordServer(id=987654321, name="Test Server")
    channel = DiscordChannel(
        id=111222333, server_id=server.id, name="general", channel_type="text"
    )
    db_session.add_all([server, channel])
    db_session.commit()

    channel_id = channel.id

    # Delete server
    db_session.delete(server)
    db_session.commit()

    # Channel should be deleted too
    deleted_channel = db_session.get(DiscordChannel, channel_id)
    assert deleted_channel is None


def test_create_discord_bot(db_session):
    """Test creating a Discord bot."""
    bot = DiscordBot(
        id=123456789,
        name="Test Bot",
        is_active=True,
    )
    db_session.add(bot)
    db_session.commit()

    assert bot.id == 123456789
    assert bot.name == "Test Bot"
    assert bot.is_active is True
    assert bot.token is None


def test_discord_bot_token_encryption(db_session):
    """Test that bot token is encrypted and decrypted correctly."""
    bot = DiscordBot(
        id=123456789,
        name="Test Bot",
    )
    bot.token = "my_secret_token"
    db_session.add(bot)
    db_session.commit()

    # Token should be encrypted in storage
    assert bot.token_encrypted is not None
    assert bot.token_encrypted != b"my_secret_token"

    # But should decrypt correctly via property
    assert bot.token == "my_secret_token"


def test_discord_bot_user_authorization(db_session):
    """Test many-to-many relationship between bots and users."""
    user1 = HumanUser.create_with_password(
        email="user1@example.com", name="User 1", password="password123"
    )
    user2 = HumanUser.create_with_password(
        email="user2@example.com", name="User 2", password="password123"
    )
    db_session.add_all([user1, user2])
    db_session.commit()

    bot = DiscordBot(id=123456789, name="Test Bot")
    bot.authorized_users.append(user1)
    db_session.add(bot)
    db_session.commit()

    assert bot.is_authorized(user1) is True
    assert bot.is_authorized(user2) is False

    # User should have the bot in their discord_bots list
    assert bot in user1.discord_bots
    assert bot not in user2.discord_bots


def test_discord_bot_multiple_users(db_session):
    """Test that multiple users can be authorized for a bot."""
    user1 = HumanUser.create_with_password(
        email="user1@example.com", name="User 1", password="password123"
    )
    user2 = HumanUser.create_with_password(
        email="user2@example.com", name="User 2", password="password123"
    )
    db_session.add_all([user1, user2])
    db_session.commit()

    bot = DiscordBot(id=123456789, name="Test Bot")
    bot.authorized_users.extend([user1, user2])
    db_session.add(bot)
    db_session.commit()

    assert len(bot.authorized_users) == 2
    assert bot.is_authorized(user1) is True
    assert bot.is_authorized(user2) is True


def test_user_multiple_bots(db_session):
    """Test that a user can be authorized for multiple bots."""
    user = HumanUser.create_with_password(
        email="user@example.com", name="User", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    bot1 = DiscordBot(id=111111111, name="Bot 1")
    bot2 = DiscordBot(id=222222222, name="Bot 2")
    bot1.authorized_users.append(user)
    bot2.authorized_users.append(user)
    db_session.add_all([bot1, bot2])
    db_session.commit()

    assert len(user.discord_bots) == 2
    assert bot1 in user.discord_bots
    assert bot2 in user.discord_bots
