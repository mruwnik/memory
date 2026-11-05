"""Which bots the collector runs, and how a bot reaches a Discord-compatible server."""

import asyncio
import socket
import threading
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
import pytest
import pytest_asyncio
import yarl
from aiohttp import ClientConnectorError, ClientPayloadError, ClientSession, WSMessage, WSMsgType, web
from discord.errors import ConnectionClosed, GatewayNotFound, HTTPException, LoginFailure, PrivilegedIntentsRequired
from discord.gateway import DiscordWebSocket
from discord.http import HTTPClient, Route

from memory.common.db.models import DiscordBot, HumanUser
from memory.common.db.models.discord import DEFAULT_DISCORD_API_BASE_URL
from memory.common.safe_http import NetworkPolicy, parse_networks
from memory.discord import proxied_http
from memory.discord.collector import BotInfo, CollectorManager, MessageCollector, gateway_allowed
from memory.discord.proxied_http import PolicyConnector, PolicyResolver, ProxiedHTTPClient, ZlibTextFrames

PROXY_API = "https://chat.example.com/discord/api/v10"


@pytest.fixture
def idle_gateway():
    """Collectors 'connect' without a network and stay up until closed."""

    async def run_until_closed(self, token):
        await asyncio.Event().wait()

    with (
        patch.object(MessageCollector, "start", run_until_closed),
        patch.object(MessageCollector, "close", AsyncMock()),
    ):
        yield


def add_bot(db_session, application_id: int, **fields) -> DiscordBot:
    bot = DiscordBot(application_id=application_id, name=f"bot{application_id}", **fields)
    bot.token = "tok"
    db_session.add(bot)
    db_session.commit()
    return bot


def running(manager: CollectorManager) -> dict[int, BotInfo]:
    return {bot_id: c.bot_info for bot_id, c in manager.collectors.items()}


@pytest.mark.asyncio
async def test_the_collector_runs_active_gateway_bots_only(db_session, idle_gateway):
    gateway = add_bot(db_session, 1)
    add_bot(db_session, 2, realtime="webhook", public_key="ab" * 32)
    add_bot(db_session, 3, is_active=False)
    tokenless = add_bot(db_session, 4)
    tokenless.token = None
    db_session.commit()
    with patch("memory.common.chat_urls.settings.CHAT_API_ALLOWED_ORIGINS", []):
        add_bot(db_session, 5, api_base_url=PROXY_API)

        manager = CollectorManager(celery_app=None)  # type: ignore[arg-type]
        await manager.sync()

    assert running(manager) == {gateway.id: BotInfo(gateway.id, "bot1", "tok", DEFAULT_DISCORD_API_BASE_URL)}
    await manager.stop_all()


@pytest.mark.asyncio
async def test_the_collector_follows_mode_and_url_changes(db_session, idle_gateway):
    bot = add_bot(db_session, 1, public_key="ab" * 32)
    manager = CollectorManager(celery_app=None)  # type: ignore[arg-type]
    seen = []

    with patch("memory.common.chat_urls.settings.CHAT_API_ALLOWED_ORIGINS", ["https://chat.example.com"]):
        for change in ({}, {"realtime": "webhook"}, {"realtime": "gateway"}, {"api_base_url": PROXY_API}):
            for field, value in change.items():
                setattr(bot, field, value)
            db_session.commit()
            await manager.sync()
            await asyncio.sleep(0)
            seen.append({i: info.api_base_url for i, info in running(manager).items()})

    assert seen == [
        {bot.id: DEFAULT_DISCORD_API_BASE_URL},
        {},
        {bot.id: DEFAULT_DISCORD_API_BASE_URL},
        {bot.id: PROXY_API},
    ]
    await manager.stop_all()


@pytest.mark.asyncio
async def test_a_discord_bot_uses_discord_py_unchanged():
    collector = MessageCollector(BotInfo(1, "bot", "tok", DEFAULT_DISCORD_API_BASE_URL), celery_app=None)  # type: ignore[arg-type]

    assert type(collector.http) is HTTPClient


