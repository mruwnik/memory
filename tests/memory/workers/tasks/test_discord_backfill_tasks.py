from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memory.common import settings
from memory.common.celery_app import (
    app,
    BACKFILL_DISCORD_CHANNEL,
    DISCOVER_DISCORD_THREADS,
    SCHEDULE_DISCORD_BACKFILLS,
)
from memory.common.db.models import DiscordBot, DiscordChannel, DiscordServer


def test_backfill_task_names():
    assert BACKFILL_DISCORD_CHANNEL == (
        "memory.workers.tasks.discord_backfill.backfill_channel"
    )
    assert DISCOVER_DISCORD_THREADS == (
        "memory.workers.tasks.discord_backfill.discover_threads"
    )
    assert SCHEDULE_DISCORD_BACKFILLS == (
        "memory.workers.tasks.discord_backfill.schedule_discord_backfills"
    )


def test_backfill_routes_to_dedicated_queue():
    routes = app.conf.task_routes
    route = routes["memory.workers.tasks.discord_backfill.*"]
    assert route["queue"] == f"{settings.CELERY_QUEUE_PREFIX}-backfill"


def test_backfill_settings_have_defaults():
    assert settings.DISCORD_BACKFILL_INTERVAL > 0
    assert settings.DISCORD_BACKFILL_JITTER_SECONDS >= 0
    assert settings.DISCORD_BACKFILL_MAX_MESSAGES_PER_RUN > 0


@contextmanager
def acquired_lock(*args, **kwargs):
    yield MagicMock()  # truthy => lock acquired


@contextmanager
def held_lock(*args, **kwargs):
    yield None  # None => someone else holds it


def make_bot_server_channel(db_session, *, token="bot-token", channel_id=10):
    bot = DiscordBot(id=42, name="bot", is_active=True)
    bot.token = token  # encrypts
    server = DiscordServer(id=1, name="srv", collect_messages=True, bot_id=42)
    channel = DiscordChannel(id=channel_id, server_id=1, name="general",
                             channel_type="text")
    db_session.add_all([bot, server, channel])
    db_session.commit()
    return bot, server, channel


def test_resolve_channel_bot_token_happy_path(db_session):
    from memory.workers.tasks.discord_backfill import resolve_channel_bot_token

    make_bot_server_channel(db_session, token="abc")
    assert resolve_channel_bot_token(10) == (42, "abc")


def test_resolve_channel_bot_token_none_without_server(db_session):
    from memory.workers.tasks.discord_backfill import resolve_channel_bot_token

    db_session.add(DiscordChannel(id=99, server_id=None, name="dm",
                                  channel_type="dm"))
    db_session.commit()
    assert resolve_channel_bot_token(99) is None


def test_backfill_channel_skips_when_no_token(db_session):
    from memory.workers.tasks import discord_backfill

    with patch.object(discord_backfill, "resolve_channel_bot_token",
                      return_value=None):
        result = discord_backfill.backfill_channel(10)
    assert result["status"] == "skipped"


def test_backfill_channel_skips_when_locked(db_session):
    from memory.workers.tasks import discord_backfill

    with patch.object(discord_backfill, "resolve_channel_bot_token",
                      return_value=(42, "tok")), \
         patch.object(discord_backfill, "distributed_lock", held_lock):
        result = discord_backfill.backfill_channel(10)
    assert result["status"] == "locked"


def test_backfill_channel_redispatches_when_not_done(db_session):
    from memory.workers.tasks import discord_backfill

    with patch.object(discord_backfill, "resolve_channel_bot_token",
                      return_value=(42, "tok")), \
         patch.object(discord_backfill, "distributed_lock", acquired_lock), \
         patch.object(discord_backfill, "backfill_channel_messages",
                      new=AsyncMock(return_value={
                          "processed": 5000,
                          "done": False,
                          "oldest_message_id": 777,
                      })), \
         patch.object(discord_backfill.backfill_channel, "apply_async") as redispatch:
        result = discord_backfill.backfill_channel(10)

    assert result["done"] is False
    redispatch.assert_called_once()
    assert redispatch.call_args.kwargs["args"] == [10]
    # Continuation carries the cursor (oldest id fetched) in task kwargs, so it
    # doesn't re-derive MIN from the async ADD queue.
    assert redispatch.call_args.kwargs["kwargs"] == {"before_id": 777}


def test_backfill_channel_no_redispatch_when_done(db_session):
    from memory.workers.tasks import discord_backfill

    with patch.object(discord_backfill, "resolve_channel_bot_token",
                      return_value=(42, "tok")), \
         patch.object(discord_backfill, "distributed_lock", acquired_lock), \
         patch.object(discord_backfill, "backfill_channel_messages",
                      new=AsyncMock(return_value={
                          "processed": 3,
                          "done": True,
                          "oldest_message_id": 3,
                      })), \
         patch.object(discord_backfill.backfill_channel, "apply_async") as redispatch:
        result = discord_backfill.backfill_channel(10)

    assert result["done"] is True
    redispatch.assert_not_called()


@pytest.mark.parametrize(
    "server_collect,channel_collect,channel_type,expected",
    [
        (True, None, "text", True),    # inherit True
        (False, True, "text", True),   # explicit override True
        (True, False, "text", False),  # explicit override False
        (False, None, "text", False),  # inherit False
    ],
)
def test_collectible_channels_inheritance(
    db_session, server_collect, channel_collect, channel_type, expected
):
    from memory.workers.tasks.discord_backfill import collectible_channels

    db_session.add(DiscordServer(id=1, name="s", collect_messages=server_collect))
    db_session.add(
        DiscordChannel(id=10, server_id=1, name="c",
                       channel_type=channel_type, collect_messages=channel_collect)
    )
    db_session.commit()

    ids = [cid for cid, _ in collectible_channels()]
    assert (10 in ids) is expected


def test_collectible_channels_excludes_serverless_by_default(db_session):
    from memory.workers.tasks.discord_backfill import collectible_channels

    db_session.add(DiscordChannel(id=20, server_id=None, name="dm",
                                  channel_type="dm", collect_messages=None))
    db_session.commit()
    assert 20 not in [cid for cid, _ in collectible_channels()]


def test_schedule_dispatches_backfill_per_channel_and_threads_for_parents(db_session):
    from memory.workers.tasks import discord_backfill

    db_session.add(DiscordServer(id=1, name="s", collect_messages=True))
    db_session.add(DiscordChannel(id=10, server_id=1, name="text",
                                  channel_type="text"))
    db_session.add(DiscordChannel(id=11, server_id=1, name="thread",
                                  channel_type="thread"))
    db_session.commit()

    with patch.object(discord_backfill.backfill_channel, "apply_async") as bf, \
         patch.object(discord_backfill.discover_threads, "apply_async") as dt, \
         patch.object(discord_backfill.settings, "DISCORD_BACKFILL_JITTER_SECONDS", 0):
        result = discord_backfill.schedule_discord_backfills()

    assert result["scheduled"] == 2
    backfilled = {c.kwargs["args"][0] for c in bf.call_args_list}
    assert backfilled == {10, 11}
    discovered = {c.kwargs["args"][0] for c in dt.call_args_list}
    assert discovered == {10}  # threads only discovered under parent types
