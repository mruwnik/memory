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
from memory.common.db.models import (
    DiscordBot,
    DiscordBotServer,
    DiscordChannel,
    DiscordMessage,
    DiscordServer,
    DiscordUser,
    HumanUser,
)
from memory.discord.rest import BotAccess
from memory.workers.tasks import discord_backfill
from memory.workers.tasks.discord_backfill import (
    collectible_channels,
    resolve_channel_bot,
)

ACCESS = BotAccess(bot_id=42, token="tok", api_base_url="https://discord.com/api/v10", push_only=False)


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
    assert settings.DISCORD_BACKFILL_INTERVAL == 60 * 60 * 24
    assert settings.DISCORD_CATCH_UP_DAYS > 0
    assert settings.DISCORD_BACKFILL_JITTER_SECONDS >= 0
    assert settings.DISCORD_BACKFILL_MAX_MESSAGES_PER_RUN > 0


@contextmanager
def acquired_lock(*args, **kwargs):
    yield MagicMock()  # truthy => lock acquired


@contextmanager
def held_lock(*args, **kwargs):
    yield None  # None => someone else holds it


def make_bot_server_channel(db_session, *, token="bot-token", channel_id=10):
    bot = DiscordBot(id=42, application_id=42, name="bot", is_active=True)
    bot.token = token  # encrypts
    server = DiscordServer(id=1, name="srv", collect_messages=True, bot_id=42, verified=True)
    channel = DiscordChannel(id=channel_id, server_id=1, name="general",
                             channel_type="text")
    db_session.add_all([bot, server, channel])
    db_session.commit()
    return bot, server, channel


def test_resolve_channel_bot_happy_path(db_session):
    bot, _, _ = make_bot_server_channel(db_session, token="abc")
    bot.api_base_url = "https://chat.example.com/discord/api/v10"
    db_session.commit()

    with patch.object(settings, "CHAT_API_ALLOWED_ORIGINS", ["https://chat.example.com"]):
        access = resolve_channel_bot(10)

    assert access == BotAccess(
        bot_id=42, token="abc", api_base_url="https://chat.example.com/discord/api/v10", push_only=False
    )


@pytest.mark.parametrize("realtime, push_only", [("gateway", False), ("webhook", True)])
def test_resolve_channel_bot_marks_webhook_bots_push_only(db_session, realtime, push_only):
    bot, _, _ = make_bot_server_channel(db_session)
    bot.public_key = "ab" * 32
    bot.realtime = realtime
    db_session.commit()

    assert resolve_channel_bot(10).push_only is push_only


@pytest.mark.parametrize(
    "change", [{"is_active": False}, {"token": None}, {"api_base_url": "https://chat.example.com/api"}]
)
def test_resolve_channel_bot_none_without_a_usable_bot(db_session, change):
    bot, _, _ = make_bot_server_channel(db_session)
    for field, value in change.items():
        setattr(bot, field, value)
    db_session.commit()

    assert resolve_channel_bot(10) is None


def owned_bot(db_session, bot_id: int, owner: HumanUser, token: str | None = "tok") -> DiscordBot:
    bot = DiscordBot(id=bot_id, application_id=bot_id, name=f"bot {bot_id}", created_by_user_id=owner.id)
    bot.token = token
    db_session.add(bot)
    db_session.flush()
    return bot


def person(db_session, email: str) -> HumanUser:
    user = HumanUser(email=email, name=email, password_hash="x")
    db_session.add(user)
    db_session.flush()
    return user


def delivered(db_session, channel_id: int, bot_id: int, message_id: int) -> None:
    db_session.merge(DiscordUser(id=100, username="alice"))
    db_session.flush()
    db_session.add(DiscordMessage(
        modality="message", sha256=message_id.to_bytes(32, "big"), content="hi", message_id=message_id,
        channel_id=channel_id, author_id=100, bot_id=bot_id, sent_at="2024-01-01T00:00:00+00:00",
    ))
    db_session.flush()


@pytest.mark.parametrize("first_bot_token, expected", [("tok", 51), (None, 52)])
def test_resolve_channel_bot_for_a_dm_uses_only_its_owners_bots(db_session, first_bot_token, expected):
    owner, other = person(db_session, "owner@example.com"), person(db_session, "other@example.com")
    owned_bot(db_session, 51, owner, first_bot_token)
    owned_bot(db_session, 52, owner)
    owned_bot(db_session, 60, other)
    db_session.add(DiscordChannel(id=99, server_id=None, bot_id=51, name="dm", channel_type="dm"))
    db_session.flush()
    delivered(db_session, 99, 60, message_id=500)
    db_session.commit()

    assert resolve_channel_bot(99).bot_id == expected


@pytest.mark.parametrize("memberships", [[], [60]])
def test_a_push_delivery_never_chooses_the_bot(db_session, memberships):
    make_bot_server_channel(db_session)
    owned_bot(db_session, 60, person(db_session, "second@example.com"))
    delivered(db_session, 10, 60, message_id=500)
    db_session.add_all([DiscordBotServer(bot_id=b, server_id=1) for b in memberships])
    db_session.commit()

    assert resolve_channel_bot(10).bot_id == 42


