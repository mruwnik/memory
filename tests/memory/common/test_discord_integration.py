import pytest
from unittest.mock import Mock, patch
import requests

from memory.common import discord


@pytest.fixture
def mock_api_url():
    """Mock the API URL to avoid using actual settings"""
    with patch(
        "memory.common.discord.get_api_url", return_value="http://localhost:8000"
    ):
        yield


@patch("memory.common.settings.DISCORD_COLLECTOR_SERVER_URL", "testhost")
@patch("memory.common.settings.DISCORD_COLLECTOR_PORT", 9999)
def test_get_api_url():
    """Test API URL construction"""
    assert discord.get_api_url() == "http://testhost:9999"


@patch("requests.post")
def test_send_dm_success(mock_post, mock_api_url):
    """Test successful DM sending"""
    mock_response = Mock()
    mock_response.json.return_value = {"success": True}
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    result = discord.send_dm("user123", "Hello!")

    assert result is True
    mock_post.assert_called_once_with(
        "http://localhost:8000/send_dm",
        json={"user_identifier": "user123", "message": "Hello!"},
        timeout=10,
    )


@patch("requests.post")
def test_send_dm_api_failure(mock_post, mock_api_url):
    """Test DM sending when API returns failure"""
    mock_response = Mock()
    mock_response.json.return_value = {"success": False}
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    result = discord.send_dm("user123", "Hello!")

    assert result is False


@patch("requests.post")
def test_send_dm_request_exception(mock_post, mock_api_url):
    """Test DM sending when request raises exception"""
    mock_post.side_effect = requests.RequestException("Network error")

    result = discord.send_dm("user123", "Hello!")

    assert result is False


@patch("requests.post")
def test_send_dm_http_error(mock_post, mock_api_url):
    """Test DM sending when HTTP error occurs"""
    mock_response = Mock()
    mock_response.raise_for_status.side_effect = requests.HTTPError("404 Not Found")
    mock_post.return_value = mock_response

    result = discord.send_dm("user123", "Hello!")

    assert result is False


@patch("requests.post")
def test_broadcast_message_success(mock_post, mock_api_url):
    """Test successful channel message broadcast"""
    mock_response = Mock()
    mock_response.json.return_value = {"success": True}
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    result = discord.broadcast_message("general", "Announcement!")

    assert result is True
    mock_post.assert_called_once_with(
        "http://localhost:8000/send_channel",
        json={"channel_name": "general", "message": "Announcement!"},
        timeout=10,
    )


@patch("requests.post")
def test_broadcast_message_failure(mock_post, mock_api_url):
    """Test channel message broadcast failure"""
    mock_response = Mock()
    mock_response.json.return_value = {"success": False}
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    result = discord.broadcast_message("general", "Announcement!")

    assert result is False


@patch("requests.post")
def test_broadcast_message_exception(mock_post, mock_api_url):
    """Test channel message broadcast with exception"""
    mock_post.side_effect = requests.Timeout("Request timeout")

    result = discord.broadcast_message("general", "Announcement!")

    assert result is False


@patch("requests.get")
def test_is_collector_healthy_true(mock_get, mock_api_url):
    """Test health check when collector is healthy"""
    mock_response = Mock()
    mock_response.json.return_value = {"status": "healthy"}
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    result = discord.is_collector_healthy()

    assert result is True
    mock_get.assert_called_once_with("http://localhost:8000/health", timeout=5)


@patch("requests.get")
def test_is_collector_healthy_false_status(mock_get, mock_api_url):
    """Test health check when collector returns unhealthy status"""
    mock_response = Mock()
    mock_response.json.return_value = {"status": "unhealthy"}
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    result = discord.is_collector_healthy()

    assert result is False


@patch("requests.get")
def test_is_collector_healthy_exception(mock_get, mock_api_url):
    """Test health check when request fails"""
    mock_get.side_effect = requests.ConnectionError("Connection refused")

    result = discord.is_collector_healthy()

    assert result is False


@patch("requests.post")
def test_refresh_discord_metadata_success(mock_post, mock_api_url):
    """Test successful metadata refresh"""
    mock_response = Mock()
    mock_response.json.return_value = {
        "servers": 5,
        "channels": 20,
        "users": 100,
    }
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    result = discord.refresh_discord_metadata()

    assert result == {"servers": 5, "channels": 20, "users": 100}
    mock_post.assert_called_once_with(
        "http://localhost:8000/refresh_metadata", timeout=30
    )


