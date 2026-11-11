"""Gateway event payloads (as carried by Discord Webhook Events) stored like collected messages."""

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import cast
from unittest.mock import ANY, patch

import httpx
import pytest
from celery import Task
from celery.exceptions import Retry

from memory.common.db.models import (
    DiscordBot,
    DiscordBotServer,
    DiscordChannel,
    DiscordMessage,
    DiscordServer,
    DiscordUser,
    Event,
    HumanUser,
    Project,
)
from memory.api.discord import user_can_manage_channel
from memory.common.access_control import user_can_access
from memory.common.celery_app import UPDATE_SOURCE_ACCESS_CONTROL, VERIFY_DISCORD_MESSAGES
from memory.common.db.connection import make_session
from memory.common.db.models.events import record_raw_event
from memory.common import discord_attachments, discord_events, rate_limit, settings
from memory.common.discord_events import ensure_listed_channel, message_task_kwargs
from memory.common.reactions import apply_reaction
from memory.discord import rest
from memory.workers.tasks import discord
from tests.conftest import item_events

GUILD = "900000000000000001"
CHANNEL = "900000000000000002"
AUTHOR = "900000000000000003"
MESSAGE = "900000000000000004"
HOOKED_TOKEN, SECOND_TOKEN, ATTACKER_TOKEN = "tok-hooked", "tok-second", "tok-attacker"
THREAD = "900000000000000042"
LONG_TEXT = "a message long enough to be embedded by the pipeline"


def listing(channel_id, guild_id=GUILD, **extra) -> dict:
    """GET /channels/<id>, as Discord answers it."""
    return {"id": str(channel_id), "guild_id": str(guild_id), "type": 0, "name": f"channel {channel_id}"} | extra


def discord_error(status, code) -> httpx.Response:
    return httpx.Response(status, json={"code": code})


UNKNOWN_CHANNEL, UNKNOWN_MESSAGE = discord_error(404, 10003), discord_error(404, 10008)


class FakeDiscord:
    """Discord's REST API as each bot's token sees it.

    ``channels[(token, path)]`` is what GET /channels/<path> returns to that token: a body, an error response
    (None: an unknown channel), or an exception the connection fails with. A token reads the messages of each channel it reads as Discord holds them now:
    ``messages[channel id][message id]``, whose reactions are the users in ``reactors[(channel id, message id)]``.
    """

    def __init__(self, channels: dict[tuple[str, str], dict | httpx.Response | Exception | None]):
        self.channels = channels
        self.messages: dict[str, dict[str, dict]] = {}
        self.reactors: dict[tuple[str, str], dict[str, list[str]]] = {}
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        token = request.headers["authorization"].removeprefix("Bot ")
        path = request.url.path.removeprefix("/api/v10/channels/")
        if (token, path) in self.channels or "/" not in path:
            answer = self.channels.get((token, path)) or UNKNOWN_CHANNEL
        else:
            answer = self.read(token, path, request.url.params)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, httpx.Response) else httpx.Response(200, json=answer)

    def read(self, token: str, path: str, params: httpx.QueryParams) -> dict | list | httpx.Response:
        """GET /channels/<channel>/messages/<message>[/reactions/<emoji>]."""
        channel_id, _, message_id, *reaction = path.split("/")
        if not isinstance(self.channels.get((token, channel_id)), dict):
            return UNKNOWN_CHANNEL
        message = self.messages.get(channel_id, {}).get(message_id)
        if message is None:
            return UNKNOWN_MESSAGE
        reactors = self.reactors.get((channel_id, message_id), {})
        if not reaction:
            return message | {"reactions": [{"emoji": emoji_of(e), "count": len(users)} for e, users in reactors.items()]}
        after, limit = int(params.get("after", 0)), int(params.get("limit", 25))
        return [{"id": user} for user in reactors.get(reaction[-1], []) if int(user) > after][:limit]

    def post(self, data: dict) -> None:
        """Discord now holds this message, or this change to it. A message read by REST names no server."""
        held = self.messages.setdefault(data["channel_id"], {})
        held[data["id"]] = held.get(data["id"], {}) | {k: v for k, v in data.items() if k != "guild_id"}

    def happen(self, event_type: str, data: dict) -> None:
        """Make what an event reports true on Discord's side."""
        if event_type in ("MESSAGE_CREATE", "MESSAGE_UPDATE"):
            self.post(data)
        for message_id in [data["id"]] if event_type == "MESSAGE_DELETE" else data.get("ids", []):
            self.messages.get(data["channel_id"], {}).pop(message_id, None)
        if event_type in discord.REACTION_EVENTS:
            key = (data["channel_id"], data["message_id"])
            action = discord.REACTION_EVENTS[event_type]
            self.reactors[key] = apply_reaction(
                self.reactors.get(key), action, discord_events.emoji_key(data.get("emoji")), data.get("user_id"),
            )


def emoji_of(key: str) -> dict:
    name, _, emoji_id = key.partition(":")
    return {"name": name, "id": emoji_id or None}


@pytest.fixture(autouse=True)
def discord_api():
    """The two integrations of application 777 read the channel and threads these tests name in GUILD; the
    attacker reads nothing."""
    seen = [listing(CHANNEL), listing(THREAD, type=11, parent_id=CHANNEL), listing("42", type=11, parent_id=CHANNEL, name="a thread")]
    fake = FakeDiscord({(token, channel["id"]): channel for token in (HOOKED_TOKEN, SECOND_TOKEN) for channel in seen})
    transport = httpx.MockTransport(fake.handle)
    with patch.object(rest, "public_http_client", side_effect=lambda **kw: httpx.AsyncClient(transport=transport, **kw)):
        yield fake


@pytest.fixture
def webhook_bot(db_session):
    bot = DiscordBot(application_id=777, name="Hooked", realtime="webhook", public_key="ab" * 32, token=HOOKED_TOKEN)
    db_session.add(bot)
    db_session.commit()
    return bot


@pytest.fixture
def user_owned_bot(db_session):
    """A second person's integration of the same application."""
    owner = HumanUser(email="second@example.com", name="Second", password_hash="x")
    db_session.add(owner)
    db_session.flush()
    bot = DiscordBot(application_id=777, name="Theirs", realtime="webhook", public_key="cd" * 32, created_by_user_id=owner.id, token=SECOND_TOKEN)
    db_session.add(bot)
    db_session.commit()
    return bot


def message_create(**overrides) -> dict:
    return {
        "id": MESSAGE,
        "channel_id": CHANNEL,
        "guild_id": GUILD,
        "author": {"id": AUTHOR, "username": "alice", "global_name": "Alice"},
        "content": LONG_TEXT,
        "timestamp": "2026-11-05T12:00:00.000000+00:00",
        "edited_timestamp": None,
        "pinned": False,
        "type": 0,
        "attachments": [],
        "embeds": [],
    } | overrides


def handle(bot, event_type, data, pushed=True):
    return discord.handle_discord_event(bot_id=bot.id, event_type=event_type, data=data, pushed=pushed)


def relay(api: FakeDiscord, bot, event_type, data):
    """An event as an honest bot pushes it: what it reports has happened on Discord."""
    api.happen(event_type, data)
    return handle(bot, event_type, data)


def stored(db_session) -> DiscordMessage:
    db_session.expire_all()
    return db_session.query(DiscordMessage).filter_by(message_id=int(MESSAGE)).one()


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({}, {"message_type": "default", "reply_to_message_id": None}),
        (
            {"type": 19, "message_reference": {"message_id": "5"}},
            {"message_type": "reply", "reply_to_message_id": 5},
        ),
        ({"type": 7}, {"message_type": "system"}),
        ({"thread": {"id": "6"}}, {"message_type": "thread_starter", "thread_id": 6}),
        ({"pinned": True, "edited_timestamp": "2026-11-05T13:00:00+00:00"},
         {"is_pinned": True, "edited_at": "2026-11-05T13:00:00+00:00"}),
    ],
)
def test_message_task_kwargs(overrides, expected):
    kwargs = message_task_kwargs(message_create(**overrides), bot_id=3)

    assert {k: kwargs[k] for k in expected} == expected
    assert (kwargs["bot_id"], kwargs["message_id"], kwargs["channel_id"], kwargs["author_id"]) == (
        3, int(MESSAGE), int(CHANNEL), int(AUTHOR),
    )


def test_message_task_kwargs_downloads_images_and_records_every_attachment():
    kwargs = message_task_kwargs(
        message_create(
            attachments=[
                {"filename": "a.png", "content_type": "image/png", "size": 1, "url": "https://cdn.example.com/a.png?ex=1&hm=sig"},
                {"filename": "b.pdf", "content_type": "application/pdf", "size": 2, "url": "https://cdn.example.com/b.pdf"},
            ]
        ),
        bot_id=3,
    )

    assert (kwargs["images"], kwargs["attachments"]) == (
        ["https://cdn.example.com/a.png?ex=1&hm=sig"],
        [
            {"filename": "a.png", "content_type": "image/png", "size": 1, "url": "https://cdn.example.com/a.png"},
            {"filename": "b.pdf", "content_type": "application/pdf", "size": 2, "url": "https://cdn.example.com/b.pdf"},
        ],
    )


def test_message_create_stores_message_with_placeholder_server_and_listed_channel(
    db_session, discord_api, webhook_bot, qdrant
):
    result = relay(discord_api, webhook_bot, "MESSAGE_CREATE", message_create())

    assert result["status"] == "processed"
    message = stored(db_session)
    assert (message.content, message.bot_id) == (LONG_TEXT, webhook_bot.id)
    server = db_session.get(DiscordServer, int(GUILD))
    channel = db_session.get(DiscordChannel, int(CHANNEL))
    assert (server.name, server.bot_id, server.collect_messages) == (f"Server {GUILD}", webhook_bot.id, True)
    assert (channel.name, channel.channel_type) == (f"channel {CHANNEL}", "text")
    assert db_session.get(DiscordUser, int(AUTHOR)).display_name == "Alice"


def test_a_server_switched_off_is_not_stored(db_session, discord_api, webhook_bot, qdrant):
    db_session.add(DiscordServer(id=int(GUILD), name="Guild", collect_messages=False, bot_id=webhook_bot.id))
    db_session.commit()

    result = relay(discord_api, webhook_bot, "MESSAGE_CREATE", message_create())

    assert result["status"] == "skipped"
    assert db_session.query(DiscordMessage).count() == 0


def test_message_create_is_idempotent(db_session, discord_api, webhook_bot, qdrant):
    relay(discord_api, webhook_bot, "MESSAGE_CREATE", message_create())

    result = relay(discord_api, webhook_bot, "MESSAGE_CREATE", message_create())

    assert result["status"] == "already_exists"
    assert db_session.query(DiscordMessage).count() == 1


EDITED = "the message after its author edited it, still long"
EDITED_AT = datetime(2026, 11, 5, 13, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "update",
    [
        {"id": MESSAGE, "channel_id": CHANNEL, "content": EDITED, "edited_timestamp": "2026-11-05T13:00:00+00:00"},
        message_create(content=EDITED, edited_timestamp="2026-11-05T13:00:00+00:00"),
    ],
)
def test_message_update_with_content_edits(db_session, discord_api, webhook_bot, update, qdrant):
    relay(discord_api, webhook_bot, "MESSAGE_CREATE", message_create())

    relay(discord_api, webhook_bot, "MESSAGE_UPDATE", update)

    message = stored(db_session)
    assert (message.content, message.edited_at) == (EDITED, EDITED_AT)


