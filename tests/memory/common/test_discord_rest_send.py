"""Every bot sends through the REST API at its API URL, whatever its real-time mode."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from memory.common import discord
from memory.common.db.models import DiscordBot, DiscordChannel, DiscordServer, DiscordUser, HumanUser
from memory.common.discord_data import get_user_bots
from memory.discord import rest


@pytest.fixture
def api():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/users/@me/channels"):
            return httpx.Response(200, json={"id": "555"})
        return httpx.Response(200, json={"id": "1"})

    with (
        patch.object(rest, "public_http_client", side_effect=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw)),
        patch.object(rest.asyncio, "sleep", new=AsyncMock()),
    ):
        yield requests


REALTIME = pytest.mark.parametrize("realtime", ["gateway", "webhook"])


def make_bot(db_session, realtime: str = "gateway") -> DiscordBot:
    bot = DiscordBot(application_id=7, name="bot", realtime=realtime, public_key="ab" * 32)
    bot.token = "tok"
    db_session.add(bot)
    db_session.flush()
    db_session.add_all([
        DiscordServer(id=1, name="srv", bot_id=bot.id),
        DiscordUser(id=200, username="alice"),
    ])
    db_session.flush()
    db_session.add(DiscordChannel(id=10, server_id=1, name="general", channel_type="text"))
    db_session.commit()
    return bot


@REALTIME
@pytest.mark.parametrize("channel", ["10", 10, "general"])
def test_a_bot_sends_to_a_channel_over_rest(db_session, api, channel, realtime):
    bot = make_bot(db_session, realtime)

    assert discord.send_to_channel(bot.id, channel, "hello") is True

    assert [(r.method, r.url.path, r.headers["authorization"]) for r in api] == [
        ("POST", "/api/v10/channels/10/messages", "Bot tok")
    ]


@REALTIME
@pytest.mark.parametrize("user", ["200", "alice"])
def test_a_bot_sends_a_dm_over_rest(db_session, api, user, realtime):
    bot = make_bot(db_session, realtime)

    assert discord.send_dm(bot.id, user, "hello") is True

    assert [(r.method, r.url.path) for r in api] == [
        ("POST", "/api/v10/users/@me/channels"),
        ("POST", "/api/v10/channels/555/messages"),
    ]


def test_a_reaction_goes_to_the_bots_api_url(db_session, api):
    bot = make_bot(db_session)
    bot.api_base_url = "https://chat.example.com/discord/api/v10"
    db_session.commit()

    with patch("memory.common.chat_urls.settings.CHAT_API_ALLOWED_ORIGINS", ["https://chat.example.com"]):
        assert discord.add_reaction(bot.id, "general", 99, "👍") is True

    assert [(r.method, r.url.host, r.url.raw_path) for r in api] == [
        ("PUT", "chat.example.com", b"/discord/api/v10/channels/10/messages/99/reactions/%F0%9F%91%8D/@me")
    ]


def test_a_dm_goes_only_to_a_username_not_a_display_name(db_session, api):
    bot = make_bot(db_session)
    db_session.add(DiscordUser(id=201, username="mallory", display_name="Alice Admin"))
    db_session.commit()

    assert discord.send_dm(bot.id, "Alice Admin", "hello") is False
    assert api == []


def test_a_paused_bot_does_not_send(db_session, api):
    bot = make_bot(db_session)
    bot.is_active = False
    db_session.commit()

    assert discord.send_to_channel(bot.id, "10", "hello") is False
    assert api == []


def test_a_failed_rest_send_reports_false(db_session):
    bot = make_bot(db_session)

    forbidden = httpx.MockTransport(lambda r: httpx.Response(403, json={"code": 50001}))
    with patch.object(rest, "public_http_client", side_effect=lambda **kw: httpx.AsyncClient(transport=forbidden, **kw)):
        assert discord.send_to_channel(bot.id, "10", "hello") is False


def test_a_users_default_bot_is_a_gateway_bot(db_session):
    owner = HumanUser(email="o@example.com", name="O", password_hash="x")
    db_session.add(owner)
    db_session.flush()
    hooked = DiscordBot(application_id=1, name="hooked", realtime="webhook", public_key="ab" * 32, created_by_user_id=owner.id)
    db_session.add(hooked)
    db_session.flush()
    gateway = DiscordBot(application_id=2, name="gateway", created_by_user_id=owner.id)
    db_session.add(gateway)
    db_session.commit()
    db_session.expire_all()

    assert [b.name for b in get_user_bots(db_session, owner.id)] == ["gateway", "hooked"]
    assert [b.name for b in db_session.get(HumanUser, owner.id).discord_bots] == ["gateway", "hooked"]