def unusable_owner_with_a_second_bot(db_session) -> None:
    bot, _, _ = make_bot_server_channel(db_session)
    bot.is_active = False
    owned_bot(db_session, 60, person(db_session, "second@example.com"))
    delivered(db_session, 10, 60, message_id=500)


def test_a_verified_member_reads_when_the_owner_cannot(db_session):
    unusable_owner_with_a_second_bot(db_session)
    db_session.add(DiscordBotServer(bot_id=60, server_id=1))
    db_session.commit()

    assert resolve_channel_bot(10).bot_id == 60


def test_a_bot_that_only_pushed_never_reads(db_session):
    unusable_owner_with_a_second_bot(db_session)
    db_session.commit()

    assert resolve_channel_bot(10) is None


def test_resolve_channel_bot_none_for_an_empty_dm(db_session):
    db_session.add(DiscordChannel(id=99, server_id=None, name="dm",
                                  channel_type="dm"))
    db_session.commit()
    assert resolve_channel_bot(99) is None


def test_backfill_channel_skips_when_no_token(db_session):
    with patch.object(discord_backfill, "resolve_channel_bot",
                      return_value=None):
        result = discord_backfill.backfill_channel(10)
    assert result["status"] == "skipped"


def test_backfill_channel_skips_when_locked(db_session):
    with patch.object(discord_backfill, "resolve_channel_bot",
                      return_value=ACCESS), \
         patch.object(discord_backfill, "distributed_lock", held_lock):
        result = discord_backfill.backfill_channel(10)
    assert result["status"] == "locked"


def test_backfill_channel_redispatches_when_not_done(db_session):
    with patch.object(discord_backfill, "resolve_channel_bot",
                      return_value=ACCESS), \
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
    with patch.object(discord_backfill, "resolve_channel_bot",
                      return_value=ACCESS), \
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
    db_session.add(DiscordServer(id=1, name="s", collect_messages=server_collect))
    db_session.add(
        DiscordChannel(id=10, server_id=1, name="c",
                       channel_type=channel_type, collect_messages=channel_collect)
    )
    db_session.commit()

    ids = [cid for cid, _ in collectible_channels()]
    assert (10 in ids) is expected


def test_collectible_channels_excludes_serverless_by_default(db_session):
    db_session.add(DiscordChannel(id=20, server_id=None, name="dm",
                                  channel_type="dm", collect_messages=None))
    db_session.commit()
    assert 20 not in [cid for cid, _ in collectible_channels()]


def test_schedule_dispatches_backfill_per_channel_and_threads_for_parents(db_session):
    db_session.add(DiscordServer(id=1, name="s", collect_messages=True))
    db_session.add(DiscordChannel(id=10, server_id=1, name="text",
                                  channel_type="text"))
    db_session.add(DiscordChannel(id=11, server_id=1, name="thread",
                                  channel_type="thread"))
    db_session.commit()

    with patch.object(discord_backfill.backfill_channel, "apply_async") as bf, \
         patch.object(discord_backfill, "discover_servers"), \
         patch.object(discord_backfill.discover_threads, "apply_async") as dt, \
         patch.object(discord_backfill.settings, "DISCORD_BACKFILL_JITTER_SECONDS", 0):
        result = discord_backfill.schedule_discord_backfills()

    assert result["scheduled"] == 2
    backfilled = {c.kwargs["args"][0] for c in bf.call_args_list}
    assert backfilled == {10, 11}
    discovered = {c.kwargs["args"][0] for c in dt.call_args_list}
    assert discovered == {10}  # threads only discovered under parent types


def test_schedule_refreshes_servers_of_bots_with_tokens(db_session):
    make_bot_server_channel(db_session, token="abc")
    db_session.add(DiscordBot(id=43, application_id=43, name="tokenless", realtime="webhook", public_key="ab" * 32))
    db_session.commit()

    with patch.object(discord_backfill, "discover_bot_servers", new=AsyncMock(return_value=1)) as discover, \
         patch.object(discord_backfill.backfill_channel, "apply_async"), \
         patch.object(discord_backfill.discover_threads, "apply_async"):
        discord_backfill.schedule_discord_backfills()

    assert [c.args[0].bot_id for c in discover.call_args_list] == [42]


@pytest.mark.parametrize("error", [KeyError("id"), AttributeError("get")])
def test_a_malformed_server_listing_does_not_stop_the_sweep(db_session, error):
    make_bot_server_channel(db_session, token="abc")

    with patch.object(discord_backfill, "discover_bot_servers", new=AsyncMock(side_effect=error)), \
         patch.object(discord_backfill.backfill_channel, "apply_async") as bf, \
         patch.object(discord_backfill.discover_threads, "apply_async"):
        result = discord_backfill.schedule_discord_backfills()

    assert (result["scheduled"], bf.called) == (1, True)


def test_a_failing_server_refresh_does_not_stop_the_sweep(db_session):
    from memory.discord.rest import DiscordAPIError

    make_bot_server_channel(db_session, token="abc")

    with patch.object(discord_backfill, "discover_bot_servers", new=AsyncMock(side_effect=DiscordAPIError(500, None))), \
         patch.object(discord_backfill.backfill_channel, "apply_async") as bf, \
         patch.object(discord_backfill.discover_threads, "apply_async"):
        result = discord_backfill.schedule_discord_backfills()

    assert (result["scheduled"], bf.called) == (1, True)
