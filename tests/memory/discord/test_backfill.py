from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from memory.common.celery_app import ADD_DISCORD_MESSAGE, BACKFILL_DISCORD_CHANNEL, DELETE_DISCORD_MESSAGES
from memory.common.db.models import (
    DiscordBot,
    DiscordBotServer,
    DiscordChannel,
    DiscordMessage,
    DiscordServer,
    DiscordUser,
)
from memory.discord import backfill, rest
from memory.discord.backfill import snowflake_at
from memory.discord.rest import BotAccess
from memory.workers.tasks.discord import add_discord_message

BASE = "https://chat.example.com/discord/api/v10"
ACCESS = BotAccess(bot_id=42, token="tok", api_base_url=BASE, push_only=True)


def make_discord_rows(db_session):
    bot = DiscordBot(id=42, application_id=42, name="bot", is_active=True)
    server = DiscordServer(id=1, name="srv", collect_messages=True, bot_id=42)
    channel = DiscordChannel(id=10, server_id=1, name="general", channel_type="text")
    author = DiscordUser(id=100, username="alice")
    db_session.add_all([bot, server, channel, author])
    db_session.flush()
    return server, channel, author


def add_message(db_session, message_id, channel_id=10, author_id=100):
    msg = DiscordMessage(
        modality="message",
        sha256=message_id.to_bytes(32, "big"),
        content=f"m{message_id}",
        message_id=message_id,
        channel_id=channel_id,
        author_id=author_id,
        sent_at="2024-01-01T00:00:00+00:00",
    )
    db_session.add(msg)
    db_session.flush()
    return msg


def api_message(message_id: int) -> dict:
    return {
        "id": str(message_id),
        "channel_id": "10",
        "author": {"id": "100", "username": "alice"},
        "content": f"m{message_id}",
        "timestamp": "2024-01-01T00:00:00+00:00",
        "type": 0,
    }


class FakeDiscord:
    """Serves a channel's messages like Discord: newest first, ``before``/``limit``."""

    def __init__(self, message_ids=(), routes=None, page_size=100, first_replies=()):
        """``first_replies`` answer the first requests: responses, or exceptions to raise."""
        self.message_ids = sorted(message_ids, reverse=True)
        self.routes = routes or {}
        self.page_size = page_size
        self.first_replies = list(first_replies)
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.first_replies:
            reply = self.first_replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        path = request.url.path.removeprefix("/discord/api/v10")
        if path in self.routes:
            status, body = self.routes[path]
            return httpx.Response(status, json=body)
        if path == "/channels/10/messages":
            before = request.url.params.get("before")
            ids = [m for m in self.message_ids if before is None or m < int(before)]
            return httpx.Response(200, json=[api_message(m) for m in ids[: self.page_size]])
        return httpx.Response(404, json={"code": 10003, "message": "Unknown Channel"})

    def client(self, **kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler), **kwargs)


@pytest.fixture
def fake_api():
    def install(fake: FakeDiscord):
        return patch.object(rest, "public_http_client", side_effect=fake.client)

    with patch.object(rest.asyncio, "sleep", new=AsyncMock()):
        yield install


def queued_ids(celery_app) -> list[int]:
    return [
        c.kwargs["kwargs"]["message_id"]
        for c in celery_app.send_task.call_args_list
        if c.args[0] == ADD_DISCORD_MESSAGE
    ]


@pytest.mark.asyncio
async def test_first_backfill_queues_everything_with_the_bots_token_and_url(db_session, fake_api):
    make_discord_rows(db_session)
    db_session.commit()
    fake = FakeDiscord([5, 4, 3])
    celery_app = MagicMock()

    with fake_api(fake):
        result = await backfill.backfill_channel_messages(ACCESS, 10, celery_app, max_messages=100)

    assert result == {"processed": 3, "done": True, "oldest_message_id": 3}
    assert queued_ids(celery_app) == [5, 4, 3]
    assert {(r.url.host, r.headers["authorization"]) for r in fake.requests} == {
        ("chat.example.com", "Bot tok")
    }


@pytest.mark.asyncio
async def test_backfill_pages_until_the_budget(db_session, fake_api):
    make_discord_rows(db_session)
    db_session.commit()
    fake = FakeDiscord(range(1, 8), page_size=3)

    with patch.object(rest, "PAGE_SIZE", 3), patch.object(backfill, "PAGE_SIZE", 3), fake_api(fake):
        result = await backfill.backfill_channel_messages(ACCESS, 10, MagicMock(), max_messages=5)

    assert result == {"processed": 5, "done": False, "oldest_message_id": 3}