@patch("requests.post")
def test_refresh_discord_metadata_failure(mock_post, mock_api_url):
    """Test metadata refresh failure"""
    mock_post.side_effect = requests.RequestException("Failed to connect")

    result = discord.refresh_discord_metadata()

    assert result is None


@patch("requests.post")
def test_refresh_discord_metadata_http_error(mock_post, mock_api_url):
    """Test metadata refresh with HTTP error"""
    mock_response = Mock()
    mock_response.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
    mock_post.return_value = mock_response

    result = discord.refresh_discord_metadata()

    assert result is None


@patch("memory.common.discord.broadcast_message")
@patch("memory.common.settings.DISCORD_ERROR_CHANNEL", "errors")
def test_send_error_message(mock_broadcast):
    """Test sending error message to error channel"""
    mock_broadcast.return_value = True

    result = discord.send_error_message("Something broke")

    assert result is True
    mock_broadcast.assert_called_once_with("errors", "Something broke")


@patch("memory.common.discord.broadcast_message")
@patch("memory.common.settings.DISCORD_ACTIVITY_CHANNEL", "activity")
def test_send_activity_message(mock_broadcast):
    """Test sending activity message to activity channel"""
    mock_broadcast.return_value = True

    result = discord.send_activity_message("User logged in")

    assert result is True
    mock_broadcast.assert_called_once_with("activity", "User logged in")


@patch("memory.common.discord.broadcast_message")
@patch("memory.common.settings.DISCORD_DISCOVERY_CHANNEL", "discoveries")
def test_send_discovery_message(mock_broadcast):
    """Test sending discovery message to discovery channel"""
    mock_broadcast.return_value = True

    result = discord.send_discovery_message("Found interesting pattern")

    assert result is True
    mock_broadcast.assert_called_once_with("discoveries", "Found interesting pattern")


@patch("memory.common.discord.broadcast_message")
@patch("memory.common.settings.DISCORD_CHAT_CHANNEL", "chat")
def test_send_chat_message(mock_broadcast):
    """Test sending chat message to chat channel"""
    mock_broadcast.return_value = True

    result = discord.send_chat_message("Hello from bot")

    assert result is True
    mock_broadcast.assert_called_once_with("chat", "Hello from bot")


@patch("memory.common.discord.send_error_message")
@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", True)
def test_notify_task_failure_basic(mock_send_error):
    """Test basic task failure notification"""
    discord.notify_task_failure("test_task", "Something went wrong")

    mock_send_error.assert_called_once()
    message = mock_send_error.call_args[0][0]

    assert "🚨 **Task Failed: test_task**" in message
    assert "**Error:** Something went wrong" in message


@patch("memory.common.discord.send_error_message")
@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", True)
def test_notify_task_failure_with_args(mock_send_error):
    """Test task failure notification with arguments"""
    discord.notify_task_failure(
        "test_task",
        "Error occurred",
        task_args=("arg1", 42),
        task_kwargs={"key": "value", "number": 123},
    )

    message = mock_send_error.call_args[0][0]

    assert "**Args:** `('arg1', 42)" in message
    assert "**Kwargs:** `{'key': 'value', 'number': 123}" in message


@patch("memory.common.discord.send_error_message")
@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", True)
def test_notify_task_failure_with_traceback(mock_send_error):
    """Test task failure notification with traceback"""
    traceback = "Traceback (most recent call last):\n  File test.py, line 10\n    raise Exception('test')\nException: test"

    discord.notify_task_failure("test_task", "Error occurred", traceback_str=traceback)

    message = mock_send_error.call_args[0][0]

    assert "**Traceback:**" in message
    assert "Exception: test" in message


@patch("memory.common.discord.send_error_message")
@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", True)
def test_notify_task_failure_truncates_long_error(mock_send_error):
    """Test that long error messages are truncated"""
    long_error = "x" * 600

    discord.notify_task_failure("test_task", long_error)

    message = mock_send_error.call_args[0][0]

    # Error should be truncated to 500 chars - check that the full 600 char string is not there
    assert "**Error:** " + long_error[:500] in message
    # The full 600-char error should not be present
    error_section = message.split("**Error:** ")[1].split("\n")[0]
    assert len(error_section) == 500


