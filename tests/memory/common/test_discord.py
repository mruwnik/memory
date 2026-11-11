import json
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
import requests

from memory.common import discord, settings
from memory.common.content_processing import safe_task_execution
from memory.common.db.models import DiscordBot, DiscordChannel, DiscordServer, HumanUser
from memory.discord import rest

BOT_ID = 42


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


@patch("requests.get")
def test_is_collector_healthy_true(mock_get, mock_api_url):
    """Test health check when collector is healthy"""
    mock_response = Mock()
    mock_response.json.return_value = {str(BOT_ID): {"connected": True}}
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    result = discord.is_collector_healthy(BOT_ID)

    assert result is True
    mock_get.assert_called_once_with("http://localhost:8000/health", timeout=5)


@patch("requests.get")
def test_is_collector_healthy_false_status(mock_get, mock_api_url):
    """Test health check when collector returns unhealthy status"""
    mock_response = Mock()
    mock_response.json.return_value = {str(BOT_ID): {"connected": False}}
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    result = discord.is_collector_healthy(BOT_ID)

    assert result is False


@patch("requests.get")
def test_is_collector_healthy_exception(mock_get, mock_api_url):
    """Test health check when request fails"""
    mock_get.side_effect = requests.ConnectionError("Connection refused")

    result = discord.is_collector_healthy(BOT_ID)

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

    result = discord.refresh_discord_metadata(BOT_ID)

    assert result == {"servers": 5, "channels": 20, "users": 100}
    mock_post.assert_called_once_with(
        "http://localhost:8000/refresh_metadata/42", timeout=30
    )


@patch("requests.post")
def test_refresh_discord_metadata_failure(mock_post, mock_api_url):
    """Test metadata refresh failure"""
    mock_post.side_effect = requests.RequestException("Failed to connect")

    result = discord.refresh_discord_metadata(BOT_ID)

    assert result is None


@patch("requests.post")
def test_refresh_discord_metadata_http_error(mock_post, mock_api_url):
    """Test metadata refresh with HTTP error"""
    mock_response = Mock()
    mock_response.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
    mock_post.return_value = mock_response

    result = discord.refresh_discord_metadata(BOT_ID)

    assert result is None


@patch("requests.post")
def test_refresh_discord_metadata_for_a_bot_not_running(mock_post, mock_api_url):
    mock_post.return_value = Mock(status_code=404)

    with pytest.raises(discord.BotNotConnected):
        discord.refresh_discord_metadata(BOT_ID)


OPERATOR_CHANNEL = 2_000
OTHER_CHANNEL = 1_000  # older than the operator's: a lower snowflake