@pytest.mark.asyncio
async def test_catch_up_and_history_have_separate_budgets(db_session, fake_api):
    make_discord_rows(db_session)
    now = datetime.now(timezone.utc)
    stored = snowflake_at(now - timedelta(hours=5))
    add_message(db_session, stored)
    db_session.commit()
    newer = [snowflake_at(now - timedelta(minutes=m)) for m in (1, 2, 3)]
    older = [stored - 1000, stored - 2000, stored - 3000]
    celery_app = MagicMock()

    with fake_api(FakeDiscord([*newer, stored, *older])):
        result = await backfill.backfill_channel_messages(ACCESS, 10, celery_app, max_messages=3)

    assert queued_ids(celery_app) == [*newer, *older]
    assert result["oldest_message_id"] == older[-1]


@pytest.mark.asyncio
async def test_continuation_uses_its_cursor(db_session, fake_api):
    make_discord_rows(db_session)
    add_message(db_session, 100)
    db_session.commit()
    fake = FakeDiscord([120, 60, 40])
    celery_app = MagicMock()

    with fake_api(fake):
        result = await backfill.backfill_channel_messages(ACCESS, 10, celery_app, max_messages=100, before_id=50)

    assert queued_ids(celery_app) == [40]
    assert result["done"] is True


@pytest.mark.asyncio
async def test_catch_up_fills_recent_gaps_then_crawls_older_history(db_session, fake_api):
    """Stored: 2 days ago is the window. Messages newer than the newest stored
    one, and ones missing inside the window, are re-read; then history continues
    below the oldest stored message."""
    make_discord_rows(db_session)
    now = datetime.now(timezone.utc)
    window = snowflake_at(now - timedelta(days=2))
    recent_stored = snowflake_at(now - timedelta(hours=5))
    recent_missed = snowflake_at(now - timedelta(hours=10))
    newest = snowflake_at(now - timedelta(hours=1))
    old_stored = window - 1000
    older = window - 2000
    add_message(db_session, recent_stored)
    add_message(db_session, old_stored)
    db_session.commit()
    fake = FakeDiscord([newest, recent_stored, recent_missed, old_stored, older])
    celery_app = MagicMock()

    with fake_api(fake):
        await backfill.backfill_channel_messages(ACCESS, 10, celery_app, max_messages=100)

    assert queued_ids(celery_app) == [newest, recent_stored, recent_missed, older]
    queued = [c.kwargs["kwargs"] for c in sent(celery_app, ADD_DISCORD_MESSAGE)]
    assert [k["is_edit"] for k in queued] == [True, True, True, False]
    # Only history below what is stored is a backfill; the catch-up is what push missed.
    assert [k["backfill"] for k in queued] == [False, False, False, True]


@pytest.mark.parametrize(
    "collect, recent_only",
    [(None, True), (True, False)],
    ids=["collected by default: the catch-up window only", "switched on: full history"],
)
@pytest.mark.asyncio
async def test_a_dms_history_is_crawled_only_once_it_is_switched_on(db_session, fake_api, collect, recent_only):
    db_session.add_all([
        DiscordBot(id=42, application_id=42, name="bot", is_active=True),
        DiscordChannel(id=10, name="DM with alice", channel_type="dm", bot_id=42, collect_messages=collect),
        DiscordUser(id=100, username="alice"),
    ])
    db_session.commit()
    now = datetime.now(timezone.utc)
    recent, old = snowflake_at(now - timedelta(hours=5)), snowflake_at(now - timedelta(days=30))
    celery_app = MagicMock()

    with fake_api(FakeDiscord([recent, old])):
        result = await backfill.backfill_channel_messages(ACCESS, 10, celery_app, max_messages=100, catch_up_days=2)

    assert (queued_ids(celery_app) == [recent], result["done"]) == (recent_only, True)


def sent(celery_app, task: str) -> list:
    return [c for c in celery_app.send_task.call_args_list if c.args[0] == task]