@pytest.mark.asyncio
async def test_a_proxied_bot_sends_rest_and_gateway_through_one_client():
    collector = MessageCollector(BotInfo(1, "bot", "tok", PROXY_API), celery_app=None)  # type: ignore[arg-type]

    assert isinstance(collector.http, ProxiedHTTPClient)
    assert collector._connection.http is collector.http


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "refusal",
    [
        LoginFailure("Improper token has been passed."),
        PrivilegedIntentsRequired(None),
        ConnectionClosed(SimpleNamespace(close_code=4004), shard_id=None),
    ],
)
async def test_a_bot_discord_refuses_waits_for_new_settings(db_session, refusal):
    bot = add_bot(db_session, 1)
    logins: list[str] = []

    async def refuse(self, token):
        logins.append(token)
        raise refusal

    manager = CollectorManager(celery_app=None)  # type: ignore[arg-type]
    with patch.object(MessageCollector, "start", refuse), patch.object(MessageCollector, "close", AsyncMock()):
        for token in ("tok", "tok", "new-token"):
            bot.token = token
            db_session.commit()
            await manager.sync()
            for _ in range(3):
                await asyncio.sleep(0)

    assert logins == ["tok", "new-token"]
    await manager.stop_all()


@pytest.mark.asyncio
async def test_a_crashing_bot_is_retried_with_growing_delays(db_session):
    add_bot(db_session, 1)
    clock = [0.0]
    starts: list[float] = []

    async def crash(self, token):
        starts.append(clock[0])
        raise RuntimeError("gateway fell over")

    manager = CollectorManager(celery_app=None)  # type: ignore[arg-type]
    with (
        patch.object(MessageCollector, "start", crash),
        patch.object(MessageCollector, "close", AsyncMock()),
        patch("memory.discord.collector.monotonic", lambda: clock[0]),
    ):
        for clock[0] in range(0, 600, 60):
            await manager.sync()
            for _ in range(3):
                await asyncio.sleep(0)

    assert starts == [0, 60, 180, 420]
    await manager.stop_all()


@pytest.mark.parametrize(
    "admin, gateway, allowed",
    [
        (False, "wss://chat.example.com/discord/gateway", True),
        (False, "wss://chat.example.com:8443/gateway", False),
        (False, "wss://169.254.169.254/gateway", False),
        (True, "wss://169.254.169.254/gateway", True),
    ],
)
def test_the_gateway_host_must_be_an_allowed_chat_origin(db_session, admin, gateway, allowed):
    owner = HumanUser(email="o@example.com", name="O", password_hash="x", scopes=["*"] if admin else ["read"])
    db_session.add(owner)
    db_session.flush()
    bot = add_bot(db_session, 1, created_by_user_id=owner.id)

    with patch("memory.common.chat_urls.settings.CHAT_API_ALLOWED_ORIGINS", ["https://chat.example.com"]):
        assert gateway_allowed(bot.id, gateway) is allowed


def allow_all(url: str) -> bool:
    return True


@pytest.fixture
def proxied():
    return ProxiedHTTPClient(PROXY_API + "/", allow_all, asyncio.new_event_loop())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "route, url",
    [
        (Route("GET", "/users/@me"), f"{PROXY_API}/users/@me"),
        (Route("GET", "/gateway/bot"), f"{PROXY_API}/gateway/bot"),
        (Route("POST", "/channels/{channel_id}/messages", channel_id=10), f"{PROXY_API}/channels/10/messages"),
    ],
)
async def test_rest_calls_go_to_the_bots_api_url_once(proxied, route, url):
    with patch.object(HTTPClient, "request", AsyncMock(return_value={})) as sent:
        await proxied.request(route)
        await proxied.request(route)

    assert [call.args[0].url for call in sent.call_args_list] == [url, url]
    assert sent.call_args.kwargs["allow_redirects"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "requested, gateway, dialed",
    [
        (
            DiscordWebSocket.DEFAULT_GATEWAY.with_query(v=10, encoding="json", compress="zstd-stream"),
            "wss://chat.example.com/gateway",
            "wss://chat.example.com/gateway?v=10&encoding=json&compress=zlib-stream",
        ),
        (
            yarl.URL("wss://resume.chat.example.com/?v=10&encoding=json&compress=zlib-stream"),
            "wss://unused.example.com",
            "wss://resume.chat.example.com/?v=10&encoding=json&compress=zlib-stream",
        ),
    ],
)
async def test_the_gateway_is_the_one_the_api_url_names(proxied, requested, gateway, dialed):
    with (
        patch.object(ProxiedHTTPClient, "get_bot_gateway", AsyncMock(return_value=(1, gateway, {}))),
        patch.object(ProxiedHTTPClient, "dial", AsyncMock()) as connect,
    ):
        socket = await proxied.ws_connect(str(requested))

    assert connect.call_args.args[0] == dialed
    assert isinstance(socket, ZlibTextFrames)


