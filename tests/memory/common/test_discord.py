import logging
import pytest
from unittest.mock import Mock, patch
import requests
import json

from memory.common import discord, settings


@pytest.fixture
def mock_session_request():
    with patch("requests.Session.request") as mock:
        yield mock


@pytest.fixture
def mock_get_channels_response():
    return [
        {"name": "memory-errors", "id": "error_channel_id"},
        {"name": "memory-activity", "id": "activity_channel_id"},
        {"name": "memory-discoveries", "id": "discovery_channel_id"},
        {"name": "memory-chat", "id": "chat_channel_id"},
    ]


def test_discord_server_init(mock_session_request, mock_get_channels_response):
    # Mock the channels API call
    mock_response = Mock()
    mock_response.json.return_value = mock_get_channels_response
    mock_response.raise_for_status.return_value = None
    mock_session_request.return_value = mock_response

    server = discord.DiscordServer("server123", "Test Server")

    assert server.server_id == "server123"
    assert server.server_name == "Test Server"
    assert hasattr(server, "channels")


@patch("memory.common.settings.DISCORD_ERROR_CHANNEL", "memory-errors")
@patch("memory.common.settings.DISCORD_ACTIVITY_CHANNEL", "memory-activity")
@patch("memory.common.settings.DISCORD_DISCOVERY_CHANNEL", "memory-discoveries")
@patch("memory.common.settings.DISCORD_CHAT_CHANNEL", "memory-chat")
def test_setup_channels_existing(mock_session_request, mock_get_channels_response):
    # Mock the channels API call
    mock_response = Mock()
    mock_response.json.return_value = mock_get_channels_response
    mock_response.raise_for_status.return_value = None
    mock_session_request.return_value = mock_response

    server = discord.DiscordServer("server123", "Test Server")

    assert server.channels[discord.ERROR_CHANNEL] == "error_channel_id"
    assert server.channels[discord.ACTIVITY_CHANNEL] == "activity_channel_id"
    assert server.channels[discord.DISCOVERY_CHANNEL] == "discovery_channel_id"
    assert server.channels[discord.CHAT_CHANNEL] == "chat_channel_id"


@patch("memory.common.settings.DISCORD_ERROR_CHANNEL", "new-error-channel")
def test_setup_channels_create_missing(mock_session_request):
    # Mock get channels (empty) and create channel calls
    get_response = Mock()
    get_response.json.return_value = []
    get_response.raise_for_status.return_value = None

    create_response = Mock()
    create_response.json.return_value = {"id": "new_channel_id"}
    create_response.raise_for_status.return_value = None

    mock_session_request.side_effect = [
        get_response,
        create_response,
        create_response,
        create_response,
        create_response,
    ]

    server = discord.DiscordServer("server123", "Test Server")

    assert server.channels[discord.ERROR_CHANNEL] == "new_channel_id"


def test_channel_properties():
    server = discord.DiscordServer.__new__(discord.DiscordServer)
    server.channels = {
        discord.ERROR_CHANNEL: "error_id",
        discord.ACTIVITY_CHANNEL: "activity_id",
        discord.DISCOVERY_CHANNEL: "discovery_id",
        discord.CHAT_CHANNEL: "chat_id",
    }

    assert server.error_channel == "error_id"
    assert server.activity_channel == "activity_id"
    assert server.discovery_channel == "discovery_id"
    assert server.chat_channel == "chat_id"


def test_channel_id_exists():
    server = discord.DiscordServer.__new__(discord.DiscordServer)
    server.channels = {"test-channel": "channel123"}

    assert server.channel_id("test-channel") == "channel123"


def test_channel_id_not_found():
    server = discord.DiscordServer.__new__(discord.DiscordServer)
    server.channels = {}

    with pytest.raises(ValueError, match="Channel nonexistent not found"):
        server.channel_id("nonexistent")


def test_send_message(mock_session_request):
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_session_request.return_value = mock_response

    server = discord.DiscordServer.__new__(discord.DiscordServer)

    server.send_message("channel123", "Hello World")

    mock_session_request.assert_called_with(
        "POST",
        "https://discord.com/api/v10/channels/channel123/messages",
        data=None,
        json={"content": "Hello World"},
        headers={
            "Authorization": f"Bot {settings.DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json",
        },
    )


