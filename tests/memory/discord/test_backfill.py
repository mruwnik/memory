from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from discord import MessageType

from memory.common.db.models import (
    DiscordBot,
    DiscordChannel,
    DiscordMessage,
    DiscordServer,
    DiscordUser,
)
from memory.discord import backfill
from memory.discord.backfill import oldest_stored_message_id


def make_discord_rows(db_session):
    """Minimal bot+server+channel+author so DiscordMessage FKs resolve and
    backfill's ensure_server(bot_id=...) has a valid FK target (mirrors prod:
    a backfilled server always has a registered bot)."""
    bot = DiscordBot(id=42, name="bot", is_active=True)
    server = DiscordServer(id=1, name="srv", collect_messages=True, bot_id=42)
    channel = DiscordChannel(id=10, server_id=1, name="general", channel_type="text")
    author = DiscordUser(id=100, username="alice")
    db_session.add_all([bot, server, channel, author])
    db_session.flush()
    return server, channel, author


def add_message(db_session, message_id, channel_id=10, author_id=100):
    msg = DiscordMessage(
        modality="message",
        sha256=bytes([message_id % 256]) * 32,
        content=f"m{message_id}",
        message_id=message_id,
        channel_id=channel_id,
        author_id=author_id,
        sent_at="2024-01-01T00:00:00+00:00",
    )
    db_session.add(msg)
    db_session.flush()
    return msg


def fake_history_message(mid):
    """A MagicMock that quacks like a discord.py Message, with CONCRETE string
    entity names matching the pre-created rows so ensure_* update paths are no-ops
    (avoids the MagicMock->Text-column adaptation crash)."""
    msg = MagicMock()
    msg.id = mid
    msg.guild = MagicMock(id=1)
    msg.guild.name = "srv"
    msg.guild.description = None
    msg.guild.member_count = None
    msg.channel = MagicMock(id=10)
    msg.channel.name = "general"
    msg.channel.category_id = None
    msg.author = MagicMock(id=100)
    msg.author.name = "alice"
    msg.author.display_name = "alice"
    msg.content = f"m{mid}"
    msg.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    msg.edited_at = None
    msg.reference = None
    msg.thread = None
    msg.type = MessageType.default
    msg.pinned = False
    msg.attachments = []
    msg.embeds = []
    return msg


def async_iter(items):
    async def _gen(*args, **kwargs):
        for item in items:
            yield item
    return _gen


def test_oldest_stored_message_id_returns_min(db_session):
    make_discord_rows(db_session)
    add_message(db_session, 300)
    add_message(db_session, 100)
    add_message(db_session, 200)
    db_session.commit()

    assert oldest_stored_message_id(10) == 100


def test_oldest_stored_message_id_none_when_empty(db_session):
    make_discord_rows(db_session)
    db_session.commit()
    assert oldest_stored_message_id(10) is None


@pytest.mark.asyncio
async def test_backfill_channel_messages_queues_each_and_reports_done(db_session):
    make_discord_rows(db_session)
    db_session.commit()

    messages = [fake_history_message(m) for m in (5, 4, 3)]
    fake_channel = MagicMock()
    fake_channel.history = async_iter(messages)

    fake_client = MagicMock()
    fake_client.login = AsyncMock()
    fake_client.close = AsyncMock()
    fake_client.fetch_channel = AsyncMock(return_value=fake_channel)

    celery_app = MagicMock()

    with patch("discord.Client", return_value=fake_client):
        result = await backfill.backfill_channel_messages(
            "tok", 10, bot_id=42, celery_app=celery_app, max_messages=100
        )

    # Newest-first crawl over (5, 4, 3): oldest fetched is the last yielded (3).
    assert result == {"processed": 3, "done": True, "oldest_message_id": 3}
    assert celery_app.send_task.call_count == 3
    fake_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_backfill_channel_messages_not_done_when_budget_filled(db_session):
    make_discord_rows(db_session)
    db_session.commit()

    messages = [fake_history_message(m) for m in (5, 4)]
    fake_channel = MagicMock()
    fake_channel.history = async_iter(messages)
    fake_client = MagicMock()
    fake_client.login = AsyncMock()
    fake_client.close = AsyncMock()
    fake_client.fetch_channel = AsyncMock(return_value=fake_channel)

    with patch("discord.Client", return_value=fake_client):
        result = await backfill.backfill_channel_messages(
            "tok", 10, bot_id=42, celery_app=MagicMock(), max_messages=2
        )

    # Budget filled (2 of 2); oldest fetched is the last yielded (4).
    assert result == {"processed": 2, "done": False, "oldest_message_id": 4}