@pytest.mark.asyncio
async def test_catch_up_deletes_messages_gone_from_a_fully_read_window(db_session, fake_api):
    make_discord_rows(db_session)
    now = datetime.now(timezone.utc)
    window = snowflake_at(now - timedelta(days=2))
    kept, deleted = snowflake_at(now - timedelta(hours=5)), snowflake_at(now - timedelta(hours=6))
    outside = window - 1000
    for message_id in (kept, deleted, outside):
        add_message(db_session, message_id)
    db_session.commit()
    celery_app = MagicMock()

    with fake_api(FakeDiscord([kept])):
        await backfill.backfill_channel_messages(ACCESS, 10, celery_app, max_messages=100)

    assert [c.kwargs["kwargs"] for c in sent(celery_app, DELETE_DISCORD_MESSAGES)] == [
        {"message_ids": [deleted], "bot_ids": [42]}
    ]


@pytest.mark.asyncio
async def test_a_partly_read_window_deletes_nothing(db_session, fake_api):
    make_discord_rows(db_session)
    now = datetime.now(timezone.utc)
    recent = [snowflake_at(now - timedelta(hours=h)) for h in (1, 2, 3)]
    add_message(db_session, snowflake_at(now - timedelta(hours=10)))
    db_session.commit()
    celery_app = MagicMock()

    with fake_api(FakeDiscord(recent)):
        await backfill.backfill_channel_messages(ACCESS, 10, celery_app, max_messages=2)

    assert sent(celery_app, DELETE_DISCORD_MESSAGES) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, code",
    [(403, 50001), (404, 10003), (403, None)],
)
async def test_unreadable_channels_are_skipped(db_session, fake_api, status, code):
    make_discord_rows(db_session)
    db_session.commit()
    fake = FakeDiscord(routes={"/channels/10/messages": (status, {"code": code})})
    celery_app = MagicMock()

    with fake_api(fake):
        result = await backfill.backfill_channel_messages(ACCESS, 10, celery_app, max_messages=100)

    assert result == {"processed": 0, "done": True, "oldest_message_id": None}
    celery_app.send_task.assert_not_called()


@pytest.mark.asyncio
async def test_server_errors_propagate(db_session, fake_api):
    make_discord_rows(db_session)
    db_session.commit()
    fake = FakeDiscord(routes={"/channels/10/messages": (500, {"code": 0})})

    with fake_api(fake), pytest.raises(rest.DiscordAPIError):
        await backfill.backfill_channel_messages(ACCESS, 10, MagicMock(), max_messages=100)


RATE_LIMITED = httpx.Response(429, json={"retry_after": 0.5})
UNAVAILABLE = httpx.Response(503, json={})
DROPPED = httpx.ConnectError("connection reset")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first_replies",
    [[RATE_LIMITED, RATE_LIMITED], [UNAVAILABLE], [DROPPED], [UNAVAILABLE, DROPPED]],
)
async def test_transient_failures_are_retried(db_session, fake_api, first_replies):
    make_discord_rows(db_session)
    db_session.commit()
    celery_app = MagicMock()

    with fake_api(FakeDiscord([5], first_replies=first_replies)):
        result = await backfill.backfill_channel_messages(ACCESS, 10, celery_app, max_messages=100)

    assert result["processed"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first_replies",
    [
        [httpx.Response(429, json={"retry_after": 3600})],
        [RATE_LIMITED] * (rest.MAX_RETRIES + 1),
    ],
)
async def test_long_or_endless_rate_limits_skip_the_run(db_session, fake_api, first_replies):
    make_discord_rows(db_session)
    db_session.commit()
    celery_app = MagicMock()

    with fake_api(FakeDiscord([5], first_replies=first_replies)):
        result = await backfill.backfill_channel_messages(ACCESS, 10, celery_app, max_messages=100)

    assert result == {"processed": 0, "done": True, "oldest_message_id": None}
    celery_app.send_task.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first_replies",
    [
        [UNAVAILABLE] * (rest.MAX_RETRIES + 1),
        [DROPPED] * (rest.MAX_RETRIES + 1),
        [httpx.Response(200, text="<html>not json</html>")],
    ],
)
async def test_persistent_failures_raise_one_error_type(db_session, fake_api, first_replies):
    make_discord_rows(db_session)
    db_session.commit()

    with fake_api(FakeDiscord([5], first_replies=first_replies)), pytest.raises(rest.DiscordAPIError):
        await backfill.backfill_channel_messages(ACCESS, 10, MagicMock(), max_messages=100)