def test_message_update_without_content_changes_nothing(db_session, discord_api, webhook_bot, qdrant):
    relay(discord_api, webhook_bot, "MESSAGE_CREATE", message_create())

    result = relay(discord_api, webhook_bot, "MESSAGE_UPDATE", {"id": MESSAGE, "channel_id": CHANNEL, "embeds": []})

    assert result["status"] == "ignored"
    assert stored(db_session).content == LONG_TEXT


def test_an_older_edit_arriving_late_is_ignored(db_session, discord_api, webhook_bot, qdrant):
    relay(discord_api, webhook_bot, "MESSAGE_CREATE", message_create())
    relay(discord_api, webhook_bot, "MESSAGE_UPDATE", message_create(content="newest edit of this message text", edited_timestamp="2026-11-05T14:00:00+00:00"))

    handle(webhook_bot, "MESSAGE_UPDATE", message_create(content="older edit of this message text", edited_timestamp="2026-11-05T13:00:00+00:00"))

    assert stored(db_session).content == "newest edit of this message text"


def test_an_edit_for_an_unseen_message_stores_it(db_session, discord_api, webhook_bot, qdrant):
    relay(discord_api, webhook_bot, "MESSAGE_UPDATE", message_create(content=EDITED, edited_timestamp="2026-11-05T13:00:00+00:00"))

    assert stored(db_session).content == EDITED


@pytest.mark.parametrize(
    "event_type, data",
    [
        ("MESSAGE_DELETE", {"id": MESSAGE, "channel_id": CHANNEL}),
        ("MESSAGE_DELETE_BULK", {"ids": [MESSAGE, "1"], "channel_id": CHANNEL}),
    ],
)
def test_deletes_remove_messages(db_session, discord_api, webhook_bot, event_type, data, qdrant):
    relay(discord_api, webhook_bot, "MESSAGE_CREATE", message_create())

    relay(discord_api, webhook_bot, event_type, data)
    relay(discord_api, webhook_bot, event_type, data)

    assert db_session.query(DiscordMessage).count() == 0


REACTION = {"user_id": "11", "channel_id": CHANNEL, "message_id": MESSAGE, "emoji": {"id": None, "name": "🔥"}}


@pytest.mark.parametrize(
    "events, expected",
    [
        ([("MESSAGE_REACTION_ADD", REACTION)], {"🔥": ["11"]}),
        ([("MESSAGE_REACTION_ADD", REACTION), ("MESSAGE_REACTION_ADD", REACTION)], {"🔥": ["11"]}),
        ([("MESSAGE_REACTION_ADD", REACTION), ("MESSAGE_REACTION_REMOVE", REACTION)], {}),
        (
            [
                ("MESSAGE_REACTION_ADD", REACTION),
                ("MESSAGE_REACTION_ADD", REACTION | {"user_id": "12", "emoji": {"id": "5", "name": "party"}}),
                ("MESSAGE_REACTION_REMOVE_EMOJI", {"channel_id": CHANNEL, "message_id": MESSAGE, "emoji": {"name": "🔥"}}),
            ],
            {"party:5": ["12"]},
        ),
        (
            [
                ("MESSAGE_REACTION_ADD", REACTION),
                ("MESSAGE_REACTION_REMOVE_ALL", {"channel_id": CHANNEL, "message_id": MESSAGE}),
            ],
            {},
        ),
    ],
)
def test_reaction_events(db_session, discord_api, webhook_bot, events, expected, qdrant):
    relay(discord_api, webhook_bot, "MESSAGE_CREATE", message_create())

    for event_type, data in events:
        relay(discord_api, webhook_bot, event_type, data)

    assert stored(db_session).reactions == expected


@pytest.mark.parametrize("event_type", ["THREAD_CREATE", "THREAD_UPDATE"])
def test_threads_become_channels(db_session, webhook_bot, event_type):
    handle(webhook_bot, event_type, {"id": "42", "guild_id": GUILD, "parent_id": CHANNEL, "name": "a thread", "type": 11})

    db_session.expire_all()
    thread = db_session.get(DiscordChannel, 42)
    assert (thread.name, thread.channel_type, thread.server_id, thread.parent_id) == (
        "a thread", "thread", int(GUILD), int(CHANNEL)
    )


def test_a_thread_stored_without_its_parent_gets_it_from_a_later_thread_event(db_session, webhook_bot):
    db_session.add(DiscordServer(id=int(GUILD), name="Guild", bot_id=webhook_bot.id, verified=True))
    db_session.commit()
    handle(webhook_bot, "THREAD_CREATE", {"id": "42", "guild_id": GUILD, "name": "a thread", "type": 11}, pushed=False)

    handle(webhook_bot, "THREAD_UPDATE", {"id": "42", "guild_id": GUILD, "parent_id": CHANNEL, "type": 11})

    db_session.expire_all()
    assert db_session.get(DiscordChannel, 42).parent_id == int(CHANNEL)


def thread_create(bot):
    handle(bot, "THREAD_CREATE", {"id": THREAD, "guild_id": GUILD, "parent_id": CHANNEL, "type": 11}, pushed=False)


def thread_listed(bot):
    with make_session() as session:
        ensure_listed_channel(session, {"id": THREAD, "parent_id": CHANNEL, "type": 11}, int(GUILD), bot.id, trusted=True)
        session.commit()


# A thread's first messages can arrive through the gateway before anything names its parent.
@pytest.mark.parametrize("learn_parent", [thread_create, thread_listed])
@pytest.mark.parametrize(
    "parent_collects, kept, removed, raw",
    [
        (False, 0, 1, [("not_collected", None), ("stored", THREAD)]),
        (True, 1, 0, [("stored", THREAD), ("stored", THREAD)]),
        (None, 1, 0, [("stored", THREAD), ("stored", THREAD)]),
    ],
)
def test_a_thread_found_under_a_parent_not_collected_drops_what_it_stored(
    db_session, webhook_bot, user_owned_bot, qdrant, learn_parent, parent_collects, kept, removed, raw
):
    db_session.add(DiscordServer(id=int(GUILD), name="Guild", bot_id=webhook_bot.id, collect_messages=True))
    db_session.flush()
    db_session.add(DiscordChannel(id=int(CHANNEL), server_id=int(GUILD), name="parent", channel_type="text",
                                  collect_messages=parent_collects))
    db_session.commit()
    handle(webhook_bot, "MESSAGE_CREATE", message_create(channel_id=THREAD), pushed=False)
    message_id = stored(db_session).id
    # The thread's deliveries to this bot, and one to another bot that never learns the parent.
    for bot in (webhook_bot, user_owned_bot):
        record_raw_event(
            db_session, source="discord", event_type="MESSAGE_CREATE", account_id=bot.id,
            owner_id=None, delivery_id="d1", outcome="stored", container=THREAD,
        )
    db_session.commit()

    learn_parent(webhook_bot)

    db_session.expire_all()
    assert db_session.query(DiscordMessage).count() == kept
    assert [e for e in item_events(db_session) if e[1] == "removed"] == [(message_id, "removed", None)] * removed
    # The deliveries that told this bot about the thread are as untracked as the thread.
    events = db_session.query(Event).filter_by(kind="raw").order_by(Event.account_id == user_owned_bot.id).all()
    assert [(e.outcome, e.container) for e in events] == raw


def test_thread_update_renames(db_session, webhook_bot):
    db_session.add(DiscordServer(id=int(GUILD), name="Guild", bot_id=webhook_bot.id, verified=True))
    db_session.commit()
    handle(webhook_bot, "THREAD_CREATE", {"id": "42", "guild_id": GUILD, "name": "old", "type": 11})

    handle(webhook_bot, "THREAD_UPDATE", {"id": "42", "guild_id": GUILD, "name": "new", "type": 11})

    db_session.expire_all()
    assert db_session.get(DiscordChannel, 42).name == "new"


def test_a_message_two_integrations_see_is_stored_once(db_session, discord_api, webhook_bot, user_owned_bot, qdrant):
    """Each person's integration delivers what they can see, so shared messages arrive twice, and the second
    delivery of an edit can arrive after a newer one."""
    newer = message_create(content="the later edit of this message", edited_timestamp="2026-11-05T14:00:00+00:00")
    older = message_create(content="the earlier edit of this message", edited_timestamp="2026-11-05T13:00:00+00:00")
    deliveries = [
        (webhook_bot, "MESSAGE_CREATE", message_create()),
        (user_owned_bot, "MESSAGE_CREATE", message_create()),
        (webhook_bot, "MESSAGE_UPDATE", newer),
        (user_owned_bot, "MESSAGE_UPDATE", older),
        (webhook_bot, "MESSAGE_REACTION_ADD", REACTION),
        (user_owned_bot, "MESSAGE_REACTION_ADD", REACTION),
        (webhook_bot, "MESSAGE_REACTION_ADD", REACTION | {"user_id": "12"}),
        (webhook_bot, "MESSAGE_REACTION_REMOVE", REACTION | {"user_id": "12"}),
        (user_owned_bot, "MESSAGE_REACTION_REMOVE", REACTION | {"user_id": "12"}),
    ]

    for bot, event_type, data in deliveries:
        if data is not older:
            discord_api.happen(event_type, data)
        handle(bot, event_type, data)

    message = stored(db_session)
    assert db_session.query(DiscordMessage).count() == 1
    assert (message.content, message.reactions) == ("the later edit of this message", {"🔥": ["11"]})


def test_events_for_a_deleted_bot_are_dropped(db_session):
    with patch.object(discord, "add_discord_message") as add:
        result = discord.handle_discord_event(bot_id=424242, event_type="MESSAGE_CREATE", data=message_create())

    assert result["status"] == "skipped"
    add.assert_not_called()



# --- What a push may change ------------------------------------------------
# A bot's signing key only proves its owner sent the event, so pushes may only
# touch rows that owner's bots delivered or own.


@pytest.fixture
def attacker_bot(db_session):
    attacker = HumanUser(email="attacker@example.com", name="Attacker", password_hash="x")
    db_session.add(attacker)
    db_session.flush()
    bot = DiscordBot(application_id=666, name="Forger", realtime="webhook", public_key="ef" * 32, created_by_user_id=attacker.id, token=ATTACKER_TOKEN)
    db_session.add(bot)
    db_session.commit()
    return bot


FORGED = [
    ("MESSAGE_UPDATE", {"id": MESSAGE, "channel_id": CHANNEL, "content": "forged content of this message", "edited_timestamp": "2026-11-06T00:00:00+00:00"}),
    ("MESSAGE_UPDATE", message_create(content="forged content of this message", edited_timestamp="2026-11-06T00:00:00+00:00")),
    ("MESSAGE_DELETE", {"id": MESSAGE, "channel_id": CHANNEL}),
    ("MESSAGE_DELETE_BULK", {"ids": [MESSAGE], "channel_id": CHANNEL}),
    ("MESSAGE_REACTION_ADD", REACTION | {"user_id": "99"}),
    ("MESSAGE_REACTION_REMOVE", REACTION),
    ("MESSAGE_REACTION_REMOVE_ALL", {"channel_id": CHANNEL, "message_id": MESSAGE}),
    ("MESSAGE_REACTION_REMOVE_EMOJI", {"channel_id": CHANNEL, "message_id": MESSAGE, "emoji": {"name": "🔥"}}),
]