@pytest.mark.asyncio
async def test_a_voice_socket_is_left_alone(proxied):
    with (
        patch.object(ProxiedHTTPClient, "get_bot_gateway", AsyncMock()) as lookup,
        patch.object(HTTPClient, "ws_connect", AsyncMock(return_value="voice socket")) as connect,
    ):
        socket = await proxied.ws_connect("wss://voice.example.com/?v=4", compress=15)

    assert (socket, connect.call_args.args[0], lookup.called) == ("voice socket", "wss://voice.example.com/?v=4", False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "requested, named",
    [
        (str(DiscordWebSocket.DEFAULT_GATEWAY), "ws://chat.example.com"),
        (str(DiscordWebSocket.DEFAULT_GATEWAY), "wss://169.254.169.254/gateway"),
        ("wss://169.254.169.254/?v=10", "wss://unused.example.com"),
    ],
)
async def test_a_gateway_off_the_allowlist_or_unencrypted_is_refused(requested, named):
    proxied = ProxiedHTTPClient(PROXY_API, lambda url: "169.254" not in url, asyncio.new_event_loop())
    with (
        patch.object(ProxiedHTTPClient, "get_bot_gateway", AsyncMock(return_value=(1, named, {}))),
        patch.object(ProxiedHTTPClient, "dial", AsyncMock()) as connect,
        pytest.raises(GatewayNotFound),
    ):
        await proxied.ws_connect(requested)

    connect.assert_not_called()


@pytest.mark.asyncio
async def test_the_gateway_check_runs_off_the_event_loop():
    checked_on = []

    def allowed(url):
        checked_on.append(threading.get_ident())
        return True

    proxied = ProxiedHTTPClient(PROXY_API, allowed, asyncio.get_running_loop())
    with patch.object(ProxiedHTTPClient, "dial", AsyncMock()):
        await proxied.ws_connect("wss://resume.chat.example.com/?v=10")

    assert checked_on and threading.get_ident() not in checked_on