@pytest.mark.asyncio
async def test_forum_channels_are_not_read_directly(db_session, fake_api):
    """A forum has no messages of its own; its posts are threads, crawled separately."""
    make_discord_rows(db_session)
    db_session.get(DiscordChannel, 10).channel_type = "forum"
    db_session.commit()
    fake = FakeDiscord([5])

    with fake_api(fake):
        result = await backfill.backfill_channel_messages(ACCESS, 10, MagicMock(), max_messages=100)

    assert (result["processed"], fake.requests) == (0, [])


THREAD = {"parent_id": "10", "guild_id": "1", "type": 11}


@pytest.mark.asyncio
async def test_discover_channel_threads_ensures_rows_and_dispatches(db_session, fake_api):
    make_discord_rows(db_session)
    db_session.commit()
    fake = FakeDiscord(
        routes={
            "/guilds/1/threads/active": (200, {"threads": [THREAD | {"id": "201", "name": "a"}, THREAD | {"id": "300", "parent_id": "99"}]}),
            "/channels/10/threads/archived/public": (200, {"threads": [
                THREAD | {"id": "202", "name": "b"}, THREAD | {"id": "203", "guild_id": "2"},
            ], "has_more": False}),
            "/channels/10/threads/archived/private": (403, {"code": 50001}),
        }
    )
    celery_app = MagicMock()

    with fake_api(fake):
        thread_ids = await backfill.discover_channel_threads(ACCESS, 10, celery_app)

    assert set(thread_ids) == {201, 202}
    db_session.expire_all()
    assert db_session.get(DiscordChannel, 203) is None
    assert (db_session.get(DiscordChannel, 201).name, db_session.get(DiscordChannel, 202).channel_type) == ("a", "thread")
    assert {db_session.get(DiscordChannel, t).parent_id for t in (201, 202)} == {10}
    assert {(c.args[0], c.kwargs["args"][0]) for c in celery_app.send_task.call_args_list} == {
        (BACKFILL_DISCORD_CHANNEL, 201),
        (BACKFILL_DISCORD_CHANNEL, 202),
    }


def test_a_crawl_never_files_its_channel_back_under_the_server_it_started_in(db_session):
    make_discord_rows(db_session)
    db_session.add(DiscordServer(id=2, name="moved to", collect_messages=True, bot_id=42))
    db_session.flush()
    db_session.get(DiscordChannel, 10).server_id = 2
    db_session.commit()

    backfill.queue_messages(ACCESS, [api_message(5)], 1, MagicMock(), is_edit=False)

    db_session.expire_all()
    assert db_session.get(DiscordChannel, 10).server_id == 2


def test_a_message_crawled_before_its_channel_moved_is_filed_under_the_server_it_moved_to(db_session, qdrant):
    make_discord_rows(db_session)
    db_session.add(DiscordServer(id=2, name="moved to", collect_messages=True, bot_id=42))
    db_session.commit()
    celery_app = MagicMock()
    backfill.queue_messages(ACCESS, [api_message(5) | {"content": "a message long enough to be embedded"}], 1, celery_app, is_edit=False)
    db_session.get(DiscordChannel, 10).server_id = 2
    db_session.commit()

    add_discord_message(**celery_app.send_task.call_args.kwargs["kwargs"])

    db_session.expire_all()
    assert db_session.query(DiscordMessage).filter_by(message_id=5).one().server_id == 2


@pytest.mark.asyncio
async def test_discover_channel_threads_unreadable_channel_is_skipped(db_session, fake_api):
    make_discord_rows(db_session)
    db_session.commit()
    celery_app = MagicMock()

    with fake_api(FakeDiscord(routes={"/guilds/1/threads/active": (403, {"code": 50001})})):
        assert await backfill.discover_channel_threads(ACCESS, 10, celery_app) == []
    celery_app.send_task.assert_not_called()


@pytest.mark.asyncio
async def test_discover_bot_servers_stores_servers_and_text_channels(db_session, fake_api):
    db_session.add(DiscordBot(id=42, application_id=42, name="bot"))
    db_session.commit()
    fake = FakeDiscord(
        routes={
            "/users/@me/guilds": (200, [{"id": "7", "name": "Guild"}]),
            "/guilds/7/channels": (
                200,
                [
                    {"id": "70", "name": "general", "type": 0, "parent_id": "79"},
                    {"id": "71", "name": "forum", "type": 15},
                    {"id": "79", "name": "Category", "type": 4},
                ],
            ),
        }
    )

    with fake_api(fake):
        assert await backfill.discover_bot_servers(ACCESS) == 1

    db_session.expire_all()
    server = db_session.get(DiscordServer, 7)
    general = db_session.get(DiscordChannel, 70)
    assert (server.name, server.bot_id, server.collect_messages) == ("Guild", 42, True)
    assert (general.name, general.channel_type, general.category_id) == ("general", "text", 79)
    assert db_session.get(DiscordChannel, 71).channel_type == "forum"
    assert db_session.get(DiscordChannel, 79) is None
    assert db_session.get(DiscordBot, 42).last_sync_at is not None


