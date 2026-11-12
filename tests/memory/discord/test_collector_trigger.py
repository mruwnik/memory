import asyncio
import threading
from time import monotonic, sleep
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from discord import DMChannel, MessageType
from fastapi import HTTPException
from sqlalchemy import text

from memory.common.celery_app import ADD_DISCORD_MESSAGE, BACKFILL_DISCORD_CHANNEL, HANDLE_DISCORD_EVENT
from memory.common.db.models import (
    DiscordBot,
    DiscordBotServer,
    DiscordChannel,
    DiscordMessage,
    DiscordServer,
    DiscordUser,
    HumanUser,
    Project,
)
from memory.common.db.models.discord import DEFAULT_DISCORD_API_BASE_URL
from memory.discord import api as collector_api
from memory.discord import collector as collector_module
from memory.discord.collector import BotInfo, CollectorManager, MessageCollector
from memory.discord.ingest import ensure_user
from memory.workers.tasks.discord import add_discord_message


def fake_guild_channel(channel_id=10, guild_id=1):
    ch = MagicMock()
    ch.id = channel_id
    ch.name = "general"
    ch.guild = MagicMock(id=guild_id)
    ch.category_id = None
    ch.type = None  # get_channel_type -> "unknown" (string), avoids MagicMock->Text crash
    ch.send = MagicMock()  # marks it as messageable (hasattr "send")
    return ch


@pytest.mark.asyncio
async def test_new_collectible_channel_triggers_backfill(db_session):
    db_session.add(DiscordServer(id=1, name="s", collect_messages=True))
    db_session.commit()

    celery_app = MagicMock()
    collector = MessageCollector(BotInfo(id=42, name="bot", token="t"), celery_app)

    await collector.on_guild_channel_create(fake_guild_channel())

    calls = [c for c in celery_app.send_task.call_args_list
             if c.args and c.args[0] == BACKFILL_DISCORD_CHANNEL]
    assert len(calls) == 1
    assert calls[0].kwargs["args"] == [10]


@pytest.mark.asyncio
async def test_new_non_collectible_channel_does_not_trigger_backfill(db_session):
    db_session.add(DiscordServer(id=1, name="s", collect_messages=False))
    db_session.commit()

    celery_app = MagicMock()
    collector = MessageCollector(BotInfo(id=42, name="bot", token="t"), celery_app)

    await collector.on_guild_channel_create(fake_guild_channel())

    calls = [c for c in celery_app.send_task.call_args_list
             if c.args and c.args[0] == BACKFILL_DISCORD_CHANNEL]
    assert calls == []


def collector_with(celery_app=None, **info) -> MessageCollector:
    return MessageCollector(BotInfo(id=42, name="bot", token="t", **info), celery_app or MagicMock())


def handled_events(celery_app) -> list[dict]:
    return [c.kwargs["kwargs"] for c in celery_app.send_task.call_args_list if c.args[0] == HANDLE_DISCORD_EVENT]