@pytest.mark.parametrize("event_type, data", FORGED)
def test_another_owners_bot_cannot_change_a_message(db_session, discord_api, webhook_bot, attacker_bot, event_type, data, qdrant):
    relay(discord_api, webhook_bot, "MESSAGE_CREATE", message_create())
    relay(discord_api, webhook_bot, "MESSAGE_REACTION_ADD", REACTION)

    handle(attacker_bot, event_type, data)

    message = stored(db_session)
    assert (message.content, message.edited_at, message.reactions) == (LONG_TEXT, None, {"🔥": ["11"]})


@pytest.mark.parametrize("event_type, data", FORGED[2:4])
def test_a_bot_of_the_same_owner_can_change_a_message(db_session, discord_api, event_type, data, qdrant):
    user = HumanUser(email="owner@example.com", name="Owner", password_hash="x")
    db_session.add(user)
    db_session.flush()
    first, second = (
        DiscordBot(application_id=app_id, name=f"bot {app_id}", realtime="webhook", public_key="ab" * 32, created_by_user_id=user.id, token=token)
        for app_id, token in ((1001, HOOKED_TOKEN), (1002, SECOND_TOKEN))
    )
    db_session.add_all([first, second])
    db_session.commit()
    relay(discord_api, first, "MESSAGE_CREATE", message_create())
    assert db_session.query(DiscordMessage).count() == 1

    relay(discord_api, second, event_type, data)

    assert db_session.query(DiscordMessage).count() == 0


def test_a_push_never_renames_or_claims_existing_rows(db_session, webhook_bot, attacker_bot, qdrant):
    db_session.add_all([
        DiscordServer(id=int(GUILD), name="Real server", collect_messages=True, bot_id=webhook_bot.id),
        DiscordServer(id=5, name="Orphan", collect_messages=True),
        DiscordUser(id=int(AUTHOR), username="alice", display_name="Alice"),
    ])
    db_session.flush()
    db_session.add_all([
        DiscordChannel(id=int(CHANNEL), server_id=int(GUILD), name="general", channel_type="text"),
        DiscordChannel(id=55, server_id=5, name="orphan-chat", channel_type="text"),
    ])
    db_session.commit()

    handle(attacker_bot, "MESSAGE_CREATE", message_create(author={"id": AUTHOR, "username": "mallory", "global_name": "Mallory"}))
    handle(attacker_bot, "THREAD_UPDATE", {"id": CHANNEL, "guild_id": GUILD, "name": "renamed", "type": 11})
    handle(attacker_bot, "MESSAGE_CREATE", message_create(id="77", channel_id="55", guild_id="5"))

    db_session.expire_all()
    author = db_session.get(DiscordUser, int(AUTHOR))
    assert (author.username, author.display_name) == ("alice", "Alice")
    assert (db_session.get(DiscordServer, int(GUILD)).name, db_session.get(DiscordServer, int(GUILD)).bot_id) == ("Real server", webhook_bot.id)
    assert db_session.get(DiscordServer, 5).bot_id is None
    assert db_session.get(DiscordChannel, int(CHANNEL)).name == "general"




DM_WITH_ALICE = {"id": CHANNEL, "type": 1, "recipients": [{"id": AUTHOR, "username": "alice"}]}
KNOWN_GUILD = 31


def share_a_server(db_session, bot, user_id=AUTHOR):
    """The bot is in a server where the user has posted."""
    db_session.add_all([
        DiscordServer(id=KNOWN_GUILD, name="Shared", bot_id=bot.id),
        DiscordUser(id=int(user_id), username="alice"),
    ])
    db_session.flush()
    db_session.add_all([
        DiscordBotServer(bot_id=bot.id, server_id=KNOWN_GUILD),
        DiscordChannel(id=32, server_id=KNOWN_GUILD, name="general", channel_type="text"),
    ])
    db_session.flush()
    db_session.add(DiscordMessage(
        modality="message", sha256=b"\x02" * 32, content="hi", message_id=33, channel_id=32,
        server_id=KNOWN_GUILD, author_id=int(user_id), sent_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    ))
    db_session.commit()


@pytest.mark.parametrize("author", [{"id": AUTHOR, "username": "alice"}, {"id": "777", "username": "Theirs", "bot": True}])
def test_a_dm_the_bots_token_confirms_from_a_known_user_is_collected_for_its_owner_alone(
    db_session, user_owned_bot, attacker_bot, admin_user, discord_api, qdrant, author
):
    share_a_server(db_session, user_owned_bot)
    discord_api.channels[(SECOND_TOKEN, CHANNEL)] = DM_WITH_ALICE

    relay(discord_api, user_owned_bot, "MESSAGE_CREATE", message_create(guild_id=None, author=author))

    message = stored(db_session)
    channel = db_session.get(DiscordChannel, int(CHANNEL))
    assert (channel.name, channel.channel_type, channel.bot_id, channel.collect_messages) == (
        "DM with alice", "dm", user_owned_bot.id, None,
    )
    assert (message.creator_id, message.project_id) == (user_owned_bot.created_by_user_id, None)
    owners = [db_session.get(HumanUser, bot.created_by_user_id) for bot in (user_owned_bot, attacker_bot)]
    assert [user_can_access(reader, message) for reader in [*owners, admin_user]] == [True, False, True]


def test_a_dm_from_someone_sharing_no_server_with_the_bot_is_untracked(db_session, user_owned_bot, attacker_bot, discord_api, qdrant):
    share_a_server(db_session, attacker_bot)
    discord_api.channels[(SECOND_TOKEN, CHANNEL)] = DM_WITH_ALICE

    result = relay(discord_api, user_owned_bot, "MESSAGE_CREATE", message_create(guild_id=None))

    assert result == {"status": "skipped", "reason": "unknown_sender"}
    assert (db_session.get(DiscordChannel, int(CHANNEL)), db_session.query(DiscordMessage).count()) == (None, 1)


@pytest.mark.parametrize(
    "listed",
    [
        None,  # the token can't see it
        {"id": CHANNEL, "type": 0, "guild_id": GUILD},  # a server channel
        {"id": CHANNEL, "type": 11, "guild_id": GUILD, "parent_id": "5"},  # a thread
    ],
)
def test_a_pushed_dm_the_bots_token_does_not_confirm_is_dropped(db_session, attacker_bot, discord_api, qdrant, listed):
    share_a_server(db_session, attacker_bot)
    discord_api.channels[(ATTACKER_TOKEN, CHANNEL)] = listed

    result = handle(attacker_bot, "MESSAGE_CREATE", message_create(guild_id=None))

    assert result["status"] == "skipped"
    assert (db_session.get(DiscordChannel, int(CHANNEL)), db_session.query(DiscordMessage).count()) == (None, 1)


def test_a_gateway_mode_bot_is_held_to_push_rules_for_what_it_was_pushed(db_session, discord_api, qdrant):
    owner = HumanUser(email="g@example.com", name="G", password_hash="x")
    db_session.add(owner)
    db_session.flush()
    bot = DiscordBot(application_id=888, name="Switched", realtime="gateway", created_by_user_id=owner.id, token="tok-g")
    db_session.add(bot)
    db_session.commit()

    handle(bot, "MESSAGE_CREATE", message_create(guild_id=None))
    handle(bot, "THREAD_CREATE", {"id": "61", "type": 1})

    assert (db_session.get(DiscordChannel, int(CHANNEL)), db_session.get(DiscordChannel, 61)) == (None, None)


def stored_dm(db_session, bot, **settings) -> DiscordChannel:
    channel = DiscordChannel(id=int(CHANNEL), name="DM with alice", channel_type="dm", bot_id=bot.id, **settings)
    db_session.add(channel)
    db_session.commit()
    return channel


@pytest.mark.parametrize(
    "event_type, data",
    [
        ("MESSAGE_CREATE", message_create(id="78", guild_id=None)),
        ("MESSAGE_UPDATE", message_create(id="78", guild_id=None)),
    ],
)
def test_a_push_cannot_add_messages_to_another_owners_dm(
    db_session, user_owned_bot, attacker_bot, discord_api, qdrant, event_type, data
):
    stored_dm(db_session, user_owned_bot)

    handle(attacker_bot, event_type, data)

    db_session.expire_all()
    assert db_session.query(DiscordMessage).count() == 0
    assert db_session.get(DiscordChannel, int(CHANNEL)).bot_id == user_owned_bot.id


def test_another_owner_of_the_same_application_never_takes_a_dm(db_session, user_owned_bot, discord_api, qdrant):
    """Both owners' tokens are the same bot's, so both read the DM as theirs."""
    other = HumanUser(email="third@example.com", name="Third", password_hash="x")
    db_session.add(other)
    db_session.flush()
    thief = DiscordBot(application_id=777, name="Mine too", realtime="webhook", public_key="aa" * 32,
                       created_by_user_id=other.id, token=ATTACKER_TOKEN)
    db_session.add(thief)
    db_session.commit()
    stored_dm(db_session, user_owned_bot, collect_messages=False)
    discord_api.channels[(ATTACKER_TOKEN, CHANNEL)] = DM_WITH_ALICE

    handle(thief, "MESSAGE_CREATE", message_create(guild_id=None))

    db_session.expire_all()
    channel = db_session.get(DiscordChannel, int(CHANNEL))
    assert (channel.bot_id, channel.collect_messages, db_session.query(DiscordMessage).count()) == (
        user_owned_bot.id, False, 0,
    )


@pytest.mark.parametrize("event_type, data", [(t, d | {"guild_id": None}) for t, d in FORGED])
def test_another_owners_bot_cannot_change_a_dm_message(
    db_session, user_owned_bot, attacker_bot, discord_api, event_type, data, qdrant
):
    share_a_server(db_session, user_owned_bot)
    discord_api.channels[(SECOND_TOKEN, CHANNEL)] = DM_WITH_ALICE
    relay(discord_api, user_owned_bot, "MESSAGE_CREATE", message_create(guild_id=None))
    relay(discord_api, user_owned_bot, "MESSAGE_REACTION_ADD", REACTION)

    handle(attacker_bot, event_type, data)

    message = stored(db_session)
    assert (message.content, message.edited_at, message.reactions) == (LONG_TEXT, None, {"🔥": ["11"]})


def test_a_dm_switched_off_is_not_stored(db_session, discord_api, user_owned_bot, qdrant):
    stored_dm(db_session, user_owned_bot, collect_messages=False)

    relay(discord_api, user_owned_bot, "MESSAGE_CREATE", message_create(guild_id=None))

    assert db_session.query(DiscordMessage).count() == 0


def test_a_gateway_edit_never_starts_tracking_a_dm(db_session, user_owned_bot, qdrant):
    handle(user_owned_bot, "MESSAGE_UPDATE", message_create(guild_id=None), pushed=False)

    assert (db_session.get(DiscordChannel, int(CHANNEL)), db_session.query(DiscordMessage).count()) == (None, 0)


def squatted_dm(db_session, bot) -> DiscordChannel:
    """A DM row a push made for an id that is really a server channel, whose server and author are known."""
    channel = DiscordChannel(id=int(CHANNEL), name="squat", channel_type="dm", bot_id=bot.id)
    db_session.add_all([channel, DiscordServer(id=int(GUILD), name="Guild", verified=False), DiscordUser(id=int(AUTHOR), username="alice")])
    db_session.commit()
    return channel