def test_create_channel(mock_session_request):
    mock_response = Mock()
    mock_response.json.return_value = {"id": "new_channel_id"}
    mock_response.raise_for_status.return_value = None
    mock_session_request.return_value = mock_response

    server = discord.DiscordServer.__new__(discord.DiscordServer)
    server.server_id = "server123"

    channel_id = server.create_channel("new-channel")

    assert channel_id == "new_channel_id"
    mock_session_request.assert_called_with(
        "POST",
        "https://discord.com/api/v10/guilds/server123/channels",
        data=None,
        json={"name": "new-channel", "type": 0},
        headers={
            "Authorization": f"Bot {settings.DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json",
        },
    )


def test_create_channel_custom_type(mock_session_request):
    mock_response = Mock()
    mock_response.json.return_value = {"id": "voice_channel_id"}
    mock_response.raise_for_status.return_value = None
    mock_session_request.return_value = mock_response

    server = discord.DiscordServer.__new__(discord.DiscordServer)
    server.server_id = "server123"

    channel_id = server.create_channel("voice-channel", channel_type=2)

    assert channel_id == "voice_channel_id"
    mock_session_request.assert_called_with(
        "POST",
        "https://discord.com/api/v10/guilds/server123/channels",
        data=None,
        json={"name": "voice-channel", "type": 2},
        headers={
            "Authorization": f"Bot {settings.DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json",
        },
    )


def test_str_representation():
    server = discord.DiscordServer.__new__(discord.DiscordServer)
    server.server_id = "server123"
    server.server_name = "Test Server"

    assert str(server) == "DiscordServer(server_id=server123, server_name=Test Server)"


@patch("memory.common.settings.DISCORD_BOT_TOKEN", "test_token_123")
def test_request_adds_headers(mock_session_request):
    server = discord.DiscordServer.__new__(discord.DiscordServer)

    server.request("GET", "https://example.com", headers={"Custom": "header"})

    expected_headers = {
        "Custom": "header",
        "Authorization": "Bot test_token_123",
        "Content-Type": "application/json",
    }
    mock_session_request.assert_called_once_with(
        "GET", "https://example.com", headers=expected_headers
    )


def test_channels_url():
    server = discord.DiscordServer.__new__(discord.DiscordServer)
    server.server_id = "server123"

    assert (
        server.channels_url == "https://discord.com/api/v10/guilds/server123/channels"
    )


@patch("memory.common.settings.DISCORD_BOT_TOKEN", "test_token")
@patch("requests.get")
def test_get_bot_servers_success(mock_get):
    mock_response = Mock()
    mock_response.json.return_value = [
        {"id": "server1", "name": "Server 1"},
        {"id": "server2", "name": "Server 2"},
    ]
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    servers = discord.get_bot_servers()

    assert len(servers) == 2
    assert servers[0] == {"id": "server1", "name": "Server 1"}
    mock_get.assert_called_once_with(
        "https://discord.com/api/v10/users/@me/guilds",
        headers={"Authorization": "Bot test_token"},
    )


@patch("memory.common.settings.DISCORD_BOT_TOKEN", None)
def test_get_bot_servers_no_token():
    assert discord.get_bot_servers() == []


@patch("memory.common.settings.DISCORD_BOT_TOKEN", "test_token")
@patch("requests.get")
def test_get_bot_servers_exception(mock_get):
    mock_get.side_effect = requests.RequestException("API Error")

    servers = discord.get_bot_servers()

    assert servers == []


@patch("memory.common.discord.get_bot_servers")
@patch("memory.common.discord.DiscordServer")
def test_load_servers(mock_discord_server_class, mock_get_servers):
    mock_get_servers.return_value = [
        {"id": "server1", "name": "Server 1"},
        {"id": "server2", "name": "Server 2"},
    ]

    discord.load_servers()

    assert mock_discord_server_class.call_count == 2
    mock_discord_server_class.assert_any_call("server1", "Server 1")
    mock_discord_server_class.assert_any_call("server2", "Server 2")