@pytest.mark.parametrize(
    "event, payload, expected",
    [
        ("on_raw_message_delete", SimpleNamespace(message_id=5), ("MESSAGE_DELETE", {"id": "5"})),
        ("on_raw_bulk_message_delete", SimpleNamespace(message_ids={6, 5}), ("MESSAGE_DELETE_BULK", {"ids": ["5", "6"]})),
        (
            "on_raw_message_edit",
            SimpleNamespace(message_id=5, data={"id": "5", "content": "new"}),
            ("MESSAGE_UPDATE", {"id": "5", "content": "new"}),
        ),
        (
            "on_raw_reaction_add",
            SimpleNamespace(message_id=5, user_id=7, emoji=SimpleNamespace(id=None, name="🔥")),
            ("MESSAGE_REACTION_ADD", {"message_id": "5", "user_id": "7", "emoji": {"id": None, "name": "🔥"}}),
        ),
        (
            "on_raw_reaction_remove",
            SimpleNamespace(message_id=5, user_id=7, emoji=SimpleNamespace(id=9, name="party")),
            ("MESSAGE_REACTION_REMOVE", {"message_id": "5", "user_id": "7", "emoji": {"id": "9", "name": "party"}}),
        ),
        (
            "on_raw_reaction_clear_emoji",
            SimpleNamespace(message_id=5, emoji=SimpleNamespace(id=None, name="🔥")),
            ("MESSAGE_REACTION_REMOVE_EMOJI", {"message_id": "5", "emoji": {"id": None, "name": "🔥"}}),
        ),
        ("on_raw_reaction_clear", SimpleNamespace(message_id=5), ("MESSAGE_REACTION_REMOVE_ALL", {"message_id": "5"})),
    ],
)
@pytest.mark.asyncio
async def test_edits_deletes_and_reactions_go_through_the_owner_scoped_event_task(event, payload, expected):
    celery_app = MagicMock()

    await getattr(collector_with(celery_app), event)(payload)

    event_type, data = expected
    assert handled_events(celery_app) == [{"bot_id": 42, "event_type": event_type, "data": data, "pushed": False}]


def fake_guild(guild_id=1, name="Guild") -> Any:
    return SimpleNamespace(id=guild_id, name=name, description=None, member_count=3, channels=[], members=[])


@pytest.mark.parametrize("event", ["on_guild_join", "on_guild_available"])
@pytest.mark.asyncio
async def test_a_server_joined_after_ready_is_recorded_as_the_bots(db_session, event):
    db_session.add(DiscordBot(id=42, application_id=42, name="bot"))
    db_session.commit()

    await getattr(collector_with(), event)(fake_guild())

    db_session.expire_all()
    server = db_session.get(DiscordServer, 1)
    assert (server.bot_id, server.verified) == (42, True)
    assert db_session.get(DiscordBotServer, (42, 1)) is not None


@pytest.mark.parametrize(
    "owner, other_members, new_owner",
    [(42, [], None), (42, [44], None), (42, [44, 43], 43), (44, [44], 44)],
)
@pytest.mark.asyncio
async def test_leaving_a_server_hands_ownership_only_to_the_same_owners_bot(
    db_session, admin_user, regular_user, owner, other_members, new_owner
):
    db_session.add_all(
        [
            DiscordBot(id=42, application_id=42, name="a", created_by_user_id=admin_user.id),
            DiscordBot(id=43, application_id=43, name="b", created_by_user_id=admin_user.id),
            DiscordBot(id=44, application_id=44, name="c", created_by_user_id=regular_user.id),
        ]
    )
    db_session.add(DiscordServer(id=1, name="Guild", bot_id=owner))
    db_session.flush()
    db_session.add_all([DiscordBotServer(bot_id=b, server_id=1) for b in [42, *other_members]])
    db_session.commit()

    await collector_with().on_guild_remove(fake_guild())

    db_session.expire_all()
    assert db_session.get(DiscordBotServer, (42, 1)) is None
    assert db_session.get(DiscordServer, 1).bot_id == new_owner


@pytest.mark.asyncio
async def test_another_owners_bot_claiming_a_left_server_starts_with_default_access(db_session, admin_user, regular_user):
    project = Project(title="Discord project", state="open")
    db_session.add_all(
        [
            project,
            DiscordBot(id=42, application_id=42, name="a", created_by_user_id=admin_user.id),
            DiscordBot(id=44, application_id=44, name="c", created_by_user_id=regular_user.id),
        ]
    )
    db_session.flush()
    db_session.add(DiscordServer(id=1, name="Guild", bot_id=42, project_id=project.id, sensitivity="confidential"))
    db_session.flush()
    db_session.add_all([DiscordBotServer(bot_id=b, server_id=1) for b in [42, 44]])
    db_session.commit()

    await collector_with().on_guild_remove(fake_guild())
    await MessageCollector(BotInfo(id=44, name="c", token="t"), MagicMock()).on_guild_available(fake_guild())

    db_session.expire_all()
    server = db_session.get_one(DiscordServer, 1)
    assert (server.bot_id, server.project_id, server.sensitivity) == (44, None, "basic")