def test_a_message_in_a_server_never_takes_its_creator_from_a_squatted_dm(
    db_session, webhook_bot, attacker_bot, qdrant
):
    squatted_dm(db_session, attacker_bot)
    discord.add_discord_message(**message_task_kwargs(message_create(), webhook_bot.id))

    assert stored(db_session).creator_id is None


def test_a_dm_message_another_owners_bot_delivered_has_no_creator(db_session, user_owned_bot, attacker_bot, qdrant):
    squatted_dm(db_session, attacker_bot)
    discord.add_discord_message(**message_task_kwargs(message_create(guild_id=None), user_owned_bot.id))

    assert stored(db_session).creator_id is None


SQUATTED_THREAD = {"id": CHANNEL, "guild_id": GUILD, "parent_id": "5", "name": "a thread", "type": 11}


def list_thread(bot):
    with make_session() as session:
        ensure_listed_channel(session, SQUATTED_THREAD, int(GUILD), bot.id, trusted=True)
        session.commit()


def test_a_listing_turns_a_squatted_dm_into_the_server_channel_it_is(db_session, user_owned_bot, attacker_bot, qdrant):
    squatted_dm(db_session, attacker_bot)

    list_thread(user_owned_bot)

    db_session.expire_all()
    channel = db_session.get(DiscordChannel, int(CHANNEL))
    assert (channel.server_id, channel.channel_type, channel.parent_id, channel.content_owner_id) == (
        int(GUILD), "thread", 5, None,
    )


def test_a_token_turning_a_stored_server_channel_into_a_dm_refiles_its_messages(
    db_session, user_owned_bot, attacker_bot, no_celery_dispatch, qdrant
):
    server_of(db_session, int(GUILD), user_owned_bot)
    db_session.add_all([
        DiscordBotServer(bot_id=attacker_bot.id, server_id=int(GUILD)),
        DiscordChannel(id=int(CHANNEL), server_id=int(GUILD), name="general", channel_type="text"),
    ])
    db_session.commit()
    gateway_message(user_owned_bot)
    handle(attacker_bot, "MESSAGE_CREATE", message_create(id="61"), pushed=False)

    with make_session() as session:
        discord_events.claim_dm(session, int(CHANNEL), session.get_one(DiscordBot, user_owned_bot.id))
        session.commit()

    db_session.expire_all()
    messages = {m.message_id: (m.server_id, m.bot_id) for m in db_session.query(DiscordMessage)}
    disowned = db_session.query(DiscordMessage).filter_by(message_id=61).one()
    assert messages == {int(MESSAGE): (None, user_owned_bot.id), 61: (None, None)}
    assert verifications(no_celery_dispatch) == [[user_owned_bot.id, [disowned.id]]]


def test_a_dm_a_token_shows_to_be_a_server_channel_keeps_no_creator(db_session, user_owned_bot, qdrant):
    server_of(db_session, int(GUILD), user_owned_bot)
    db_session.add_all([
        DiscordChannel(id=int(CHANNEL), name="dm", channel_type="dm", bot_id=user_owned_bot.id),
        DiscordUser(id=int(AUTHOR), username="alice"),
    ])
    db_session.flush()
    db_session.add(DiscordMessage(
        modality="message", sha256=b"\x04" * 32, content="in the dm", message_id=int(MESSAGE), channel_id=int(CHANNEL),
        author_id=int(AUTHOR), bot_id=user_owned_bot.id, creator_id=user_owned_bot.created_by_user_id,
        sent_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    ))
    db_session.commit()

    token_listing(user_owned_bot)

    message = stored(db_session)
    assert (message.server_id, message.bot_id, message.creator_id) == (int(GUILD), user_owned_bot.id, None)


@pytest.mark.parametrize("own_dm", [True, False], ids=["its own DM", "another owner's DM"])
def test_a_push_never_turns_a_dm_into_a_server_channel(db_session, user_owned_bot, attacker_bot, qdrant, own_dm):
    holder = {True: attacker_bot, False: user_owned_bot}[own_dm]
    squatted_dm(db_session, holder)

    handle(attacker_bot, "THREAD_UPDATE", SQUATTED_THREAD)

    db_session.expire_all()
    channel = db_session.get(DiscordChannel, int(CHANNEL))
    assert (channel.server_id, channel.channel_type, channel.bot_id) == (None, "dm", holder.id)


@pytest.mark.parametrize("pushed_thread", [{"id": "61", "type": 1}, {"id": "61", "type": 3}], ids=["dm", "group dm"])
def test_a_pushed_thread_event_never_makes_a_dm(db_session, attacker_bot, qdrant, pushed_thread):
    handle(attacker_bot, "THREAD_CREATE", pushed_thread)

    assert db_session.get(DiscordChannel, 61) is None


def victims_server(db_session, victim) -> None:
    """A server the victim's bot verified, with a channel in it."""
    db_session.add(DiscordServer(id=int(GUILD), name="Victim's", bot_id=victim.id, verified=True, collect_messages=True))
    db_session.flush()
    db_session.add(DiscordChannel(id=int(CHANNEL), server_id=int(GUILD), name="general", channel_type="text"))
    db_session.commit()


@pytest.mark.parametrize(
    "event_type, data",
    [
        ("MESSAGE_CREATE", message_create()),
        ("MESSAGE_CREATE", message_create(guild_id=None)),  # naming no server doesn't get round it
        ("MESSAGE_UPDATE", message_create()),
        ("THREAD_CREATE", {"id": "61", "guild_id": GUILD, "parent_id": CHANNEL, "name": "t", "type": 11}),
    ],
)
def test_a_push_into_another_owners_verified_server_is_refused(
    db_session, user_owned_bot, attacker_bot, discord_api, qdrant, event_type, data
):
    victims_server(db_session, user_owned_bot)

    handle(attacker_bot, event_type, data)

    assert (db_session.query(DiscordMessage).count(), db_session.get(DiscordChannel, 61)) == (0, None)


@pytest.mark.parametrize(
    "event_type, data",
    [
        ("MESSAGE_CREATE", message_create(channel_id="61")),
        ("THREAD_CREATE", {"id": "61", "guild_id": GUILD, "parent_id": CHANNEL, "name": "t", "type": 11}),
    ],
)
def test_a_new_channel_in_a_server_the_bot_may_not_push_into_is_refused_before_asking_its_token(
    db_session, user_owned_bot, attacker_bot, discord_api, qdrant, event_type, data
):
    victims_server(db_session, user_owned_bot)
    discord_api.channels[(ATTACKER_TOKEN, "61")] = listing(61, type=11, parent_id=CHANNEL)

    result = handle(attacker_bot, event_type, data)

    assert result == {"status": "skipped", "reason": "not_member"}
    assert (db_session.get(DiscordChannel, 61), discord_api.requests) == (None, [])
    assert db_session.get(DiscordBotServer, (attacker_bot.id, int(GUILD))) is None


def co_member(db_session, owner, member) -> None:
    """The owner's verified server, which a token also lists the member's bot in."""
    victims_server(db_session, owner)
    db_session.add(DiscordBotServer(bot_id=member.id, server_id=int(GUILD)))
    db_session.commit()


FORGER = {"id": "13", "username": "mallory"}
FUTURE = "2099-01-01T00:00:00+00:00"


def holds(db_session, bot) -> None:
    pass


def holds_nothing(db_session, bot) -> None:
    """The server's owning bot has left it."""
    db_session.get(DiscordServer, int(GUILD)).bot_id = None
    db_session.commit()


@pytest.mark.parametrize("holder", [holds, holds_nothing], ids=["held by another", "held by no one"])
@pytest.mark.parametrize(
    "event_type, data",
    [
        ("MESSAGE_CREATE", message_create(id="79")),
        *FORGED,
        ("THREAD_CREATE", {"id": "61", "guild_id": GUILD, "parent_id": CHANNEL, "name": "t", "type": 11}),
        ("THREAD_UPDATE", {"id": CHANNEL, "guild_id": GUILD, "name": "renamed", "type": 11}),
    ],
    ids=[
        "create", "partial edit", "full edit", "delete", "bulk delete", "reaction", "unreaction", "clear", "clear emoji",
        "thread create", "thread update",
    ],
)
def test_a_push_from_a_member_that_does_not_hold_the_server_is_refused(
    db_session, user_owned_bot, attacker_bot, discord_api, qdrant, holder, event_type, data
):
    co_member(db_session, user_owned_bot, attacker_bot)
    handle(user_owned_bot, "MESSAGE_CREATE", message_create(), pushed=False)
    handle(user_owned_bot, "MESSAGE_REACTION_ADD", REACTION, pushed=False)
    holder(db_session, user_owned_bot)
    raw_id = delivery(db_session, attacker_bot)

    result = discord.handle_discord_event(bot_id=attacker_bot.id, event_type=event_type, data=data, caused_by=raw_id)

    message = stored(db_session)
    assert result == {"status": "skipped", "reason": "held_by_other"}
    assert (message.content, message.edited_at, message.reactions, db_session.query(DiscordMessage).count()) == (
        LONG_TEXT, None, {"🔥": ["11"]}, 1,
    )
    channel = db_session.get(DiscordChannel, int(CHANNEL))
    assert (channel.name, channel.channel_type, db_session.get(DiscordChannel, 61)) == ("general", "text", None)
    assert discord_api.requests == []
    assert [e.outcome for e in db_session.query(Event).filter_by(kind="raw").populate_existing()] == ["rejected"]


@pytest.mark.parametrize("pusher, stored_count", [("user_owned_bot", 1), ("attacker_bot", 0)], ids=["holder", "outsider"])
def test_a_push_naming_no_server_into_a_stored_server_channel_takes_its_stored_server(
    db_session, user_owned_bot, attacker_bot, discord_api, qdrant, request, pusher, stored_count
):
    victims_server(db_session, user_owned_bot)

    relay(discord_api, request.getfixturevalue(pusher), "MESSAGE_CREATE", message_create(guild_id=None))

    db_session.expire_all()
    assert [m.server_id for m in db_session.query(DiscordMessage)] == [int(GUILD)] * stored_count
    assert [r.url.path for r in discord_api.requests] == [f"/api/v10/channels/{CHANNEL}/messages/{MESSAGE}"] * stored_count


def test_a_push_reusing_a_stored_message_id_never_touches_its_images(db_session, user_owned_bot, attacker_bot, qdrant):
    """A forged create naming another owner's message id, with its image URL re-signed so the download fails."""
    victims_server(db_session, user_owned_bot)
    server_of(db_session, ATTACKERS_GUILD, attacker_bot)
    db_session.add(DiscordChannel(id=55, server_id=ATTACKERS_GUILD, name="mine", channel_type="text"))
    db_session.commit()
    handle(user_owned_bot, "MESSAGE_CREATE", message_create(), pushed=False)

    with patch.object(discord, "stream_download_to_path", return_value=False) as download:
        handle(attacker_bot, "MESSAGE_CREATE", message_create(
            channel_id="55", guild_id=str(ATTACKERS_GUILD), attachments=[image_attachment(f"{CDN}?junk")],
        ))

    download.assert_not_called()