@pytest.fixture
def discord_api():
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"id": "1"})

    def client(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    with (
        patch.object(rest, "public_http_client", side_effect=client),
        patch.object(rest.asyncio, "sleep", new=AsyncMock()),
    ):
        yield sent


def add_bot(db_session, email: str, application_id: int) -> DiscordBot:
    owner = HumanUser(name=email, email=email, password_hash="x", scopes=["read", "write"])
    db_session.add(owner)
    db_session.flush()
    bot = DiscordBot(
        application_id=application_id, name=email, realtime="gateway", public_key="ab" * 32,
        created_by_user_id=owner.id,
    )
    bot.token = "tok-" + email
    db_session.add(bot)
    db_session.flush()
    return bot


def add_channel(
    db_session, bot: DiscordBot | None, server_id: int, channel_id: int, verified: bool = True, channel_type: str = "text"
) -> None:
    db_session.merge(DiscordServer(id=server_id, name=f"s{server_id}", bot_id=bot and bot.id, verified=verified))
    db_session.flush()
    db_session.add(DiscordChannel(id=channel_id, server_id=server_id, name="ops-errors", channel_type=channel_type))
    db_session.commit()


@pytest.fixture
def alerts_on(monkeypatch):
    monkeypatch.setattr(settings, "DISCORD_NOTIFICATIONS_ENABLED", True)
    monkeypatch.setattr(settings, "DISCORD_ERROR_CHANNEL", "ops-errors")


def sent_paths(requests: list[httpx.Request]) -> list[tuple[str, str]]:
    return [(r.url.path, r.headers["authorization"]) for r in requests]


@pytest.mark.parametrize("verified", [True, False])
def test_alert_ignores_same_named_channel_of_another_users_bot(db_session, discord_api, alerts_on, verified):
    operator = add_bot(db_session, "operator@example.com", 7)
    mallory = add_bot(db_session, "mallory@example.com", 8)
    add_channel(db_session, operator, 1, OPERATOR_CHANNEL)
    add_channel(db_session, mallory, 2, OTHER_CHANNEL, verified=verified)

    discord.notify_task_failure("sync_note", "ValueError", bot_id=operator.id)

    assert sent_paths(discord_api) == [
        (f"/api/v10/channels/{OPERATOR_CHANNEL}/messages", "Bot tok-operator@example.com")
    ]


def test_alert_ignores_channel_in_unverified_server_claimed_for_the_operator_bot(db_session, discord_api, alerts_on):
    operator = add_bot(db_session, "operator@example.com", 7)
    add_channel(db_session, operator, 1, OPERATOR_CHANNEL)
    add_channel(db_session, operator, 2, OTHER_CHANNEL, verified=False)

    discord.notify_task_failure("sync_note", "ValueError", bot_id=operator.id)

    assert sent_paths(discord_api) == [
        (f"/api/v10/channels/{OPERATOR_CHANNEL}/messages", "Bot tok-operator@example.com")
    ]


@pytest.mark.parametrize("operator_channels", [[], [1_500, OPERATOR_CHANNEL]])
def test_alert_sends_nothing_without_exactly_one_named_channel(db_session, discord_api, alerts_on, operator_channels):
    operator = add_bot(db_session, "operator@example.com", 7)
    mallory = add_bot(db_session, "mallory@example.com", 8)
    add_channel(db_session, mallory, 2, OTHER_CHANNEL)
    for server_id, channel_id in enumerate(operator_channels, start=10):
        add_channel(db_session, operator, server_id, channel_id)

    discord.notify_task_failure("sync_note", "ValueError", bot_id=operator.id)

    assert discord_api == []


@pytest.mark.parametrize("operator_channel_type", ["text", "news"])
@pytest.mark.parametrize("other_type", ["thread", "voice", "forum"])
def test_alert_ignores_same_named_threads_and_non_text_channels(
    db_session, discord_api, alerts_on, operator_channel_type, other_type
):
    operator = add_bot(db_session, "operator@example.com", 7)
    add_channel(db_session, operator, 1, OPERATOR_CHANNEL, channel_type=operator_channel_type)
    add_channel(db_session, operator, 1, OTHER_CHANNEL, channel_type=other_type)

    discord.notify_task_failure("sync_note", "ValueError", bot_id=operator.id)

    assert sent_paths(discord_api) == [
        (f"/api/v10/channels/{OPERATOR_CHANNEL}/messages", "Bot tok-operator@example.com")
    ]


@pytest.mark.parametrize("channel_type", ["thread", "voice", "forum"])
def test_alert_never_goes_to_a_same_named_thread_alone(db_session, discord_api, alerts_on, channel_type):
    operator = add_bot(db_session, "operator@example.com", 7)
    add_channel(db_session, operator, 1, OTHER_CHANNEL, channel_type=channel_type)

    discord.notify_task_failure("sync_note", "ValueError", bot_id=operator.id)

    assert discord_api == []


def test_alert_goes_to_configured_channel_id(db_session, discord_api, alerts_on, monkeypatch):
    operator = add_bot(db_session, "operator@example.com", 7)
    db_session.commit()
    monkeypatch.setattr(settings, "DISCORD_ERROR_CHANNEL", "4242")

    discord.notify_task_failure("sync_note", "ValueError", bot_id=operator.id)

    assert sent_paths(discord_api) == [("/api/v10/channels/4242/messages", "Bot tok-operator@example.com")]


def test_alert_uses_settings_bot(db_session, discord_api, alerts_on, monkeypatch):
    operator = add_bot(db_session, "operator@example.com", 7)
    add_channel(db_session, operator, 1, OPERATOR_CHANNEL)
    monkeypatch.setattr(settings, "DISCORD_BOT_ID", operator.id)

    discord.notify_task_failure("sync_note", "ValueError", task_id="abc-123", job_id=17)

    assert [json.loads(r.content)["content"] for r in discord_api] == [
        "🚨 **Task Failed: sync_note**\n**Error:** ValueError\n**Task id:** abc-123\n**Job id:** 17"
    ]


@pytest.mark.parametrize(
    "task_id, job_id, expected",
    [
        (None, None, "🚨 **Task Failed: t**\n**Error:** KeyError"),
        ("abc", None, "🚨 **Task Failed: t**\n**Error:** KeyError\n**Task id:** abc"),
        (None, 5, "🚨 **Task Failed: t**\n**Error:** KeyError\n**Job id:** 5"),
    ],
)
def test_failure_message_holds_ids_only(task_id, job_id, expected):
    assert discord.failure_message("t", "KeyError", task_id, job_id) == expected


@pytest.mark.parametrize(
    "kwargs, expected",
    [({"job_id": 9}, 9), ({"job_id": "9"}, None), ({"job_id": True}, None), ({}, None)],
)
def test_job_id_of(kwargs, expected):
    assert discord.job_id_of(kwargs) == expected


def test_failed_task_alert_carries_no_task_arguments(db_session, discord_api, alerts_on, monkeypatch):
    operator = add_bot(db_session, "operator@example.com", 7)
    add_channel(db_session, operator, 1, OPERATOR_CHANNEL)
    monkeypatch.setattr(settings, "DISCORD_BOT_ID", operator.id)

    @safe_task_execution
    def sync_note(subject: str, content: str, job_id: int):
        raise ValueError(f"bad row: {content}")

    with pytest.raises(ValueError):
        sync_note(subject="salary review", content="PRIVATE NOTE TEXT", job_id=3)

    assert [json.loads(r.content)["content"] for r in discord_api] == [
        "🚨 **Task Failed: sync_note**\n**Error:** ValueError\n**Job id:** 3"
    ]


@patch("memory.common.discord.send_error_message")
@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", False)
def test_notify_task_failure_disabled(mock_send_error):
    discord.notify_task_failure("test_task", "ValueError", bot_id=BOT_ID)

    mock_send_error.assert_not_called()


@patch("memory.common.discord.send_error_message")
@patch("memory.common.settings.DISCORD_NOTIFICATIONS_ENABLED", True)
def test_notify_task_failure_send_error_exception(mock_send_error):
    """Exceptions while sending never reach the failing task."""
    mock_send_error.side_effect = Exception("Failed to send")

    discord.notify_task_failure("test_task", "ValueError", bot_id=BOT_ID)

    mock_send_error.assert_called_once()


@patch("requests.get")
def test_is_collector_healthy_missing_status_key(mock_get, mock_api_url):
    """Test health check when response doesn't have status key"""
    mock_response = Mock()
    mock_response.json.return_value = {}
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    result = discord.is_collector_healthy(BOT_ID)

    assert result is False
