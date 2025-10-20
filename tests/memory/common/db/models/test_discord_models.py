"""Tests for Discord database models."""

import pytest
from memory.common.db.models import DiscordServer, DiscordChannel, DiscordUser


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
    assert server.track_messages is True  # default value
    assert server.ignore_messages is False


def test_discord_server_as_xml(db_session):
    """Test DiscordServer.as_xml() method."""
    server = DiscordServer(
        id=123456789,
        name="Test Server",
        summary="This is a test server for gaming",
    )
    db_session.add(server)
    db_session.commit()

    xml = server.as_xml()
    assert "<servers>" in xml  # tablename is discord_servers, strips to "servers"
    assert "<name>Test Server</name>" in xml
    assert "<summary>This is a test server for gaming</summary>" in xml
    assert "</servers>" in xml


def test_discord_server_message_tracking(db_session):
    """Test Discord server message tracking flags."""
    server = DiscordServer(
        id=123456789,
        name="Test Server",
        track_messages=False,
        ignore_messages=True,
    )
    db_session.add(server)
    db_session.commit()

    assert server.track_messages is False
    assert server.ignore_messages is True


def test_discord_server_allowed_tools(db_session):
    """Test Discord server allowed/disallowed tools."""
    server = DiscordServer(
        id=123456789,
        name="Test Server",
        allowed_tools=["search", "schedule"],
        disallowed_tools=["delete", "ban"],
    )
    db_session.add(server)
    db_session.commit()

    assert "search" in server.allowed_tools
    assert "schedule" in server.allowed_tools
    assert "delete" in server.disallowed_tools
    assert "ban" in server.disallowed_tools


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


def test_discord_channel_as_xml(db_session):
    """Test DiscordChannel.as_xml() method."""
    channel = DiscordChannel(
        id=111222333,
        name="general",
        channel_type="text",
        summary="Main discussion channel",
    )
    db_session.add(channel)
    db_session.commit()

    xml = channel.as_xml()
    assert "<channels>" in xml  # tablename is discord_channels, strips to "channels"
    assert "<name>general</name>" in xml
    assert "<summary>Main discussion channel</summary>" in xml
    assert "</channels>" in xml


def test_discord_channel_inherits_server_settings(db_session):
    """Test that channels can have their own or inherit server settings."""
    server = DiscordServer(
        id=987654321, name="Server", track_messages=True, ignore_messages=False
    )
    channel = DiscordChannel(
        id=111222333,
        server_id=server.id,
        name="announcements",
        channel_type="text",
        track_messages=False,  # Override server setting
    )
    db_session.add_all([server, channel])
    db_session.commit()

    assert server.track_messages is True
    assert channel.track_messages is False


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


def test_discord_user_with_system_user(db_session):
    """Test Discord user linked to a system user."""
    from memory.common.db.models import HumanUser

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
    assert discord_user.system_user.email == "user@example.com"


def test_discord_user_as_xml(db_session):
    """Test DiscordUser.as_xml() method."""
    user = DiscordUser(
        id=555666777,
        username="testuser",
        summary="Friendly and helpful community member",
    )
    db_session.add(user)
    db_session.commit()

    xml = user.as_xml()
    assert "<users>" in xml  # tablename is discord_users, strips to "users"
    assert "<name>testuser</name>" in xml
    assert "<summary>Friendly and helpful community member</summary>" in xml
    assert "</users>" in xml


def test_discord_user_message_preferences(db_session):
    """Test Discord user message tracking preferences."""
    user = DiscordUser(
        id=555666777,
        username="testuser",
        track_messages=True,
        ignore_messages=False,
    )
    db_session.add(user)
    db_session.commit()

    assert user.track_messages is True
    assert user.ignore_messages is False


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