@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", True)
def test_broadcast_message():
    mock_server1 = Mock()
    mock_server2 = Mock()
    discord.servers = {"1": mock_server1, "2": mock_server2}

    discord.broadcast_message("test-channel", "Hello")

    mock_server1.send_message.assert_called_once_with(
        mock_server1.channel_id.return_value, "Hello"
    )
    mock_server2.send_message.assert_called_once_with(
        mock_server2.channel_id.return_value, "Hello"
    )


@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", False)
def test_broadcast_message_disabled():
    mock_server = Mock()
    discord.servers = {"1": mock_server}

    discord.broadcast_message("test-channel", "Hello")

    mock_server.send_message.assert_not_called()


@patch("memory.common.discord.broadcast_message")
def test_send_error_message(mock_broadcast):
    discord.send_error_message("Error occurred")
    mock_broadcast.assert_called_once_with(discord.ERROR_CHANNEL, "Error occurred")


@patch("memory.common.discord.broadcast_message")
def test_send_activity_message(mock_broadcast):
    discord.send_activity_message("Activity update")
    mock_broadcast.assert_called_once_with(discord.ACTIVITY_CHANNEL, "Activity update")


@patch("memory.common.discord.broadcast_message")
def test_send_discovery_message(mock_broadcast):
    discord.send_discovery_message("Discovery made")
    mock_broadcast.assert_called_once_with(discord.DISCOVERY_CHANNEL, "Discovery made")


@patch("memory.common.discord.broadcast_message")
def test_send_chat_message(mock_broadcast):
    discord.send_chat_message("Chat message")
    mock_broadcast.assert_called_once_with(discord.CHAT_CHANNEL, "Chat message")


@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", True)
@patch("memory.common.discord.send_error_message")
def test_notify_task_failure_basic(mock_send_error):
    discord.notify_task_failure("test_task", "Something went wrong")

    mock_send_error.assert_called_once()
    message = mock_send_error.call_args[0][0]

    assert "🚨 **Task Failed: test_task**" in message
    assert "**Error:** Something went wrong" in message


@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", True)
@patch("memory.common.discord.send_error_message")
def test_notify_task_failure_with_args(mock_send_error):
    discord.notify_task_failure(
        "test_task",
        "Error message",
        task_args=("arg1", "arg2"),
        task_kwargs={"key": "value"},
    )

    message = mock_send_error.call_args[0][0]

    assert "**Args:** `('arg1', 'arg2')`" in message
    assert "**Kwargs:** `{'key': 'value'}`" in message


@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", True)
@patch("memory.common.discord.send_error_message")
def test_notify_task_failure_with_traceback(mock_send_error):
    traceback = "Traceback (most recent call last):\n  File ...\nError: Something"

    discord.notify_task_failure("test_task", "Error message", traceback_str=traceback)

    message = mock_send_error.call_args[0][0]
    assert "**Traceback:**" in message
    assert traceback in message


@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", True)
@patch("memory.common.discord.send_error_message")
def test_notify_task_failure_truncates_long_error(mock_send_error):
    long_error = "x" * 600  # Longer than 500 char limit

    discord.notify_task_failure("test_task", long_error)

    message = mock_send_error.call_args[0][0]
    assert long_error[:500] in message


@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", True)
@patch("memory.common.discord.send_error_message")
def test_notify_task_failure_truncates_long_traceback(mock_send_error):
    long_traceback = "x" * 1000  # Longer than 800 char limit

    discord.notify_task_failure("test_task", "Error", traceback_str=long_traceback)

    message = mock_send_error.call_args[0][0]
    assert long_traceback[-800:] in message


@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", False)
@patch("memory.common.discord.send_error_message")
def test_notify_task_failure_disabled(mock_send_error):
    discord.notify_task_failure("test_task", "Error message")
    mock_send_error.assert_not_called()


@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", True)
@patch("memory.common.discord.send_error_message")
def test_notify_task_failure_send_fails(mock_send_error):
    mock_send_error.side_effect = Exception("Discord API error")

    # Should not raise, just log the error
    discord.notify_task_failure("test_task", "Error message")

    mock_send_error.assert_called_once()