@pytest.mark.asyncio
async def test_discovery_claims_ownerless_servers_but_never_another_bots(db_session, fake_api):
    db_session.add_all([
        DiscordBot(id=42, application_id=42, name="bot"),
        DiscordBot(id=43, application_id=43, name="other"),
    ])
    db_session.flush()
    db_session.add_all([
        DiscordServer(id=7, name="Theirs", bot_id=43, verified=True),
        DiscordServer(id=8, name="Orphan", verified=True),
    ])
    db_session.flush()
    db_session.add(DiscordChannel(id=70, server_id=7, name="general", channel_type="text"))
    db_session.commit()
    fake = FakeDiscord(
        routes={
            "/users/@me/guilds": (200, [{"id": "7", "name": "Renamed"}, {"id": "8", "name": "Mine now"}]),
            "/guilds/7/channels": (200, [{"id": "70", "name": "renamed", "type": 0}]),
            "/guilds/8/channels": (200, []),
        }
    )

    with fake_api(fake):
        await backfill.discover_bot_servers(ACCESS)

    db_session.expire_all()
    assert (db_session.get(DiscordServer, 7).name, db_session.get(DiscordServer, 7).bot_id) == ("Theirs", 43)
    assert (db_session.get(DiscordServer, 8).name, db_session.get(DiscordServer, 8).bot_id) == ("Mine now", 42)
    assert db_session.get(DiscordChannel, 70).name == "general"


@pytest.mark.asyncio
async def test_discovery_takes_over_a_server_only_pushes_have_claimed(db_session, fake_api):
    db_session.add_all([
        DiscordBot(id=42, application_id=42, name="bot"),
        DiscordBot(id=43, application_id=43, name="pusher", public_key="ab" * 32),
    ])
    db_session.flush()
    db_session.add(DiscordServer(id=7, name="Squatted", bot_id=43, verified=False, sensitivity="public"))
    db_session.commit()
    fake = FakeDiscord(routes={"/users/@me/guilds": (200, [{"id": "7", "name": "Real"}]), "/guilds/7/channels": (200, [])})

    with fake_api(fake):
        await backfill.discover_bot_servers(ACCESS)

    db_session.expire_all()
    server = db_session.get(DiscordServer, 7)
    assert (server.name, server.bot_id, server.verified, server.project_id, server.sensitivity) == (
        "Real", 42, True, None, "basic",
    )


@pytest.mark.asyncio
async def test_discovery_records_membership_of_servers_others_own(db_session, fake_api):
    db_session.add_all([DiscordBot(id=42, application_id=42, name="bot"), DiscordBot(id=43, application_id=43, name="owner")])
    db_session.flush()
    db_session.add(DiscordServer(id=7, name="Theirs", bot_id=43, verified=True))
    db_session.commit()
    fake = FakeDiscord(routes={"/users/@me/guilds": (200, [{"id": "7", "name": "x"}]), "/guilds/7/channels": (200, [])})

    with fake_api(fake):
        await backfill.discover_bot_servers(ACCESS)

    members = db_session.query(DiscordBotServer.bot_id).filter_by(server_id=7).all()
    assert [m[0] for m in members] == [42]


@pytest.mark.parametrize(
    "first_replies",
    [[httpx.Response(500)], [httpx.Response(429, json={"retry_after": 1})], [httpx.ConnectTimeout("slow")]],
    ids=["server error", "rate limited", "timeout"],
)
@pytest.mark.asyncio
async def test_reading_a_channel_for_a_push_is_one_short_try(fake_api, first_replies):
    fake = FakeDiscord(first_replies=first_replies)

    with fake_api(fake):
        confirmed = await rest.token_channel(ACCESS, 10)

    assert (confirmed, len(fake.requests), fake.requests[0].extensions["timeout"]["read"]) == (None, 1, 5.0)