@patch("memory.common.discord.send_error_message")
@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", True)
def test_notify_task_failure_truncates_long_traceback(mock_send_error):
    """Test that long tracebacks are truncated"""
    long_traceback = "x" * 1000

    discord.notify_task_failure("test_task", "Error", traceback_str=long_traceback)

    message = mock_send_error.call_args[0][0]

    # Traceback should show last 800 chars
    assert long_traceback[-800:] in message
    # The full 1000-char traceback should not be present
    traceback_section = message.split("**Traceback:**\n```\n")[1].split("\n```")[0]
    assert len(traceback_section) == 800


@patch("memory.common.discord.send_error_message")
@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", True)
def test_notify_task_failure_truncates_long_args(mock_send_error):
    """Test that long task arguments are truncated"""
    long_args = ("x" * 300,)

    discord.notify_task_failure("test_task", "Error", task_args=long_args)

    message = mock_send_error.call_args[0][0]

    # Args should be truncated to 200 chars
    assert (
        len(message.split("**Args:**")[1].split("\n")[0]) <= 210
    )  # Some buffer for formatting


@patch("memory.common.discord.send_error_message")
@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", True)
def test_notify_task_failure_truncates_long_kwargs(mock_send_error):
    """Test that long task kwargs are truncated"""
    long_kwargs = {"key": "x" * 300}

    discord.notify_task_failure("test_task", "Error", task_kwargs=long_kwargs)

    message = mock_send_error.call_args[0][0]

    # Kwargs should be truncated to 200 chars
    assert len(message.split("**Kwargs:**")[1].split("\n")[0]) <= 210


@patch("memory.common.discord.send_error_message")
@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", False)
def test_notify_task_failure_disabled(mock_send_error):
    """Test that notifications are not sent when disabled"""
    discord.notify_task_failure("test_task", "Error occurred")

    mock_send_error.assert_not_called()


@patch("memory.common.discord.send_error_message")
@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", True)
def test_notify_task_failure_send_error_exception(mock_send_error):
    """Test that exceptions in send_error_message don't propagate"""
    mock_send_error.side_effect = Exception("Failed to send")

    # Should not raise
    discord.notify_task_failure("test_task", "Error occurred")

    mock_send_error.assert_called_once()


@pytest.mark.parametrize(
    "function,channel_setting,message",
    [
        (discord.send_error_message, "DISCORD_ERROR_CHANNEL", "Error!"),
        (discord.send_activity_message, "DISCORD_ACTIVITY_CHANNEL", "Activity!"),
        (discord.send_discovery_message, "DISCORD_DISCOVERY_CHANNEL", "Discovery!"),
        (discord.send_chat_message, "DISCORD_CHAT_CHANNEL", "Chat!"),
    ],
)
@patch("memory.common.discord.broadcast_message")
def test_convenience_functions_use_correct_channels(
    mock_broadcast, function, channel_setting, message
):
    """Test that convenience functions use the correct channel settings"""
    with patch(f"memory.common.settings.{channel_setting}", "test-channel"):
        function(message)
        mock_broadcast.assert_called_once_with("test-channel", message)


@patch("requests.post")
def test_send_dm_with_special_characters(mock_post, mock_api_url):
    """Test sending DM with special characters"""
    mock_response = Mock()
    mock_response.json.return_value = {"success": True}
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    message_with_special_chars = "Hello! 🎉 <@123> #general"
    result = discord.send_dm("user123", message_with_special_chars)

    assert result is True
    call_args = mock_post.call_args
    assert call_args[1]["json"]["message"] == message_with_special_chars


@patch("requests.post")
def test_broadcast_message_with_long_message(mock_post, mock_api_url):
    """Test broadcasting a long message"""
    mock_response = Mock()
    mock_response.json.return_value = {"success": True}
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    long_message = "A" * 2000
    result = discord.broadcast_message("general", long_message)

    assert result is True
    call_args = mock_post.call_args
    assert call_args[1]["json"]["message"] == long_message


@patch("requests.get")
def test_is_collector_healthy_missing_status_key(mock_get, mock_api_url):
    """Test health check when response doesn't have status key"""
    mock_response = Mock()
    mock_response.json.return_value = {}
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    result = discord.is_collector_healthy()

    assert result is False
