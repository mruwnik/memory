from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from memory.discord import api


@pytest.fixture(autouse=True)
def reset_app_bots():
    existing = getattr(api.app, "bots", None)
    api.app.bots = {}
    yield
    if existing is None:
        delattr(api.app, "bots")
    else:
        api.app.bots = existing


@pytest.fixture
def active_bot():
    collector = SimpleNamespace(
        send_dm=AsyncMock(return_value=True),
        trigger_typing_dm=AsyncMock(return_value=True),
        send_to_channel=AsyncMock(return_value=True),
        trigger_typing_channel=AsyncMock(return_value=True),
        add_reaction=AsyncMock(return_value=True),
        refresh_metadata=AsyncMock(return_value={"refreshed": True}),
        is_closed=Mock(return_value=False),
        user="CollectorUser#1234",
        guilds=[101, 202],
    )
    bot = SimpleNamespace(
        collector=collector,
        collector_task=None,
        bot_id=1,
        bot_token="token-123",
        bot_name="Test Bot",
    )
    api.app.bots[bot.bot_id] = bot
    return bot


@pytest.mark.asyncio
async def test_send_dm_success(active_bot):
    request = api.SendDMRequest(bot_id=active_bot.bot_id, user="user123", message="Hello")

    response = await api.send_dm_endpoint(request)

    assert response == {
        "success": True,
        "message": "DM sent to user123",
        "user": "user123",
    }
    active_bot.collector.send_dm.assert_awaited_once_with("user123", "Hello")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint,payload",
    [
        (
            api.send_dm_endpoint,
            api.SendDMRequest(bot_id=99, user="ghost", message="hi"),
        ),
        (
            api.trigger_dm_typing,
            api.TypingDMRequest(bot_id=99, user="ghost"),
        ),
        (
            api.send_channel_endpoint,
            api.SendChannelRequest(bot_id=99, channel="general", message="hello"),
        ),
        (
            api.trigger_channel_typing,
            api.TypingChannelRequest(bot_id=99, channel="general"),
        ),
        (
            api.add_reaction_endpoint,
            api.AddReactionRequest(
                bot_id=99,
                channel="general",
                message_id=42,
                emoji=":thumbsup:",
            ),
        ),
    ],
)
async def test_endpoint_returns_404_when_bot_missing(endpoint, payload):
    with pytest.raises(HTTPException) as exc:
        await endpoint(payload)

    assert exc.value.status_code == 404
    assert exc.value.detail == "Bot not found"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint,request_cls,request_kwargs,attr_name,detail_template",
    [
        (
            api.send_dm_endpoint,
            api.SendDMRequest,
            {"bot_id": 1, "user": "user123", "message": "Hi"},
            "send_dm",
            "Failed to send DM to {user}",
        ),
        (
            api.trigger_dm_typing,
            api.TypingDMRequest,
            {"bot_id": 1, "user": "user123"},
            "trigger_typing_dm",
            "Failed to trigger typing for user123",
        ),
        (
            api.send_channel_endpoint,
            api.SendChannelRequest,
            {"bot_id": 1, "channel": "general", "message": "Hello"},
            "send_to_channel",
            "Failed to send message to channel general",
        ),
        (
            api.trigger_channel_typing,
            api.TypingChannelRequest,
            {"bot_id": 1, "channel": "general"},
            "trigger_typing_channel",
            "Failed to trigger typing for channel general",
        ),
        (
            api.add_reaction_endpoint,
            api.AddReactionRequest,
            {"bot_id": 1, "channel": "general", "message_id": 55, "emoji": ":fire:"},
            "add_reaction",
            "Failed to add reaction to message 55",
        ),
    ],
)
async def test_endpoint_returns_400_on_collector_failure(
    active_bot, endpoint, request_cls, request_kwargs, attr_name, detail_template
):
    request = request_cls(**request_kwargs)
    getattr(active_bot.collector, attr_name).return_value = False
    expected_detail = detail_template.format(**request_kwargs)

    with pytest.raises(HTTPException) as exc:
        await endpoint(request)

    assert exc.value.status_code == 400
    assert exc.value.detail == expected_detail


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint,request_cls,request_kwargs,attr_name",
    [
        (
            api.send_dm_endpoint,
            api.SendDMRequest,
            {"bot_id": 1, "user": "user123", "message": "Hi"},
            "send_dm",
        ),
        (
            api.trigger_dm_typing,
            api.TypingDMRequest,
            {"bot_id": 1, "user": "user123"},
            "trigger_typing_dm",
        ),
        (
            api.send_channel_endpoint,
            api.SendChannelRequest,
            {"bot_id": 1, "channel": "general", "message": "Hello"},
            "send_to_channel",
        ),
        (
            api.trigger_channel_typing,
            api.TypingChannelRequest,
            {"bot_id": 1, "channel": "general"},
            "trigger_typing_channel",
        ),
        (
            api.add_reaction_endpoint,
            api.AddReactionRequest,
            {"bot_id": 1, "channel": "general", "message_id": 55, "emoji": ":fire:"},
            "add_reaction",
        ),
    ],
)
async def test_endpoint_returns_500_on_collector_exception(
    active_bot, endpoint, request_cls, request_kwargs, attr_name
):
    request = request_cls(**request_kwargs)
    getattr(active_bot.collector, attr_name).side_effect = RuntimeError("boom")

    with pytest.raises(HTTPException) as exc:
        await endpoint(request)

    assert exc.value.status_code == 500
    assert "boom" in exc.value.detail


@pytest.mark.asyncio
async def test_health_check_success(active_bot):
    response = await api.health_check()

    assert response["Test Bot"] == {
        "status": "healthy",
        "connected": True,
        "user": "CollectorUser#1234",
        "guilds": 2,
    }
    active_bot.collector.is_closed.assert_called_once_with()


@pytest.mark.asyncio
async def test_health_check_without_bots():
    with pytest.raises(HTTPException) as exc:
        await api.health_check()

    assert exc.value.status_code == 503
    assert exc.value.detail == "Discord collector not running"


@pytest.mark.asyncio
async def test_refresh_metadata_success(active_bot):
    active_bot.collector.refresh_metadata.return_value = {"channels": 3}

    response = await api.refresh_metadata()

    assert response["success"] is True
    assert response["message"] == "Metadata refreshed successfully for 1 bots"
    assert response["results"]["Test Bot"] == {"channels": 3}
    active_bot.collector.refresh_metadata.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_refresh_metadata_without_bots():
    with pytest.raises(HTTPException) as exc:
        await api.refresh_metadata()

    assert exc.value.status_code == 503
    assert exc.value.detail == "Discord collector not running"


@pytest.mark.asyncio
async def test_refresh_metadata_failure(active_bot):
    active_bot.collector.refresh_metadata.side_effect = RuntimeError("sync failed")

    with pytest.raises(HTTPException) as exc:
        await api.refresh_metadata()

    assert exc.value.status_code == 500
    assert "sync failed" in exc.value.detail