@pytest.mark.parametrize("is_edit", [False, True], ids=["create", "edit"])
def test_a_message_stored_elsewhere_while_a_store_downloads_its_images_is_never_overwritten(
    db_session, user_owned_bot, attacker_bot, qdrant, is_edit
):
    """A store naming another owner's message id in its own channel, kept between its checks by a slow image while
    the gateway stores the real message in its channel."""
    victims_server(db_session, user_owned_bot)
    server_of(db_session, ATTACKERS_GUILD, attacker_bot)
    db_session.add(DiscordChannel(id=55, server_id=ATTACKERS_GUILD, name="mine", channel_type="text"))
    db_session.commit()

    def gateway_stores_the_real_one(urls, message_id):
        handle(user_owned_bot, "MESSAGE_CREATE", message_create(), pushed=False)
        return ["discord/forged.png"]

    forged = message_create(
        channel_id="55", guild_id=str(ATTACKERS_GUILD), content=FORGED_TEXT, attachments=[image_attachment(CDN)],
    )
    with patch.object(discord, "download_and_save_images", side_effect=gateway_stores_the_real_one):
        discord.add_discord_message(**message_task_kwargs(forged, attacker_bot.id, is_edit=is_edit))

    message = stored(db_session)
    assert (message.content, message.channel_id, message.images, message.bot_id) == (
        LONG_TEXT, int(CHANNEL), None, user_owned_bot.id,
    )


@pytest.mark.parametrize("guild", [{"guild_id": GUILD}, {}], ids=["naming the server", "naming none"])
def test_a_co_members_forged_edit_never_holds_back_a_real_one(db_session, user_owned_bot, attacker_bot, qdrant, guild):
    co_member(db_session, user_owned_bot, attacker_bot)
    handle(user_owned_bot, "MESSAGE_CREATE", message_create(), pushed=False)

    handle(attacker_bot, "MESSAGE_UPDATE", {
        "id": MESSAGE, "channel_id": CHANNEL, "content": "forged content of this message", "edited_timestamp": FUTURE,
    } | guild)
    forged = stored(db_session).content
    handle(user_owned_bot, "MESSAGE_UPDATE", message_create(content="the real edit of this message", edited_timestamp="2026-11-05T13:00:00+00:00"), pushed=False)

    assert (forged, stored(db_session).content) == (LONG_TEXT, "the real edit of this message")


def test_a_squatter_cannot_move_its_dm_into_a_verified_server_it_is_not_in(db_session, user_owned_bot, attacker_bot, qdrant):
    squatted_dm(db_session, attacker_bot)
    db_session.get(DiscordServer, int(GUILD)).verified = True
    db_session.get(DiscordServer, int(GUILD)).bot_id = user_owned_bot.id
    db_session.commit()

    with make_session() as session:
        discord_events.ensure_channel(session, int(CHANNEL), int(GUILD), attacker_bot.id)
        session.commit()

    db_session.expire_all()
    assert db_session.get(DiscordChannel, int(CHANNEL)).server_id is None


def test_custom_emojis_with_one_name_stay_apart(db_session, discord_api, webhook_bot, qdrant):
    relay(discord_api, webhook_bot, "MESSAGE_CREATE", message_create())

    for emoji_id in ("1", "2"):
        relay(discord_api, webhook_bot, "MESSAGE_REACTION_ADD", REACTION | {"emoji": {"id": emoji_id, "name": "party"}})

    assert stored(db_session).reactions == {"party:1": ["11"], "party:2": ["11"]}


def test_a_content_only_update_keeps_the_edit_time(db_session, discord_api, webhook_bot, qdrant):
    relay(discord_api, webhook_bot, "MESSAGE_CREATE", message_create(edited_timestamp="2026-11-05T13:00:00+00:00"))

    result = relay(discord_api, webhook_bot, "MESSAGE_UPDATE", {"id": MESSAGE, "channel_id": CHANNEL, "content": EDITED})

    message = stored(db_session)
    assert result["status"] != "error"
    assert (message.content, message.edited_at) == (EDITED, EDITED_AT)


def test_a_full_update_without_an_edit_time_keeps_the_stored_one(db_session, discord_api, webhook_bot, qdrant):
    relay(discord_api, webhook_bot, "MESSAGE_CREATE", message_create(edited_timestamp="2026-11-05T13:00:00+00:00"))

    relay(discord_api, webhook_bot, "MESSAGE_UPDATE", message_create(content=EDITED))

    assert stored(db_session).edited_at == EDITED_AT



def test_the_first_pusher_of_a_server_owns_nothing_in_it_once_another_token_verifies_it(
    db_session, webhook_bot, attacker_bot, discord_api, qdrant
):
    """Whoever pushes a guild id first creates the row, and is a member if its
    token placed the channel there, but owns nothing in it once another token
    verifies the server; until then it is closed to other owners."""
    discord_api.channels[(ATTACKER_TOKEN, CHANNEL)] = listing(CHANNEL)
    relay(discord_api, attacker_bot, "MESSAGE_CREATE", message_create(id="70", author={"id": "5", "username": "x"}))
    blocked = relay(discord_api, webhook_bot, "MESSAGE_CREATE", message_create())
    with make_session() as session:
        discord_events.verify_server(session, int(GUILD), webhook_bot.id, collect=True)
        session.commit()
    relay(discord_api, webhook_bot, "MESSAGE_CREATE", message_create())

    handle(attacker_bot, "MESSAGE_DELETE", {"id": MESSAGE, "channel_id": CHANNEL})
    handle(attacker_bot, "THREAD_UPDATE", {"id": CHANNEL, "guild_id": GUILD, "name": "renamed", "type": 11})

    db_session.expire_all()
    server = db_session.get(DiscordServer, int(GUILD))
    assert blocked == {"status": "skipped", "reason": "not_member"}
    assert (server.bot_id, server.verified) == (webhook_bot.id, True)
    assert db_session.query(DiscordMessage).filter_by(message_id=int(MESSAGE)).count() == 1
    assert db_session.get(DiscordChannel, int(CHANNEL)).name == f"channel {CHANNEL}"


PUSHED_THREAD = 900000000000000042
ATTACKERS_GUILD = 5


def victims_thread_with_a_message(db_session, discord_api, victim, attacker) -> None:
    """The victim's verified server with a thread holding one of its messages; the attacker's own server has a
    channel that isn't collected."""
    db_session.add_all([
        DiscordServer(id=int(GUILD), name="Victim", bot_id=victim.id, verified=True, collect_messages=True),
        DiscordServer(id=ATTACKERS_GUILD, name="Off", bot_id=attacker.id, verified=True, collect_messages=False),
    ])
    db_session.flush()
    db_session.add_all([
        DiscordBotServer(bot_id=victim.id, server_id=int(GUILD)),
        DiscordBotServer(bot_id=attacker.id, server_id=ATTACKERS_GUILD),
        DiscordChannel(id=int(CHANNEL), server_id=int(GUILD), name="general", channel_type="text"),
        DiscordChannel(id=55, server_id=ATTACKERS_GUILD, name="off", channel_type="text"),
    ])
    db_session.commit()
    handle(victim, "THREAD_CREATE", {"id": str(PUSHED_THREAD), "guild_id": GUILD, "parent_id": CHANNEL, "type": 11})
    relay(discord_api, victim, "MESSAGE_CREATE", message_create(channel_id=str(PUSHED_THREAD)))


@pytest.mark.parametrize(
    "hijack",
    [
        {"parent_id": "55"},
        {"parent_id": "55", "guild_id": str(ATTACKERS_GUILD)},
        {"parent_id": "55", "guild_id": GUILD},
    ],
    ids=["no guild", "the attacker's guild", "the victim's guild"],
)
def test_a_push_cannot_reparent_another_owners_thread(db_session, discord_api, webhook_bot, attacker_bot, qdrant, hijack):
    victims_thread_with_a_message(db_session, discord_api, webhook_bot, attacker_bot)

    handle(attacker_bot, "THREAD_UPDATE", {"id": str(PUSHED_THREAD), "type": 11, **hijack})

    db_session.expire_all()
    thread = db_session.get(DiscordChannel, PUSHED_THREAD)
    assert (thread.parent_id, thread.server_id, db_session.query(DiscordMessage).count()) == (int(CHANNEL), int(GUILD), 1)


def test_a_thread_cannot_be_moved_under_a_channel_of_another_server(db_session, discord_api, webhook_bot, qdrant):
    victims_thread_with_a_message(db_session, discord_api, webhook_bot, webhook_bot)

    handle(webhook_bot, "THREAD_UPDATE", {"id": str(PUSHED_THREAD), "guild_id": GUILD, "parent_id": "55", "type": 11})

    db_session.expire_all()
    assert db_session.get(DiscordChannel, PUSHED_THREAD).parent_id == int(CHANNEL)


@pytest.mark.parametrize("pushed", [{"type": 11, "parent_id": CHANNEL}, {"type": 0}], ids=["thread", "channel"])
def test_a_pushed_channel_with_no_server_is_never_stored(db_session, discord_api, webhook_bot, attacker_bot, qdrant, pushed):
    victims_server(db_session, webhook_bot)

    handle(attacker_bot, "THREAD_CREATE", {"id": str(PUSHED_THREAD), **pushed})
    relay(discord_api, webhook_bot, "MESSAGE_CREATE", message_create(channel_id=str(PUSHED_THREAD)))

    db_session.expire_all()
    thread = db_session.get(DiscordChannel, PUSHED_THREAD)
    assert (thread.server_id, thread.bot_id) == (int(GUILD), webhook_bot.id)
    assert stored(db_session).server_id == int(GUILD)


def test_a_push_into_a_serverless_channel_that_is_not_a_dm_is_refused(db_session, attacker_bot, qdrant):
    db_session.add(DiscordChannel(id=int(CHANNEL), name="orphan", channel_type="text", bot_id=attacker_bot.id))
    db_session.commit()

    result = handle(attacker_bot, "MESSAGE_CREATE", message_create(guild_id=None))

    assert (result["status"], db_session.query(DiscordMessage).count()) == ("skipped", 0)


def test_a_forged_guild_on_a_message_in_ones_own_dm_teaches_no_one_a_stranger(db_session, webhook_bot, attacker_bot, qdrant):
    victims_server(db_session, webhook_bot)
    db_session.add_all([
        DiscordBotServer(bot_id=webhook_bot.id, server_id=int(GUILD)),
        DiscordChannel(id=70, name="DM", channel_type="dm", bot_id=attacker_bot.id),
    ])
    db_session.commit()
    stranger = 123456789

    handle(attacker_bot, "MESSAGE_CREATE", message_create(
        id="71", channel_id="70", author={"id": str(stranger), "username": "s"},
    ))

    db_session.expire_all()
    assert discord_events.knows_user(db_session, webhook_bot.id, [stranger]) is False


def test_a_push_naming_another_server_than_its_channels_is_refused(db_session, user_owned_bot, qdrant):
    share_a_server(db_session, user_owned_bot)
    stored_dm(db_session, user_owned_bot)

    result = handle(user_owned_bot, "MESSAGE_CREATE", message_create(guild_id=str(KNOWN_GUILD)))

    assert (result, db_session.query(DiscordMessage).count()) == ({"status": "skipped", "reason": "guild_mismatch"}, 1)


