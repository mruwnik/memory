"""A gateway that stays connected but stops delivering is caught by checking REST."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

from memory.common.celery_app import BACKFILL_DISCORD_CHANNEL
from memory.common.db.models import (
    DiscordBot,
    DiscordBotServer,
    DiscordChannel,
    DiscordMessage,
    DiscordServer,
    DiscordUser,
)
from memory.discord import rest
from memory.discord.backfill import snowflake_at
from memory.discord.collector import CollectorManager, MessageCollector

def minutes_ago(minutes: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes)


@pytest.fixture
def bot_id(db_session):
    bot = DiscordBot(application_id=1, name="bot")
    bot.token = "tok"
    db_session.add(bot)
    db_session.flush()
    db_session.add_all([
        DiscordServer(id=1, name="srv", collect_messages=True, bot_id=bot.id),
        DiscordUser(id=100, username="alice"),
    ])
    db_session.flush()
    db_session.add_all([
        DiscordBotServer(bot_id=bot.id, server_id=1),
        DiscordChannel(id=10, server_id=1, name="general", channel_type="text"),
        DiscordChannel(id=20, name="dm", channel_type="dm", bot_id=bot.id),
    ])
    db_session.flush()
    for channel_id in (10, 20):
        sent = minutes_ago(20)
        db_session.add(DiscordMessage(
            modality="message", sha256=channel_id.to_bytes(32, "big"), content="hi",
            message_id=snowflake_at(sent) + channel_id, channel_id=channel_id, author_id=100, sent_at=sent,
        ))
    db_session.commit()
    return bot.id


def upstream(newest: dict[int, float] | None = None, status: int = 200):
    """Discord serving each channel's newest message, minted the given minutes ago."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if status != 200:
            return httpx.Response(status, json={"code": 0})
        message_id = snowflake_at(minutes_ago((newest or {})[int(request.url.path.split("/")[-2])]))
        before = int(request.url.params["before"])
        page = [{"id": str(message_id)}] if message_id < before else []
        return httpx.Response(200, json=page)

    client = lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw)  # noqa: E731
    return patch.object(rest, "public_http_client", side_effect=client), requests


@pytest_asyncio.fixture
async def manager(bot_id):
    async def run_until_closed(self, token):
        await asyncio.Event().wait()

    with (
        patch.object(MessageCollector, "start", run_until_closed),
        patch.object(MessageCollector, "close", AsyncMock()),
        patch.object(MessageCollector, "is_ready", lambda self: True),
    ):
        manager = CollectorManager(celery_app=MagicMock())
        await manager.sync()
        yield manager
        await manager.stop_all()


def silence(manager: CollectorManager, bot_id: int, minutes: float) -> MessageCollector:
    collector = manager.collectors[bot_id]
    collector.last_event_at = minutes_ago(minutes)
    return collector


def alerts(caplog, bot_id: int) -> list[tuple[int, bool]]:
    """Level of each collector warning or error, and whether it names the bot."""
    return [
        (r.levelno, r.message.startswith(f"Bot {bot_id}:"))
        for r in caplog.records
        if r.name == "memory.discord.collector" and r.levelno >= logging.WARNING
    ]


def backfills(manager: CollectorManager) -> list[int]:
    sent = cast(MagicMock, manager.celery_app.send_task)
    return [c.kwargs["args"][0] for c in sent.call_args_list if c.args[0] == BACKFILL_DISCORD_CHANNEL]


@pytest.mark.asyncio
async def test_a_silent_gateway_missing_messages_is_reconnected_and_backfilled(manager, bot_id, caplog):
    stalled = silence(manager, bot_id, 15)
    patched, _ = upstream({10: 5, 20: 20})

    with patched, caplog.at_level(logging.WARNING):
        await manager.rescue_stalled()

    assert manager.collectors[bot_id] is not stalled
    assert backfills(manager) == [10]
    assert alerts(caplog, bot_id) == [(logging.WARNING, True)]


@pytest.mark.parametrize(
    "newest",
    [
        {10: 20, 20: 20},
        # Sent before the gateway went quiet: its delivery is the event that came last.
        {10: 17, 20: 20},
        # Not yet past the delivery grace.
        {10: 0.2, 20: 20},
    ],
)
@pytest.mark.asyncio
async def test_a_quiet_but_caught_up_bot_is_left_alone(manager, bot_id, newest):
    quiet = silence(manager, bot_id, 15)
    patched, requests = upstream(newest)

    with patched:
        await manager.rescue_stalled()
        await manager.rescue_stalled()

    assert manager.collectors[bot_id] is quiet
    assert backfills(manager) == []
    assert sorted(int(r.url.path.split("/")[-2]) for r in requests) == [10, 20]


@pytest.mark.asyncio
async def test_a_bot_with_fresh_events_is_not_probed(manager, bot_id):
    silence(manager, bot_id, 1)
    patched, requests = upstream()

    with patched:
        await manager.rescue_stalled()

    assert requests == []


@pytest.mark.asyncio
async def test_a_bot_rescued_again_soon_is_logged_as_an_error_and_probed_less_often(manager, bot_id, caplog):
    patched, _ = upstream({10: 5, 20: 20})
    with patched, caplog.at_level(logging.WARNING):
        silence(manager, bot_id, 15)
        await manager.rescue_stalled()
        interval = manager.watches[bot_id].interval
        silence(manager, bot_id, 15)
        manager.watches[bot_id].probed_at = None
        await manager.rescue_stalled()

    assert alerts(caplog, bot_id) == [(logging.WARNING, True), (logging.ERROR, True)]
    assert manager.watches[bot_id].interval == interval * 2


@pytest.mark.asyncio
async def test_a_rest_failure_during_the_probe_leaves_the_gateway_alone(manager, bot_id, caplog):
    quiet = silence(manager, bot_id, 15)
    patched, requests = upstream(status=502)

    with patched, caplog.at_level(logging.INFO):
        await manager.rescue_stalled()
        await manager.rescue_stalled()

    assert manager.collectors[bot_id] is quiet
    assert backfills(manager) == []
    assert len(requests) == 1
    assert alerts(caplog, bot_id) == [(logging.WARNING, True)]