@pytest.mark.asyncio
async def test_backfill_uses_min_message_id_as_before_cursor(db_session):
    make_discord_rows(db_session)
    add_message(db_session, 100)
    db_session.commit()

    captured = {}

    def history(*args, **kwargs):
        captured["before"] = kwargs.get("before")
        async def _gen():
            if False:
                yield None
        return _gen()

    fake_channel = MagicMock()
    fake_channel.history = history
    fake_client = MagicMock()
    fake_client.login = AsyncMock()
    fake_client.close = AsyncMock()
    fake_client.fetch_channel = AsyncMock(return_value=fake_channel)

    with patch("discord.Client", return_value=fake_client):
        await backfill.backfill_channel_messages(
            "tok", 10, bot_id=42, celery_app=MagicMock(), max_messages=100
        )

    assert isinstance(captured["before"], discord.Object)
    assert captured["before"].id == 100


@pytest.mark.asyncio
async def test_backfill_explicit_before_id_used_verbatim(db_session):
    """A continuation passes its cursor in before_id; it must be used directly,
    not overridden by MIN(stored). Here MIN=100 but before_id=50 wins."""
    make_discord_rows(db_session)
    add_message(db_session, 100)  # MIN(stored) is 100
    db_session.commit()

    captured = {}

    def history(*args, **kwargs):
        captured["before"] = kwargs.get("before")
        async def _gen():
            if False:
                yield None
        return _gen()

    fake_channel = MagicMock()
    fake_channel.history = history
    fake_client = MagicMock()
    fake_client.login = AsyncMock()
    fake_client.close = AsyncMock()
    fake_client.fetch_channel = AsyncMock(return_value=fake_channel)

    with patch("discord.Client", return_value=fake_client):
        result = await backfill.backfill_channel_messages(
            "tok", 10, bot_id=42, celery_app=MagicMock(), max_messages=100,
            before_id=50,
        )

    # Explicit cursor wins over MIN(stored)=100.
    assert isinstance(captured["before"], discord.Object)
    assert captured["before"].id == 50
    # Nothing fetched, so the continuation cursor is None.
    assert result == {"processed": 0, "done": True, "oldest_message_id": None}


@pytest.mark.asyncio
async def test_backfill_channel_messages_forum_channel_is_noop(db_session):
    """ForumChannel has no .history() — direct backfill must be a clean no-op.

    Uses spec= to restrict the mock's attributes so hasattr(forum, "history")
    is genuinely False, mirroring the real discord.py 2.3.2 ForumChannel which
    is not Messageable and has no history() method.
    """
    make_discord_rows(db_session)
    db_session.commit()

    # spec restricts the mock to only the listed attrs → hasattr(forum, "history") is False
    forum = MagicMock(spec=["id", "guild"])
    forum.id = 10

    fake_client = MagicMock()
    fake_client.login = AsyncMock()
    fake_client.close = AsyncMock()
    fake_client.fetch_channel = AsyncMock(return_value=forum)

    celery_app = MagicMock()

    with patch("discord.Client", return_value=fake_client):
        result = await backfill.backfill_channel_messages(
            "tok", 10, bot_id=42, celery_app=celery_app, max_messages=100
        )

    # No messages processed — graceful no-op, no continuation dispatched.
    assert result == {"processed": 0, "done": True, "oldest_message_id": None}
    celery_app.send_task.assert_not_called()
    fake_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_discover_channel_threads_ensures_rows_and_dispatches(db_session):
    make_discord_rows(db_session)  # bot 42, server 1, channel 10, author 100
    db_session.commit()

    def fake_thread(tid):
        th = MagicMock(spec=discord.Thread)
        th.id = tid
        th.parent_id = 10
        th.guild = MagicMock(id=1)
        th.name = f"thread-{tid}"
        # discord.Thread.category_id is a real property (parent's category), so
        # spec=Thread leaves it as a MagicMock unless set; real threads return
        # int|None. Set None so ensure_channel writes a real value, not a Mock.
        th.category_id = None
        return th

    active_thread = fake_thread(201)
    archived_thread = fake_thread(202)

    fake_channel = MagicMock()
    fake_channel.guild = MagicMock()
    fake_channel.guild.active_threads = AsyncMock(return_value=[active_thread])
    fake_channel.archived_threads = async_iter([archived_thread])

    fake_client = MagicMock()
    fake_client.login = AsyncMock()
    fake_client.close = AsyncMock()
    fake_client.fetch_channel = AsyncMock(return_value=fake_channel)

    celery_app = MagicMock()

    with patch("discord.Client", return_value=fake_client):
        thread_ids = await backfill.discover_channel_threads(
            "tok", 10, celery_app=celery_app
        )

    assert set(thread_ids) == {201, 202}
    # Thread rows persisted so backfill_channel can resolve their bot/token.
    assert db_session.get(DiscordChannel, 201) is not None
    assert db_session.get(DiscordChannel, 202) is not None
    dispatched = {c.kwargs["args"][0] for c in celery_app.send_task.call_args_list}
    assert dispatched == {201, 202}