@pytest.mark.transactional_db
@pytest.mark.asyncio
async def test_bots_syncing_servers_with_shared_members_at_once_all_succeed(db_session):
    bot_ids = range(42, 47)
    db_session.add_all([DiscordBot(id=b, application_id=b, name=f"b{b}") for b in bot_ids])
    db_session.commit()
    member = SimpleNamespace(id=7, name="alice", display_name="Alice")
    collectors = [MessageCollector(BotInfo(id=b, name=f"b{b}", token="t"), MagicMock()) for b in bot_ids]
    guilds: list[Any] = [SimpleNamespace(**{**vars(fake_guild(guild_id=b % 2)), "members": [member]}) for b in bot_ids]

    await asyncio.gather(*(c.sync_guilds([g]) for c, g in zip(collectors, guilds)))

    db_session.expire_all()
    assert db_session.query(DiscordUser).count() == 1
    assert {(m.bot_id, m.server_id) for m in db_session.query(DiscordBotServer)} == {(b, b % 2) for b in bot_ids}


@pytest.mark.transactional_db
def test_a_message_from_a_member_an_unfinished_sync_inserted_waits_for_it_then_succeeds(db_session):
    db_session.add(DiscordBot(id=42, application_id=42, name="bot"))
    db_session.add(DiscordServer(id=1, name="Guild", bot_id=42, verified=True, collect_messages=True))
    db_session.commit()
    author: Any = SimpleNamespace(id=7, name="alice", display_name="Alice")
    message = MagicMock(
        id=99, guild=fake_guild(), author=author, content="hi", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        edited_at=None, reference=None, thread=None, type=MessageType.default, pinned=False, attachments=[], embeds=[],
    )
    message.channel = SimpleNamespace(id=10, name="general", category_id=None)
    celery_app = MagicMock()
    collector = collector_with(celery_app)
    errors = []

    def handle_message():
        try:
            asyncio.run(collector._process_message(message))
        except Exception as e:
            errors.append(e)

    with collector_module.make_session() as sync:
        ensure_user(sync, author)
        sync.flush()
        handler = threading.Thread(target=handle_message)
        handler.start()
        wait_for_a_blocked_query(db_session)
        sync.commit()
    handler.join(timeout=10)

    assert errors == []
    assert [c.kwargs["kwargs"]["message_id"] for c in celery_app.send_task.call_args_list] == [99]


def wait_for_a_blocked_query(session, timeout=10.0):
    deadline = monotonic() + timeout
    query = text("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' AND datname = current_database()")
    while not session.execute(query).scalar():
        assert monotonic() < deadline, "no query blocked"
        session.rollback()
        sleep(0.05)


@pytest.mark.asyncio
async def test_syncing_servers_runs_off_the_event_loop_and_records_the_sync(db_session):
    db_session.add(DiscordBot(id=42, application_id=42, name="bot"))
    db_session.commit()
    collector = collector_with()
    sessions_on = []
    real_make_session = collector_module.make_session

    def recording_make_session():
        sessions_on.append(threading.get_ident())
        return real_make_session()

    with (
        patch.object(MessageCollector, "guilds", new=[fake_guild()]),
        patch.object(collector_module, "make_session", recording_make_session),
    ):
        await collector.sync_guild_metadata()

    assert sessions_on and threading.get_ident() not in sessions_on
    db_session.expire_all()
    assert db_session.get(DiscordBotServer, (42, 1)) is not None
    assert db_session.get(DiscordBot, 42).last_sync_at is not None