def test_events_with_no_origin_are_taken_for_pushes(db_session, user_owned_bot, attacker_bot, qdrant):
    victims_server(db_session, user_owned_bot)

    discord.handle_discord_event(bot_id=attacker_bot.id, event_type="MESSAGE_CREATE", data=message_create())

    assert db_session.query(DiscordMessage).count() == 0


# --- Pushes that would restructure rows ------------------------------------
# A push can name a real channel of someone else's server under a server of its
# own; only the bot's token can say which server a new channel is in.


def server_of(db_session, server_id, bot) -> None:
    """A server the bot's token lists it in."""
    db_session.add(DiscordServer(id=server_id, name=f"Server {server_id}", bot_id=bot.id, verified=True, collect_messages=True))
    db_session.flush()
    db_session.add(DiscordBotServer(bot_id=bot.id, server_id=server_id))
    db_session.commit()


@pytest.mark.parametrize(
    "event_type, data",
    [
        ("MESSAGE_CREATE", message_create(guild_id=str(ATTACKERS_GUILD))),
        ("MESSAGE_UPDATE", message_create(guild_id=str(ATTACKERS_GUILD))),
        ("THREAD_CREATE", {"id": CHANNEL, "guild_id": str(ATTACKERS_GUILD), "type": 11}),
    ],
)
@pytest.mark.parametrize("token_sees", [None, listing(CHANNEL)], ids=["nothing", "the channel in its real server"])
def test_a_push_never_creates_a_channel_its_token_does_not_place_in_the_pushed_server(
    db_session, user_owned_bot, attacker_bot, discord_api, qdrant, event_type, data, token_sees
):
    server_of(db_session, int(GUILD), user_owned_bot)
    server_of(db_session, ATTACKERS_GUILD, attacker_bot)
    discord_api.channels[(ATTACKER_TOKEN, CHANNEL)] = token_sees

    result = handle(attacker_bot, event_type, data)

    assert result == {"status": "skipped", "reason": "unconfirmed_channel"}
    assert (db_session.get(DiscordChannel, int(CHANNEL)), db_session.query(DiscordMessage).count()) == (None, 0)


def test_a_new_channel_is_stored_as_the_bots_token_lists_it(db_session, user_owned_bot, discord_api, qdrant):
    server_of(db_session, int(GUILD), user_owned_bot)
    discord_api.channels[(SECOND_TOKEN, THREAD)] = listing(THREAD, type=11, parent_id=CHANNEL, name="a thread")

    relay(discord_api, user_owned_bot, "MESSAGE_CREATE", message_create(channel_id=THREAD))

    db_session.expire_all()
    thread = db_session.get(DiscordChannel, int(THREAD))
    assert (thread.name, thread.channel_type, thread.parent_id, thread.server_id) == ("a thread", "thread", int(CHANNEL), int(GUILD))
    assert stored(db_session).channel_id == int(THREAD)
    assert [r.url.path for r in discord_api.requests] == [f"/api/v10/channels/{THREAD}", f"/api/v10/channels/{THREAD}/messages/{MESSAGE}"]


def channel_stored_under_the_attackers_server(db_session, victim, attacker) -> None:
    """The victim's channel as a push filed it: under the attacker's server, with the attacker's project and a
    message the attacker pushed into it."""
    server_of(db_session, int(GUILD), victim)
    server_of(db_session, ATTACKERS_GUILD, attacker)
    project = Project(title="Attacker's", state="open")
    db_session.add(project)
    db_session.flush()
    db_session.add_all([
        DiscordChannel(id=int(CHANNEL), server_id=ATTACKERS_GUILD, name="secret", channel_type="thread",
                       parent_id=55, project_id=project.id, sensitivity="public", collect_messages=True),
        DiscordUser(id=int(AUTHOR), username="alice"),
    ])
    db_session.flush()
    db_session.add(DiscordMessage(
        modality="message", sha256=b"\x03" * 32, content="planted", message_id=61, channel_id=int(CHANNEL),
        server_id=ATTACKERS_GUILD, author_id=int(AUTHOR), bot_id=attacker.id, creator_id=attacker.created_by_user_id,
        sent_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    ))
    db_session.commit()


def gateway_message(bot):
    handle(bot, "MESSAGE_CREATE", message_create(), pushed=False)


def token_listing(bot):
    with make_session() as session:
        ensure_listed_channel(session, listing(CHANNEL), int(GUILD), bot.id, trusted=True)
        session.commit()


@pytest.mark.parametrize("trusted_source", [gateway_message, token_listing])
def test_trusted_data_takes_a_channel_back_from_the_server_a_push_filed_it_under(
    db_session, user_owned_bot, attacker_bot, qdrant, no_celery_dispatch, trusted_source
):
    channel_stored_under_the_attackers_server(db_session, user_owned_bot, attacker_bot)
    no_celery_dispatch.reset_mock()

    trusted_source(user_owned_bot)

    db_session.expire_all()
    channel = db_session.get(DiscordChannel, int(CHANNEL))
    attacker = db_session.get(HumanUser, attacker_bot.created_by_user_id)
    assert (channel.server_id, channel.bot_id, channel.parent_id) == (int(GUILD), user_owned_bot.id, None)
    assert (channel.project_id, channel.sensitivity, channel.collect_messages) == (None, "basic", None)
    assert user_can_manage_channel(attacker, db_session, channel) is False
    planted = db_session.query(DiscordMessage).filter_by(message_id=61).one()
    assert (planted.bot_id, planted.creator_id) == (None, None)
    assert {m.server_id for m in db_session.query(DiscordMessage)} <= {int(GUILD)}
    dispatched = [c.kwargs["args"][:2] for c in no_celery_dispatch.call_args_list if c.args[0] == UPDATE_SOURCE_ACCESS_CONTROL]
    assert ["discord_channel", int(CHANNEL)] in dispatched


def test_a_co_member_push_never_retypes_a_channel_or_drops_its_messages(db_session, user_owned_bot, attacker_bot, qdrant):
    server_of(db_session, int(GUILD), user_owned_bot)
    db_session.add_all([
        DiscordBotServer(bot_id=attacker_bot.id, server_id=int(GUILD)),
        DiscordChannel(id=int(CHANNEL), server_id=int(GUILD), name="general", channel_type="text"),
        DiscordChannel(id=77, server_id=int(GUILD), name="off", channel_type="text", collect_messages=False),
    ])
    db_session.commit()
    gateway_message(user_owned_bot)

    handle(attacker_bot, "THREAD_UPDATE", {"id": CHANNEL, "guild_id": GUILD, "parent_id": "77", "type": 11})

    db_session.expire_all()
    channel = db_session.get(DiscordChannel, int(CHANNEL))
    assert (channel.channel_type, channel.parent_id, db_session.query(DiscordMessage).count()) == ("text", None, 1)


@pytest.mark.parametrize("pushed, channel_type", [(True, "text"), (False, "thread")])
def test_only_trusted_data_turns_a_stored_channel_into_a_thread(db_session, user_owned_bot, qdrant, pushed, channel_type):
    server_of(db_session, int(GUILD), user_owned_bot)
    db_session.add(DiscordChannel(id=42, server_id=int(GUILD), name="Channel 42", channel_type="text"))
    db_session.commit()

    handle(user_owned_bot, "THREAD_CREATE", {"id": "42", "guild_id": GUILD, "parent_id": CHANNEL, "type": 11}, pushed=pushed)

    db_session.expire_all()
    assert db_session.get(DiscordChannel, 42).channel_type == channel_type


@pytest.mark.parametrize(
    "event_type, data",
    [("MESSAGE_CREATE", message_create()), ("THREAD_CREATE", {"id": THREAD, "guild_id": GUILD, "type": 11})],
)
def test_a_push_its_token_places_in_a_server_records_the_bot_there(db_session, discord_api, webhook_bot, event_type, data, qdrant):
    relay(discord_api, webhook_bot, event_type, data)

    assert db_session.get(DiscordBotServer, (webhook_bot.id, int(GUILD))) is not None


def stays(db_session, bot) -> None:
    pass


def leaves(db_session, bot) -> None:
    db_session.query(DiscordBotServer).filter_by(bot_id=bot.id).delete()
    db_session.commit()


@pytest.mark.parametrize("pusher, delivered_by", [(stays, "pusher"), (leaves, None)], ids=["member", "left"])
def test_a_token_taking_over_a_pushed_server_keeps_what_was_pushed_into_it(
    db_session, discord_api, webhook_bot, user_owned_bot, no_celery_dispatch, qdrant, pusher, delivered_by
):
    relay(discord_api, webhook_bot, "MESSAGE_CREATE", message_create())
    pusher(db_session, webhook_bot)

    with make_session() as session:
        discord_events.verify_server(session, int(GUILD), user_owned_bot.id, collect=True, name="Real")
        session.commit()

    message = stored(db_session)
    assert (message.content, message.bot_id) == (LONG_TEXT, {"pusher": webhook_bot.id, None: None}[delivered_by])
    assert db_session.get(DiscordServer, int(GUILD)).bot_id == user_owned_bot.id
    assert verifications(no_celery_dispatch) == {"pusher": [], None: [[user_owned_bot.id, [message.id]]]}[delivered_by]


def verifications(dispatch) -> list:
    return [c.kwargs["args"] for c in dispatch.call_args_list if c.args[0] == VERIFY_DISCORD_MESSAGES]


FORGED_TEXT = "forged content of this message"


def disowned_forgery(db_session, holder, **forged) -> DiscordMessage:
    """A message the holder's server keeps as another owner pushed it."""
    server_of(db_session, int(GUILD), holder)
    handle(holder, "MESSAGE_CREATE", message_create(**forged), pushed=False)
    message = stored(db_session)
    message.bot_id = None
    db_session.commit()
    return message


ROW_FIELDS = ("content", "author_id", "edited_at", "reply_to_message_id", "message_type", "reactions")
AS_PUSHED = (FORGED_TEXT, 13, datetime(2099, 1, 1, tzinfo=timezone.utc), None, "default", None)
REAL_ROW = (LONG_TEXT, int(AUTHOR), None, None, "default", None)
PUSHED_AS = {"content": FORGED_TEXT, "author": FORGER, "edited_timestamp": FUTURE}
CDN = "https://cdn.discordapp.com/attachments/2/500/a.png"


def image_attachment(url: str) -> dict:
    return {"id": "500", "filename": "a.png", "content_type": "image/png", "size": 1, "url": url}


def stored_images(urls: list[str]) -> list[str] | None:
    return [discord_attachments.stored_image_name(url, int(MESSAGE)) for url in urls] or None


FIRE = {"🔥": ["11"]}


@pytest.fixture
def discord_storage():
    """Discord's image directory under the tests' storage root; settings derives it once, at import."""
    with patch.object(settings, "DISCORD_STORAGE_DIR", settings.FILE_STORAGE_DIR / "discord"):
        yield