@pytest.mark.asyncio
async def test_the_gateway_transport_caps_message_size():
    async def oversized(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_str("x" * 2048)
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/", oversized)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    proxied = ProxiedHTTPClient(PROXY_API, allow_all, asyncio.get_running_loop())
    proxied._HTTPClient__session = ClientSession()  # what static_login would set

    with patch.object(proxied_http, "MAX_GATEWAY_MESSAGE_BYTES", 1024):
        socket = await proxied.dial(f"ws://127.0.0.1:{runner.addresses[0][1]}/")
        message = await socket.receive()

    await socket.close()
    await proxied._HTTPClient__session.close()
    await runner.cleanup()
    assert message.type is WSMsgType.ERROR


class FakeSocket:
    closed = False

    def __init__(self, messages):
        self.messages = list(messages)

    async def receive(self, timeout=None):
        return self.messages.pop(0)


def frames_socket(frames) -> ZlibTextFrames:
    return ZlibTextFrames(FakeSocket(
        WSMessage(WSMsgType.TEXT if isinstance(f, str) else WSMsgType.BINARY, f, None) for f in frames
    ))


@pytest.mark.asyncio
async def test_a_zlib_stream_reaches_discord_py_as_text_frames():
    deflate = zlib.compressobj()
    first = deflate.compress(b'{"op": 10}') + deflate.flush(zlib.Z_SYNC_FLUSH)
    second = deflate.compress(b'{"op": 11}') + deflate.flush(zlib.Z_SYNC_FLUSH)
    socket = frames_socket([first[:3], first[3:], second, "plain"])

    received = [await socket.receive() for _ in range(3)]

    assert [(m.type, m.data) for m in received] == [
        (WSMsgType.TEXT, '{"op": 10}'), (WSMsgType.TEXT, '{"op": 11}'), (WSMsgType.TEXT, "plain"),
    ]
    assert socket.closed is False


@pytest.mark.asyncio
@pytest.mark.parametrize("compressed", [True, False])
async def test_an_oversized_gateway_message_is_refused(compressed):
    deflate = zlib.compressobj()
    bomb = deflate.compress(b"0" * 2048) + deflate.flush(zlib.Z_SYNC_FLUSH)
    unflushed = b"x" * 2048
    socket = frames_socket([bomb if compressed else unflushed])

    with patch.object(proxied_http, "MAX_GATEWAY_MESSAGE_BYTES", 1024), pytest.raises(ClientPayloadError):
        await socket.receive()


@pytest_asyncio.fixture
async def servers():
    """Plain HTTP servers on loopback that record what reached them."""
    hits: dict[str, list[str]] = {"target": [], "redirector": []}
    runners = []

    async def start(name, handler):
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        runners.append(runner)
        return f"http://127.0.0.1:{runner.addresses[0][1]}"

    async def target(request):
        hits["target"].append(request.path)
        return web.json_response({})

    target_url = await start("target", target)

    async def redirector(request):
        hits["redirector"].append(request.path)
        raise web.HTTPFound(f"{target_url}{request.path}")

    redirect_url = await start("redirector", redirector)
    yield {"target": target_url, "redirector": redirect_url, "hits": hits}
    for runner in runners:
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("open_connection", ["get", "ws_connect"])
async def test_the_connector_checks_ip_literal_hosts(servers, open_connection):
    async with ClientSession(connector=PolicyConnector(NetworkPolicy())) as session:
        with pytest.raises(ClientConnectorError):
            await getattr(session, open_connection)(f"{servers['target']}/gateway")

    assert servers["hits"]["target"] == []


@pytest.mark.asyncio
async def test_an_ip_literal_api_url_is_refused(servers):
    client = ProxiedHTTPClient(f"{servers['target']}/api/v10", allow_all, asyncio.get_running_loop())

    with pytest.raises(ClientConnectorError):
        await client.static_login("tok")

    await client.close()
    assert servers["hits"]["target"] == []


@pytest.mark.asyncio
async def test_rest_redirects_are_not_followed(servers):
    client = ProxiedHTTPClient(
        f"{servers['redirector']}/api/v10", allow_all, asyncio.get_running_loop(),
        policy=NetworkPolicy(loopback_allowed=True),
    )

    with pytest.raises(HTTPException) as refused:
        await client.static_login("tok")

    await client.close()
    assert (refused.value.status, servers["hits"]) == (
        302, {"redirector": ["/api/v10/users/@me"], "target": []},
    )


@pytest.mark.asyncio
async def test_the_resolver_returns_only_checked_addresses():
    resolver = PolicyResolver(NetworkPolicy())

    results = await resolver.resolve("93.184.216.34", 443)

    assert [(r["host"], r["port"], r["family"], r["hostname"]) for r in results] == [
        ("93.184.216.34", 443, socket.AF_INET, "93.184.216.34")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.5", "169.254.169.254"])
async def test_the_resolver_refuses_private_addresses(host):
    resolver = PolicyResolver(NetworkPolicy(blocked_networks=parse_networks("10.0.0.0/8")))

    with pytest.raises(OSError):
        await resolver.resolve(host, 443)


def test_discord_py_is_the_pinned_version():
    """ProxiedHTTPClient leans on discord.py internals (Route.url, ws_connect and its session,
    DEFAULT_GATEWAY); re-check it against a new release before bumping the pin."""
    pins = (Path(__file__).parents[3] / "requirements/requirements-ingesters.txt").read_text()

    assert f"discord.py=={discord.__version__}" in pins.splitlines()