@pytest.mark.asyncio
async def test_discover_channel_threads_swallows_private_forbidden(db_session):
    """If the private-archived enumeration raises discord.Forbidden (missing
    Manage Threads), the public/active threads are STILL ensured + dispatched."""
    make_discord_rows(db_session)
    db_session.commit()

    def fake_thread(tid):
        th = MagicMock(spec=discord.Thread)
        th.id = tid
        th.parent_id = 10
        th.guild = MagicMock(id=1)
        th.name = f"thread-{tid}"
        th.category_id = None
        return th

    active_thread = fake_thread(201)
    public_archived = fake_thread(202)

    def archived_threads(*args, **kwargs):
        if kwargs.get("private"):
            response = MagicMock(status=403, reason="Forbidden")
            raise discord.Forbidden(response, "missing Manage Threads")
        return async_iter([public_archived])()

    fake_channel = MagicMock()
    fake_channel.guild = MagicMock()
    fake_channel.guild.active_threads = AsyncMock(return_value=[active_thread])
    fake_channel.archived_threads = archived_threads

    fake_client = MagicMock()
    fake_client.login = AsyncMock()
    fake_client.close = AsyncMock()
    fake_client.fetch_channel = AsyncMock(return_value=fake_channel)

    celery_app = MagicMock()

    with patch("discord.Client", return_value=fake_client):
        thread_ids = await backfill.discover_channel_threads(
            "tok", 10, celery_app=celery_app
        )

    # Private call raised Forbidden (swallowed); active + public archived remain.
    assert set(thread_ids) == {201, 202}
    assert db_session.get(DiscordChannel, 201) is not None
    assert db_session.get(DiscordChannel, 202) is not None
    dispatched = {c.kwargs["args"][0] for c in celery_app.send_task.call_args_list}
    assert dispatched == {201, 202}
    fake_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_backfill_channel_messages_forbidden_is_skip(db_session):
    """A channel the bot can't read (archived / not a member) is a clean skip,
    not a crash — the weekly sweep must not fail on inaccessible channels."""
    make_discord_rows(db_session)
    db_session.commit()

    fake_client = MagicMock()
    fake_client.login = AsyncMock()
    fake_client.close = AsyncMock()
    fake_client.fetch_channel = AsyncMock(
        side_effect=discord.Forbidden(
            MagicMock(status=403, reason="Forbidden"), "Missing Access"
        )
    )
    celery_app = MagicMock()

    with patch("discord.Client", return_value=fake_client):
        result = await backfill.backfill_channel_messages(
            "tok", 10, bot_id=42, celery_app=celery_app, max_messages=100
        )

    assert result == {"processed": 0, "done": True, "oldest_message_id": None}
    celery_app.send_task.assert_not_called()
    fake_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_discover_channel_threads_forbidden_channel_is_skip(db_session):
    """If the bot can't access the parent channel at all, discovery skips
    cleanly (returns no threads, dispatches nothing) instead of raising."""
    make_discord_rows(db_session)
    db_session.commit()

    fake_client = MagicMock()
    fake_client.login = AsyncMock()
    fake_client.close = AsyncMock()
    fake_client.fetch_channel = AsyncMock(
        side_effect=discord.Forbidden(
            MagicMock(status=403, reason="Forbidden"), "Missing Access"
        )
    )
    celery_app = MagicMock()

    with patch("discord.Client", return_value=fake_client):
        thread_ids = await backfill.discover_channel_threads(
            "tok", 10, celery_app=celery_app
        )

    assert thread_ids == []
    celery_app.send_task.assert_not_called()
    fake_client.close.assert_awaited_once()