@pytest.mark.asyncio
async def test_a_refresh_syncs_only_the_bot_asked_for(db_session):
    db_session.add_all([DiscordBot(id=42, application_id=42, name="a"), DiscordBot(id=43, application_id=43, name="b")])
    db_session.commit()
    manager = CollectorManager(MagicMock())
    manager.collectors = {42: collector_with(), 43: MessageCollector(BotInfo(id=43, name="b", token="t"), MagicMock())}

    with (
        patch.object(collector_api, "manager", manager),
        patch.object(MessageCollector, "guilds", new=[fake_guild()]),
    ):
        await collector_api.refresh_metadata(42)
        with pytest.raises(HTTPException):
            await collector_api.refresh_metadata(44)

    db_session.expire_all()
    assert [m.bot_id for m in db_session.query(DiscordBotServer).all()] == [42]
    assert db_session.get(DiscordServer, 1).verified


@pytest.mark.parametrize(
    "api_base_url, chunks",
    [(DEFAULT_DISCORD_API_BASE_URL, True), ("https://chat.example.com/discord/api/v10", False)],
)
def test_only_discord_itself_is_asked_for_member_chunks(api_base_url, chunks):
    assert collector_with(api_base_url=api_base_url)._connection._chunk_guilds is chunks


def fake_message():
    channel = MagicMock(id=333, category_id=None)
    channel.name = "tester"
    guild = MagicMock(id=222, description=None, member_count=10)
    guild.name = "Test Server"
    author = MagicMock(id=444, display_name="Alice")
    author.name = "alice"
    return MagicMock(
        id=111, guild=guild, channel=channel, author=author, content="hello", reference=None, thread=None,
        type=MessageType.default, pinned=False, attachments=[], embeds=[], edited_at=None,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize("server_collect, queued", [(False, False), (True, True)])
@pytest.mark.asyncio
async def test_a_paused_server_stops_collection_for_channels_that_inherit(db_session, server_collect, queued):
    db_session.add(DiscordBot(id=42, application_id=42, name="bot"))
    db_session.add(DiscordServer(id=222, name="Test Server", bot_id=42, collect_messages=server_collect))
    db_session.flush()
    db_session.add(DiscordChannel(id=333, server_id=222, name="tester", channel_type="text"))
    db_session.commit()
    celery_app = MagicMock()

    await collector_with(celery_app).on_message(fake_message())

    sent = [c.args[0] for c in celery_app.send_task.call_args_list]
    assert (ADD_DISCORD_MESSAGE in sent) is queued


def fake_dm(mutual_guilds=("a guild",), author_id=444):
    """A DM as discord.py builds it from MESSAGE_CREATE: its recipients unknown."""
    message = fake_message()
    message.guild = None
    state: Any = SimpleNamespace(user=SimpleNamespace(id=42))
    message.channel = DMChannel._from_message(state, 555)
    message.author.id = author_id
    message.author.mutual_guilds = list(mutual_guilds)
    return message


@pytest.mark.parametrize("stored_collect, queued", [(None, True), (False, False)])
@pytest.mark.asyncio
async def test_a_dm_to_a_gateway_bot_is_collected_unless_switched_off(db_session, stored_collect, queued):
    db_session.add(DiscordBot(id=42, application_id=42, name="bot"))
    db_session.flush()
    db_session.add(DiscordChannel(id=555, name="DM with alice", channel_type="dm", bot_id=42, collect_messages=stored_collect))
    db_session.commit()
    celery_app = MagicMock()

    await collector_with(celery_app).on_message(fake_dm())

    sent = [c.args[0] for c in celery_app.send_task.call_args_list]
    assert (ADD_DISCORD_MESSAGE in sent) is queued


@pytest.mark.asyncio
async def test_a_first_dm_from_someone_sharing_a_server_with_the_bot_stores_its_channel(db_session):
    db_session.add(DiscordBot(id=42, application_id=42, name="bot"))
    db_session.commit()
    celery_app = MagicMock()

    await collector_with(celery_app).on_message(fake_dm())

    db_session.expire_all()
    channel = db_session.get(DiscordChannel, 555)
    assert (channel.name, channel.channel_type, channel.bot_id, channel.should_collect) == ("DM with alice", "dm", 42, True)
    assert ADD_DISCORD_MESSAGE in [c.args[0] for c in celery_app.send_task.call_args_list]


def a_post_by_alice(db_session, bot_server: int) -> None:
    """Alice posted in server 1; the bot is in ``bot_server``."""
    db_session.add_all([
        DiscordBot(id=42, application_id=42, name="bot"),
        DiscordServer(id=1, name="posted in"),
        DiscordServer(id=2, name="other"),
        DiscordUser(id=444, username="alice"),
    ])
    db_session.flush()
    db_session.add_all([
        DiscordBotServer(bot_id=42, server_id=bot_server),
        DiscordChannel(id=10, server_id=1, name="general", channel_type="text"),
    ])
    db_session.flush()
    db_session.add(DiscordMessage(
        modality="message", sha256=b"\x02" * 32, content="hi", message_id=33, channel_id=10,
        server_id=1, author_id=444, sent_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    ))
    db_session.commit()


@pytest.mark.parametrize("bot_server, stored", [(1, True), (2, False)])
@pytest.mark.asyncio
async def test_a_first_dm_is_collected_only_from_someone_who_posted_in_the_bots_servers(db_session, bot_server, stored):
    """A proxied gateway caches no members, so posts are what show who shares a server."""
    a_post_by_alice(db_session, bot_server)
    celery_app = MagicMock()

    await collector_with(celery_app).on_message(fake_dm(mutual_guilds=()))

    db_session.expire_all()
    assert (db_session.get(DiscordChannel, 555) is not None) is stored
    assert (ADD_DISCORD_MESSAGE in [c.args[0] for c in celery_app.send_task.call_args_list]) is stored


@pytest.mark.asyncio
async def test_a_first_dm_reaches_the_worker_as_a_dm_named_after_its_sender(db_session, qdrant):
    db_session.add(DiscordBot(id=42, application_id=42, name="bot"))
    db_session.commit()
    celery_app = MagicMock()

    await collector_with(celery_app).on_message(fake_dm())
    add_discord_message(**celery_app.send_task.call_args.kwargs["kwargs"])

    db_session.expire_all()
    stored = db_session.query(DiscordMessage).filter_by(message_id=111).one()
    assert (stored.channel_id, stored.channel.name, stored.channel.channel_type, stored.server_id) == (
        555, "DM with alice", "dm", None,
    )


@pytest.mark.parametrize("recipient_mutual_guilds, stored", [(["a guild"], True), ([], False)])
@pytest.mark.asyncio
async def test_the_bots_own_first_dm_asks_discord_whom_it_is_with(db_session, recipient_mutual_guilds, stored):
    db_session.add(DiscordBot(id=42, application_id=42, name="bot"))
    db_session.commit()
    celery_app = MagicMock()
    alice = SimpleNamespace(id=444, name="alice", mutual_guilds=recipient_mutual_guilds)
    fetch = AsyncMock(return_value=SimpleNamespace(recipients=[alice]))

    with patch.object(MessageCollector, "fetch_channel", fetch):
        await collector_with(celery_app).on_message(fake_dm(author_id=42))

    db_session.expire_all()
    fetch.assert_awaited_once_with(555)
    assert [c.name for c in db_session.query(DiscordChannel).filter_by(id=555)] == ["DM with alice"] * stored
    assert (ADD_DISCORD_MESSAGE in [c.args[0] for c in celery_app.send_task.call_args_list]) is stored


@pytest.mark.asyncio
async def test_the_bots_own_message_in_a_stored_dm_asks_discord_nothing(db_session):
    db_session.add(DiscordBot(id=42, application_id=42, name="bot"))
    db_session.flush()
    db_session.add(DiscordChannel(id=555, name="DM with alice", channel_type="dm", bot_id=42))
    db_session.commit()
    celery_app = MagicMock()
    fetch = AsyncMock()

    with patch.object(MessageCollector, "fetch_channel", fetch):
        await collector_with(celery_app).on_message(fake_dm(author_id=42))

    fetch.assert_not_awaited()
    assert db_session.get(DiscordChannel, 555).name == "DM with alice"
    assert ADD_DISCORD_MESSAGE in [c.args[0] for c in celery_app.send_task.call_args_list]


@pytest.mark.asyncio
async def test_a_strangers_dm_switched_on_is_collected(db_session):
    db_session.add(DiscordBot(id=42, application_id=42, name="bot"))
    db_session.flush()
    db_session.add(DiscordChannel(id=555, name="DM", channel_type="dm", bot_id=42, collect_messages=True))
    db_session.commit()
    celery_app = MagicMock()

    await collector_with(celery_app).on_message(fake_dm(mutual_guilds=()))

    assert ADD_DISCORD_MESSAGE in [c.args[0] for c in celery_app.send_task.call_args_list]


def a_dm_held_by_another_owner(db_session, **stored) -> None:
    other = HumanUser(email="o@example.com", name="O", password_hash="x")
    db_session.add(other)
    db_session.flush()
    project = Project(title="Theirs", state="open")
    db_session.add_all([
        DiscordBot(id=42, application_id=42, name="bot"),
        DiscordBot(id=43, application_id=43, name="theirs", created_by_user_id=other.id),
        DiscordServer(id=222, name="Test Server", verified=False),
        project,
    ])
    db_session.flush()
    db_session.add(DiscordChannel(**{
        "id": 555, "name": "taken", "channel_type": "dm", "collect_messages": True, "project_id": project.id,
        "bot_id": 43, **stored,
    }))
    db_session.commit()


@pytest.mark.asyncio
async def test_the_gateway_takes_back_its_dm_stored_as_a_servers_channel_with_default_access(db_session):
    a_dm_held_by_another_owner(db_session, server_id=222, channel_type="text")
    celery_app = MagicMock()

    await collector_with(celery_app).on_message(fake_dm())

    db_session.expire_all()
    channel = db_session.get(DiscordChannel, 555)
    assert (channel.server_id, channel.channel_type, channel.bot_id, channel.project_id, channel.collect_messages) == (
        None, "dm", 42, None, None,
    )
    assert [c.kwargs["kwargs"]["bot_id"] for c in celery_app.send_task.call_args_list if c.args[0] == ADD_DISCORD_MESSAGE] == [42]


@pytest.mark.asyncio
async def test_the_gateway_leaves_a_dm_another_owner_holds_alone(db_session):
    """Two owners can register the same application: the DM stays with the first."""
    a_dm_held_by_another_owner(db_session)
    celery_app = MagicMock()

    await collector_with(celery_app).on_message(fake_dm())

    db_session.expire_all()
    channel = db_session.get(DiscordChannel, 555)
    assert (channel.bot_id, channel.collect_messages, channel.project_id is None) == (43, True, False)
    assert ADD_DISCORD_MESSAGE not in [c.args[0] for c in celery_app.send_task.call_args_list]


@pytest.mark.asyncio
async def test_a_gateway_message_in_a_server_corrects_a_channel_stored_as_a_dm(db_session):
    db_session.add_all([DiscordBot(id=42, application_id=42, name="bot"), DiscordBot(id=43, application_id=43, name="squatter")])
    db_session.add(DiscordServer(id=222, name="Test Server", bot_id=42, collect_messages=True, verified=True))
    db_session.flush()
    db_session.add(DiscordChannel(id=555, name="squat", channel_type="dm", bot_id=43))
    db_session.commit()
    message = fake_message()
    message.channel = SimpleNamespace(id=555, name="general", category_id=None, type=SimpleNamespace(name="text"))

    await collector_with(MagicMock()).on_message(message)

    db_session.expire_all()
    channel = db_session.get(DiscordChannel, 555)
    assert (channel.server_id, channel.channel_type, channel.name, channel.content_owner_id) == (222, "text", "general", None)