@pytest.mark.parametrize(
    "forged, image_urls, reactions, answer, rows, images",
    [
        ({}, [], None, discord_error(404, 10008), [], []),
        ({}, [], None, discord_error(404, 10003), [REAL_ROW], []),
        ({}, [], None, discord_error(403, 50001), [REAL_ROW], []),
        (PUSHED_AS, [], None, message_create(), [REAL_ROW], []),
        (PUSHED_AS, [], FIRE, message_create(**PUSHED_AS), [AS_PUSHED], []),
        ({"type": 19, "message_reference": {"message_id": "5"}}, [f"{CDN}?forged"], FIRE, message_create(), [REAL_ROW], []),
        (
            {},
            [f"{CDN}?ex=1&hm=signed-then"],
            None,
            message_create(attachments=[image_attachment(f"{CDN}?ex=2&hm=signed-now")]),
            [REAL_ROW],
            [f"{CDN}?ex=1&hm=signed-then"],
        ),
    ],
    ids=[
        "unknown message", "unknown channel", "unreachable", "rewritten", "as stored, reactions cleared",
        "real text, forged image and reply", "same image, newly signed URL",
    ],
)
def test_a_disowned_message_becomes_what_the_holders_token_reads(
    db_session, user_owned_bot, discord_api, discord_storage, qdrant, forged, image_urls, reactions, answer, rows, images
):
    message = disowned_forgery(db_session, user_owned_bot, **forged)
    message.images, message.reactions = stored_images(image_urls), reactions
    db_session.commit()
    discord_api.channels[(SECOND_TOKEN, f"{CHANNEL}/messages/{MESSAGE}")] = answer

    with patch.object(discord, "download_and_save_images") as download:
        discord.verify_discord_messages(user_owned_bot.id, [message.id])

    db_session.expire_all()
    messages = db_session.query(DiscordMessage).all()
    assert [tuple(getattr(m, f) for f in ROW_FIELDS) for m in messages] == rows
    assert [m.images for m in messages] == [stored_images(images)] * len(rows)
    assert [p.contact_info for m in messages for p in m.people] == [{"discord": str(row[1])} for row in rows]
    download.assert_not_called()


def two_disowned_forgeries(db_session, holder) -> list[int]:
    disowned_forgery(db_session, holder, content=FORGED_TEXT)
    handle(holder, "MESSAGE_CREATE", message_create(id="62", content=FORGED_TEXT), pushed=False)
    db_session.query(DiscordMessage).update({"bot_id": None})
    db_session.commit()
    return [m.id for m in db_session.query(DiscordMessage).order_by(DiscordMessage.id)]


@contextmanager
def task_request(task, **fields):
    """Run a task's body as a worker would, with this request (its retry count)."""
    task.push_request(**fields)
    try:
        yield
    finally:
        task.pop_request()


@pytest.mark.parametrize("retries, countdown", [(0, 60), (3, 480), (10, 3600)])
def test_a_check_discord_cannot_answer_now_keeps_what_it_read_and_retries_the_rest(
    db_session, user_owned_bot, discord_api, qdrant, retries, countdown
):
    ids = two_disowned_forgeries(db_session, user_owned_bot)
    discord_api.channels[(SECOND_TOKEN, f"{CHANNEL}/messages/{MESSAGE}")] = message_create()
    discord_api.channels[(SECOND_TOKEN, f"{CHANNEL}/messages/62")] = httpx.Response(429, json={"retry_after": 600})
    task = cast(Task, discord.verify_discord_messages)

    with task_request(task, retries=retries), patch.object(task, "retry", side_effect=Retry()) as retry:
        with pytest.raises(Retry):
            task.run(user_owned_bot.id, ids)

    retry.assert_called_once_with(exc=ANY, args=[user_owned_bot.id, ids[1:]], countdown=countdown)
    assert retry.call_args.kwargs["exc"].status == 429
    db_session.expire_all()
    assert [m.content for m in db_session.query(DiscordMessage).order_by(DiscordMessage.id)] == [LONG_TEXT, FORGED_TEXT]


@pytest.mark.parametrize(
    "answer, result",
    [
        (discord_error(401, 0), {"status": "skipped", "reason": "unauthorized"}),
        (discord_error(403, 50001), {"status": "verified", "deleted": 0, "rewritten": 0}),
    ],
    ids=["token rejected", "channel unreachable"],
)
def test_a_check_stops_asking_about_what_its_token_cannot_read(
    db_session, user_owned_bot, discord_api, qdrant, answer, result
):
    ids = two_disowned_forgeries(db_session, user_owned_bot)
    discord_api.channels[(SECOND_TOKEN, f"{CHANNEL}/messages/{MESSAGE}")] = answer

    assert discord.verify_discord_messages(user_owned_bot.id, ids) == result
    assert len(discord_api.requests) == 1
    assert [m.content for m in db_session.query(DiscordMessage).populate_existing()] == [FORGED_TEXT, FORGED_TEXT]


def test_a_check_by_a_paused_bot_is_skipped(db_session, user_owned_bot, discord_api, qdrant):
    message = disowned_forgery(db_session, user_owned_bot, content=FORGED_TEXT)
    user_owned_bot.is_active = False
    db_session.commit()

    result = discord.verify_discord_messages(user_owned_bot.id, [message.id])

    assert (result, stored(db_session).content, discord_api.requests) == ({"status": "skipped", "reason": "no_access"}, FORGED_TEXT, [])


def test_disowned_messages_are_checked_in_chunks(no_celery_dispatch):
    discord_events.verify_disowned(7, list(range(250)))

    assert verifications(no_celery_dispatch) == [[7, list(range(100))], [7, list(range(100, 200))], [7, list(range(200, 250))]]


@pytest.mark.parametrize("event_type, data", FORGED)
def test_a_bot_no_longer_in_a_server_cannot_change_what_it_delivered_there(
    db_session, user_owned_bot, attacker_bot, event_type, data, qdrant
):
    co_member(db_session, user_owned_bot, attacker_bot)
    handle(attacker_bot, "MESSAGE_CREATE", message_create(), pushed=False)
    handle(attacker_bot, "MESSAGE_REACTION_ADD", REACTION, pushed=False)
    db_session.query(DiscordBotServer).filter_by(bot_id=attacker_bot.id).delete()
    db_session.commit()

    handle(attacker_bot, event_type, data)

    message = stored(db_session)
    assert (message.content, message.edited_at, message.reactions) == (LONG_TEXT, None, {"🔥": ["11"]})


@pytest.mark.parametrize(
    "event_type, data",
    [
        ("MESSAGE_CREATE", message_create(guild_id={"id": GUILD})),
        ("MESSAGE_CREATE", message_create(channel_id="1" * 40)),
        ("MESSAGE_CREATE", message_create(author="alice")),
        ("MESSAGE_CREATE", message_create(timestamp="yesterday")),
        ("MESSAGE_DELETE_BULK", {"ids": [MESSAGE, "x"], "channel_id": CHANNEL}),
        ("THREAD_CREATE", {"id": THREAD, "guild_id": GUILD, "parent_id": [CHANNEL], "type": 11}),
        ("MESSAGE_CREATE", message_create(type=[0])),
        ("THREAD_CREATE", {"id": THREAD, "guild_id": GUILD, "type": {"t": 11}}),
        ("MESSAGE_CREATE", message_create(author={"id": AUTHOR, "username": ["alice"]})),
        ("MESSAGE_CREATE", message_create(author={"id": AUTHOR, "global_name": 5})),
        ("MESSAGE_CREATE", message_create(attachments=[{"filename": "a", "content_type": ["image/png"], "url": "u"}])),
    ],
)
def test_a_payload_of_the_wrong_shape_is_refused(db_session, webhook_bot, discord_api, event_type, data, qdrant):
    assert handle(webhook_bot, event_type, data) == {"status": "skipped", "reason": "malformed"}
    assert (db_session.query(DiscordMessage).count(), discord_api.requests) == (0, [])


def delivery(db_session, bot) -> int:
    raw_id = record_raw_event(
        db_session, source="discord", event_type="MESSAGE_CREATE", account_id=bot.id,
        owner_id=bot.created_by_user_id, delivery_id="d1", outcome="stored", container=CHANNEL,
    )
    db_session.commit()
    assert raw_id is not None
    return raw_id


@pytest.mark.parametrize(
    "data",
    [message_create(), message_create(channel_id="61"), message_create(guild_id="5"), message_create(guild_id=None, channel_id="61")],
    ids=["not_member", "not_member before the token is asked", "guild_mismatch", "unconfirmed_dm"],
)
def test_a_push_the_worker_refuses_is_recorded_as_rejected(db_session, user_owned_bot, attacker_bot, discord_api, qdrant, data):
    victims_server(db_session, user_owned_bot)
    raw_id = delivery(db_session, attacker_bot)

    discord.handle_discord_event(bot_id=attacker_bot.id, event_type="MESSAGE_CREATE", data=data, caused_by=raw_id)

    assert [e.outcome for e in db_session.query(Event).filter_by(kind="raw").populate_existing()] == ["rejected"]


@pytest.mark.parametrize("content", [LONG_TEXT, "ok thanks"], ids=["embedded", "too short to embed"])
def test_a_push_the_worker_stores_stays_stored(db_session, discord_api, webhook_bot, qdrant, content):
    raw_id = delivery(db_session, webhook_bot)
    discord_api.post(message_create(content=content))

    discord.handle_discord_event(
        bot_id=webhook_bot.id, event_type="MESSAGE_CREATE", data=message_create(content=content), caused_by=raw_id
    )

    assert stored(db_session).content == content
    assert [e.outcome for e in db_session.query(Event).filter_by(kind="raw").populate_existing()] == ["stored"]


def test_a_refusal_never_overwrites_a_delivery_since_untracked(db_session, user_owned_bot, attacker_bot, qdrant):
    victims_server(db_session, user_owned_bot)
    raw_id = delivery(db_session, attacker_bot)
    db_session.query(Event).filter_by(id=raw_id).update({"outcome": "not_collected"})
    db_session.commit()

    discord.handle_discord_event(bot_id=attacker_bot.id, event_type="MESSAGE_CREATE", data=message_create(), caused_by=raw_id)

    assert [e.outcome for e in db_session.query(Event).filter_by(kind="raw").populate_existing()] == ["not_collected"]


# --- A push is applied as Discord confirms it ------------------------------
# The bot holding a server signs its own pushes, so a pushed message is stored as the pushing bot's token reads it
# from Discord, a pushed reaction refreshes the reactions Discord lists, and a pushed delete only lands once the
# token of the bot that delivered the message finds it gone.


def held_server_with_a_message(db_session, discord_api, holder, deliverer) -> None:
    """The holder's verified server, with a message Discord holds that the deliverer's gateway stored."""
    victims_server(db_session, holder)
    discord_api.post(message_create())
    handle(deliverer, "MESSAGE_CREATE", message_create(), pushed=False)


def tokens_asked(discord_api) -> set[str]:
    return {r.headers["authorization"].removeprefix("Bot ") for r in discord_api.requests}


def raw_outcomes(db_session) -> list[str]:
    return [e.outcome for e in db_session.query(Event).filter_by(kind="raw").populate_existing()]


@pytest.mark.parametrize("event_type", ["MESSAGE_CREATE", "MESSAGE_UPDATE"])
@pytest.mark.parametrize(
    "answer",
    [UNKNOWN_MESSAGE, UNKNOWN_CHANNEL, discord_error(403, 50001), discord_error(401, 0), {"id": MESSAGE, "content": "x"}],
    ids=["unknown message", "unknown channel", "no access", "token rejected", "not a message"],
)
def test_a_holders_push_of_a_message_discord_does_not_confirm_is_refused(
    db_session, discord_api, webhook_bot, qdrant, event_type, answer
):
    victims_server(db_session, webhook_bot)
    discord_api.channels[(HOOKED_TOKEN, f"{CHANNEL}/messages/{MESSAGE}")] = answer
    raw_id = delivery(db_session, webhook_bot)

    result = discord.handle_discord_event(
        bot_id=webhook_bot.id, event_type=event_type, data=message_create(**PUSHED_AS), caused_by=raw_id
    )

    assert result == {"status": "skipped", "reason": "unconfirmed_message"}
    assert (db_session.query(DiscordMessage).count(), raw_outcomes(db_session)) == (0, ["rejected"])


@pytest.mark.parametrize("event_type", ["MESSAGE_CREATE", "MESSAGE_UPDATE"])
def test_a_pushed_message_is_stored_as_discord_holds_it(db_session, discord_api, webhook_bot, qdrant, event_type):
    victims_server(db_session, webhook_bot)
    discord_api.post(message_create(attachments=[image_attachment(CDN)]))

    with patch.object(discord, "download_and_save_images", return_value=[]) as download:
        handle(webhook_bot, event_type, message_create(**PUSHED_AS, attachments=[image_attachment(f"{CDN}?forged")]))

    message = stored(db_session)
    assert (message.content, message.author_id, message.edited_at) == (LONG_TEXT, int(AUTHOR), None)
    assert [p.contact_info for p in message.people] == [{"discord": AUTHOR}]
    assert db_session.get(DiscordUser, int(FORGER["id"])) is None
    download.assert_called_once_with([CDN], int(MESSAGE))
    assert tokens_asked(discord_api) == {HOOKED_TOKEN}


@pytest.mark.parametrize(
    "edit",
    [
        {"id": MESSAGE, "channel_id": CHANNEL, "content": FORGED_TEXT, "edited_timestamp": FUTURE},
        {"id": MESSAGE, "content": FORGED_TEXT, "edited_timestamp": FUTURE},
        message_create(**PUSHED_AS),
    ],
    ids=["partial", "naming no channel", "full"],
)
def test_a_holders_forged_edit_of_another_owners_message_keeps_what_discord_holds(
    db_session, discord_api, webhook_bot, user_owned_bot, qdrant, edit
):
    held_server_with_a_message(db_session, discord_api, webhook_bot, user_owned_bot)

    handle(webhook_bot, "MESSAGE_UPDATE", edit)

    message = stored(db_session)
    assert (message.content, message.author_id, message.edited_at) == (LONG_TEXT, int(AUTHOR), None)


def test_an_honest_edit_naming_no_channel_is_read_from_the_stored_one(db_session, discord_api, webhook_bot, qdrant):
    victims_server(db_session, webhook_bot)
    relay(discord_api, webhook_bot, "MESSAGE_CREATE", message_create())
    discord_api.post(message_create(content=EDITED, edited_timestamp="2026-11-05T13:00:00+00:00"))

    handle(webhook_bot, "MESSAGE_UPDATE", {"id": MESSAGE, "content": EDITED})

    message = stored(db_session)
    assert (message.content, message.edited_at) == (EDITED, EDITED_AT)


DELETES = [("MESSAGE_DELETE", {"id": MESSAGE, "channel_id": CHANNEL}), ("MESSAGE_DELETE_BULK", {"ids": [MESSAGE], "channel_id": CHANNEL})]


@pytest.mark.parametrize("event_type, data", DELETES)
@pytest.mark.parametrize(
    "holder_reads, deliverer_reads, kept",
    [
        (UNKNOWN_MESSAGE, UNKNOWN_MESSAGE, 0),
        (UNKNOWN_MESSAGE, message_create(), 1),
        (UNKNOWN_MESSAGE, discord_error(403, 50001), 1),
        (UNKNOWN_MESSAGE, discord_error(401, 0), 1),
    ],
    ids=["gone", "the deliverer still reads it", "the deliverer can't reach it", "the deliverer's token is rejected"],
)
def test_a_holders_pushed_delete_is_confirmed_by_the_bot_that_delivered_the_message(
    db_session, discord_api, webhook_bot, user_owned_bot, qdrant, event_type, data, holder_reads, deliverer_reads, kept
):
    held_server_with_a_message(db_session, discord_api, webhook_bot, user_owned_bot)
    discord_api.channels[(HOOKED_TOKEN, f"{CHANNEL}/messages/{MESSAGE}")] = holder_reads
    discord_api.channels[(SECOND_TOKEN, f"{CHANNEL}/messages/{MESSAGE}")] = deliverer_reads

    handle(webhook_bot, event_type, data)

    assert db_session.query(DiscordMessage).count() == kept
    assert tokens_asked(discord_api) == {SECOND_TOKEN}


def store_without_embedding(item, session, **_kwargs) -> dict:
    """process_content_item minus embedding: the sandbox has no Qdrant."""
    session.add(item)
    session.commit()
    return {"status": "processed"}


@pytest.mark.parametrize("event_type, data", DELETES)
@pytest.mark.parametrize("budget, kept, asked", [(0, 1, set()), (1, 0, {SECOND_TOKEN})], ids=["spent", "left"])
def test_a_holders_pushed_deletes_read_another_owners_bot_only_within_budget(
    db_session, discord_api, webhook_bot, user_owned_bot, monkeypatch, event_type, data, budget, kept, asked
):
    monkeypatch.setattr(settings, "PUSH_CROSS_TOKEN_READS_PER_MINUTE", budget)
    monkeypatch.setattr(rate_limit, "rate_limit_redis", lambda: None)
    monkeypatch.setattr(discord, "process_content_item", store_without_embedding)
    monkeypatch.setattr(discord, "clear_item_chunks", lambda item, session: None)
    rate_limit.reset_cache()
    held_server_with_a_message(db_session, discord_api, webhook_bot, user_owned_bot)
    discord_api.requests.clear()
    discord_api.channels[(SECOND_TOKEN, f"{CHANNEL}/messages/{MESSAGE}")] = UNKNOWN_MESSAGE

    handle(webhook_bot, event_type, data)

    assert (db_session.query(DiscordMessage).count(), tokens_asked(discord_api)) == (kept, asked)


def deliverer_gone(db_session, bot) -> None:
    db_session.query(DiscordMessage).update({"bot_id": None})
    db_session.commit()


def deliverer_paused(db_session, bot) -> None:
    bot.is_active = False
    db_session.commit()


@pytest.mark.parametrize("event_type, data", DELETES)
@pytest.mark.parametrize(
    "deliverer, kept, asked",
    [(deliverer_gone, 0, {HOOKED_TOKEN}), (deliverer_paused, 1, set())],
    ids=["its bot is gone: the holder confirms", "its bot is paused: no one confirms"],
)
def test_a_pushed_delete_whose_deliverer_cannot_answer(
    db_session, discord_api, webhook_bot, user_owned_bot, qdrant, event_type, data, deliverer, kept, asked
):
    held_server_with_a_message(db_session, discord_api, webhook_bot, user_owned_bot)
    deliverer(db_session, user_owned_bot)
    discord_api.happen(event_type, data)

    handle(webhook_bot, event_type, data)

    assert (db_session.query(DiscordMessage).count(), tokens_asked(discord_api)) == (kept, asked)


@pytest.mark.parametrize("event_type, data", FORGED[4:], ids=["reaction", "unreaction", "clear", "clear emoji"])
def test_a_holders_forged_reaction_stores_the_reactions_discord_lists(
    db_session, discord_api, webhook_bot, user_owned_bot, qdrant, event_type, data
):
    held_server_with_a_message(db_session, discord_api, webhook_bot, user_owned_bot)
    discord_api.happen("MESSAGE_REACTION_ADD", REACTION)
    handle(user_owned_bot, "MESSAGE_REACTION_ADD", REACTION, pushed=False)

    handle(webhook_bot, event_type, data)

    assert stored(db_session).reactions == FIRE


def test_a_pushed_reaction_reads_every_page_of_users(db_session, discord_api, webhook_bot, qdrant):
    victims_server(db_session, webhook_bot)
    relay(discord_api, webhook_bot, "MESSAGE_CREATE", message_create())
    users = [str(user) for user in range(1, 251)]
    discord_api.reactors[(CHANNEL, MESSAGE)] = {"🔥": users, "party:5": ["7"]}

    handle(webhook_bot, "MESSAGE_REACTION_ADD", REACTION)

    assert stored(db_session).reactions == {"🔥": users, "party:5": ["7"]}


TRANSIENT = [httpx.Response(429, json={"retry_after": 600}), httpx.Response(502), httpx.ConnectError("down")]


@pytest.mark.parametrize(
    "event_type, data, message_id",
    [
        ("MESSAGE_CREATE", message_create(id="62"), "62"),
        ("MESSAGE_UPDATE", message_create(**PUSHED_AS), MESSAGE),
        ("MESSAGE_DELETE", {"id": MESSAGE, "channel_id": CHANNEL}, MESSAGE),
        ("MESSAGE_REACTION_ADD", REACTION, MESSAGE),
    ],
    ids=["create", "edit", "delete", "reaction"],
)
@pytest.mark.parametrize("answer", TRANSIENT, ids=["rate limited", "server error", "connection dropped"])
@pytest.mark.parametrize("retries, countdown", [(0, 60), (2, 180)])
def test_a_push_discord_cannot_confirm_now_is_retried(
    db_session, discord_api, webhook_bot, qdrant, event_type, data, message_id, answer, retries, countdown
):
    victims_server(db_session, webhook_bot)
    discord_api.post(message_create())
    handle(webhook_bot, "MESSAGE_CREATE", message_create(), pushed=False)
    discord_api.channels[(HOOKED_TOKEN, f"{CHANNEL}/messages/{message_id}")] = answer
    task = cast(Task, discord.handle_discord_event)

    with task_request(task, retries=retries), patch.object(task, "retry", side_effect=Retry()) as retry:
        with pytest.raises(Retry):
            task.run(webhook_bot.id, event_type, data)

    retry.assert_called_once_with(exc=ANY, countdown=countdown)
    assert isinstance(retry.call_args.kwargs["exc"], discord.PushNotReady)
    message = stored(db_session)
    assert (db_session.query(DiscordMessage).count(), message.content, message.reactions) == (1, LONG_TEXT, None)


def test_a_pushed_reaction_to_a_message_not_stored_yet_is_retried(db_session, discord_api, webhook_bot, qdrant):
    victims_server(db_session, webhook_bot)
    task = cast(Task, discord.handle_discord_event)

    with task_request(task, retries=0), patch.object(task, "retry", side_effect=Retry()) as retry:
        with pytest.raises(Retry):
            task.run(webhook_bot.id, "MESSAGE_REACTION_ADD", REACTION)

    retry.assert_called_once_with(exc=ANY, countdown=60)
    assert discord_api.requests == []


def test_a_pushed_create_of_a_stored_message_is_not_read_again(db_session, discord_api, webhook_bot, qdrant):
    victims_server(db_session, webhook_bot)
    relay(discord_api, webhook_bot, "MESSAGE_CREATE", message_create())
    discord_api.requests.clear()

    result = handle(webhook_bot, "MESSAGE_CREATE", message_create(**PUSHED_AS))

    assert (result["status"], stored(db_session).content, discord_api.requests) == ("already_exists", LONG_TEXT, [])
