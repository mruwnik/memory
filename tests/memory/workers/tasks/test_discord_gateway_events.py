"""Gateway event payloads (as carried by Discord Webhook Events) stored like collected messages."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from memory.common.db.models import (
    DiscordBot,
    DiscordChannel,
    DiscordMessage,
    DiscordServer,
    DiscordUser,
    HumanUser,
    Person,
)
from memory.common.discord_events import message_task_kwargs
from memory.workers.tasks import discord

GUILD = "900000000000000001"
CHANNEL = "900000000000000002"
AUTHOR = "900000000000000003"
MESSAGE = "900000000000000004"
LONG_TEXT = "a message long enough to be embedded by the pipeline"


@pytest.fixture
def webhook_bot(db_session):
    bot = DiscordBot(application_id=777, name="Hooked", realtime="webhook", public_key="ab" * 32)
    db_session.add(bot)
    db_session.commit()
    return bot


@pytest.fixture
def user_owned_bot(db_session):
    """A second person's integration of the same application."""
    owner = HumanUser(email="second@example.com", name="Second", password_hash="x")
    db_session.add(owner)
    db_session.flush()
    bot = DiscordBot(application_id=777, name="Theirs", realtime="webhook", public_key="cd" * 32, created_by_user_id=owner.id)
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


def handle(bot, event_type, data):
    return discord.handle_discord_event(bot_id=bot.id, event_type=event_type, data=data)


def stored(db_session) -> DiscordMessage:
    db_session.expire_all()
    return db_session.query(DiscordMessage).filter_by(message_id=int(MESSAGE)).one()


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({}, {"message_type": "default", "reply_to_message_id": None, "server_id": int(GUILD)}),
        (
            {"type": 19, "message_reference": {"message_id": "5"}},
            {"message_type": "reply", "reply_to_message_id": 5},
        ),
        ({"type": 7}, {"message_type": "system"}),
        ({"thread": {"id": "6"}}, {"message_type": "thread_starter", "thread_id": 6}),
        ({"guild_id": None}, {"server_id": None}),
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


def test_message_task_kwargs_splits_images_from_attachments():
    kwargs = message_task_kwargs(
        message_create(
            attachments=[
                {"filename": "a.png", "content_type": "image/png", "size": 1, "url": "https://cdn.example.com/a.png"},
                {"filename": "b.pdf", "content_type": "application/pdf", "size": 2, "url": "https://cdn.example.com/b.pdf"},
            ]
        ),
        bot_id=3,
    )

    assert kwargs["images"] == ["https://cdn.example.com/a.png"]
    assert kwargs["attachments"] == [
        {"filename": "b.pdf", "content_type": "application/pdf", "size": 2, "url": "https://cdn.example.com/b.pdf"}
    ]


def test_message_create_stores_message_with_placeholder_server_and_channel(
    db_session, webhook_bot, qdrant
):
    result = handle(webhook_bot, "MESSAGE_CREATE", message_create())

    assert result["status"] == "processed"
    message = stored(db_session)
    assert (message.content, message.bot_id) == (LONG_TEXT, webhook_bot.id)
    server = db_session.get(DiscordServer, int(GUILD))
    channel = db_session.get(DiscordChannel, int(CHANNEL))
    assert (server.name, server.bot_id, server.collect_messages) == (f"Server {GUILD}", webhook_bot.id, True)
    assert (channel.name, channel.channel_type) == (f"Channel {CHANNEL}", "text")
    assert db_session.get(DiscordUser, int(AUTHOR)).display_name == "Alice"


def test_direct_messages_are_not_stored_by_default(db_session, webhook_bot, qdrant):
    result = handle(webhook_bot, "MESSAGE_CREATE", message_create(guild_id=None))

    channel = db_session.get(DiscordChannel, int(CHANNEL))
    assert (channel.channel_type, channel.server_id, channel.should_collect) == ("dm", None, False)
    assert (result["status"], db_session.query(DiscordMessage).count()) == ("skipped", 0)


def test_a_server_switched_off_is_not_stored(db_session, webhook_bot, qdrant):
    db_session.add(DiscordServer(id=int(GUILD), name="Guild", collect_messages=False, bot_id=webhook_bot.id))
    db_session.commit()

    result = handle(webhook_bot, "MESSAGE_CREATE", message_create())

    assert result["status"] == "skipped"
    assert db_session.query(DiscordMessage).count() == 0


def test_message_create_is_idempotent(db_session, webhook_bot, qdrant):
    handle(webhook_bot, "MESSAGE_CREATE", message_create())

    result = handle(webhook_bot, "MESSAGE_CREATE", message_create())

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
def test_message_update_with_content_edits(db_session, webhook_bot, update, qdrant):
    handle(webhook_bot, "MESSAGE_CREATE", message_create())

    handle(webhook_bot, "MESSAGE_UPDATE", update)

    message = stored(db_session)
    assert (message.content, message.edited_at) == (EDITED, EDITED_AT)


def test_message_update_without_content_changes_nothing(db_session, webhook_bot, qdrant):
    handle(webhook_bot, "MESSAGE_CREATE", message_create())

    result = handle(webhook_bot, "MESSAGE_UPDATE", {"id": MESSAGE, "channel_id": CHANNEL, "embeds": []})

    assert result["status"] == "ignored"
    assert stored(db_session).content == LONG_TEXT


def test_an_older_edit_arriving_late_is_ignored(db_session, webhook_bot, qdrant):
    handle(webhook_bot, "MESSAGE_CREATE", message_create())
    handle(webhook_bot, "MESSAGE_UPDATE", message_create(content="newest edit of this message text", edited_timestamp="2026-11-05T14:00:00+00:00"))

    handle(webhook_bot, "MESSAGE_UPDATE", message_create(content="older edit of this message text", edited_timestamp="2026-11-05T13:00:00+00:00"))

    assert stored(db_session).content == "newest edit of this message text"


def test_an_edit_for_an_unseen_message_stores_it(db_session, webhook_bot, qdrant):
    handle(webhook_bot, "MESSAGE_UPDATE", message_create(content=EDITED, edited_timestamp="2026-11-05T13:00:00+00:00"))

    assert stored(db_session).content == EDITED


@pytest.mark.parametrize(
    "event_type, data",
    [
        ("MESSAGE_DELETE", {"id": MESSAGE, "channel_id": CHANNEL}),
        ("MESSAGE_DELETE_BULK", {"ids": [MESSAGE, "1"], "channel_id": CHANNEL}),
    ],
)
def test_deletes_remove_messages(db_session, webhook_bot, event_type, data, qdrant):
    handle(webhook_bot, "MESSAGE_CREATE", message_create())

    handle(webhook_bot, event_type, data)
    handle(webhook_bot, event_type, data)

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
def test_reaction_events(db_session, webhook_bot, events, expected, qdrant):
    handle(webhook_bot, "MESSAGE_CREATE", message_create())

    for event_type, data in events:
        handle(webhook_bot, event_type, data)

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
    handle(webhook_bot, "THREAD_CREATE", {"id": "42", "guild_id": GUILD, "name": "a thread", "type": 11})

    handle(webhook_bot, "THREAD_UPDATE", {"id": "42", "guild_id": GUILD, "parent_id": CHANNEL, "type": 11})

    db_session.expire_all()
    assert db_session.get(DiscordChannel, 42).parent_id == int(CHANNEL)


def test_a_thread_first_stored_as_a_text_channel_becomes_a_thread(db_session, webhook_bot):
    db_session.add(DiscordServer(id=int(GUILD), name="Guild", bot_id=webhook_bot.id, verified=True))
    db_session.flush()
    db_session.add(DiscordChannel(id=42, server_id=int(GUILD), name="Channel 42", channel_type="text"))
    db_session.commit()

    handle(webhook_bot, "THREAD_CREATE", {"id": "42", "guild_id": GUILD, "parent_id": CHANNEL, "type": 11})

    db_session.expire_all()
    assert db_session.get(DiscordChannel, 42).channel_type == "thread"


def test_thread_update_renames(db_session, webhook_bot):
    db_session.add(DiscordServer(id=int(GUILD), name="Guild", bot_id=webhook_bot.id, verified=True))
    db_session.commit()
    handle(webhook_bot, "THREAD_CREATE", {"id": "42", "guild_id": GUILD, "name": "old", "type": 11})

    handle(webhook_bot, "THREAD_UPDATE", {"id": "42", "guild_id": GUILD, "name": "new", "type": 11})

    db_session.expire_all()
    assert db_session.get(DiscordChannel, 42).name == "new"


def test_a_message_two_integrations_see_is_stored_once(db_session, webhook_bot, user_owned_bot, qdrant):
    """Each person's integration delivers what they can see, so shared messages arrive twice."""
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
    bot = DiscordBot(application_id=666, name="Forger", realtime="webhook", public_key="ef" * 32, created_by_user_id=attacker.id)
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
def test_another_owners_bot_cannot_change_a_message(db_session, webhook_bot, attacker_bot, event_type, data, qdrant):
    handle(webhook_bot, "MESSAGE_CREATE", message_create())
    handle(webhook_bot, "MESSAGE_REACTION_ADD", REACTION)

    handle(attacker_bot, event_type, data)

    message = stored(db_session)
    assert (message.content, message.edited_at, message.reactions) == (LONG_TEXT, None, {"🔥": ["11"]})


@pytest.mark.parametrize("event_type, data", FORGED[2:4])
def test_a_bot_of_the_same_owner_can_change_a_message(db_session, event_type, data, qdrant):
    user = HumanUser(email="owner@example.com", name="Owner", password_hash="x")
    db_session.add(user)
    db_session.flush()
    first, second = (
        DiscordBot(application_id=app_id, name=f"bot {app_id}", realtime="webhook", public_key="ab" * 32, created_by_user_id=user.id)
        for app_id in (1001, 1002)
    )
    db_session.add_all([first, second])
    db_session.commit()
    handle(first, "MESSAGE_CREATE", message_create())

    handle(second, event_type, data)

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


def test_a_push_never_links_the_author_to_a_person_by_username(db_session, webhook_bot, qdrant):
    named = Person(identifier="alice", display_name="Alice", contact_info={"discord": "alice"})
    db_session.add(named)
    db_session.commit()

    handle(webhook_bot, "MESSAGE_CREATE", message_create())

    db_session.expire_all()
    author = db_session.get(DiscordUser, int(AUTHOR))
    assert author.person_id is not None
    assert author.person_id != named.id


def test_a_dm_records_the_bot_that_delivered_it(db_session, webhook_bot, attacker_bot, qdrant):
    handle(webhook_bot, "MESSAGE_CREATE", message_create(guild_id=None))
    handle(attacker_bot, "MESSAGE_CREATE", message_create(id="78", guild_id=None))

    db_session.expire_all()
    assert db_session.get(DiscordChannel, int(CHANNEL)).bot_id == webhook_bot.id


def test_custom_emojis_with_one_name_stay_apart(db_session, webhook_bot, qdrant):
    handle(webhook_bot, "MESSAGE_CREATE", message_create())

    for emoji_id in ("1", "2"):
        handle(webhook_bot, "MESSAGE_REACTION_ADD", REACTION | {"emoji": {"id": emoji_id, "name": "party"}})

    assert stored(db_session).reactions == {"party:1": ["11"], "party:2": ["11"]}


def test_a_content_only_update_keeps_the_edit_time(db_session, webhook_bot, qdrant):
    handle(webhook_bot, "MESSAGE_CREATE", message_create(edited_timestamp="2026-11-05T13:00:00+00:00"))

    result = handle(webhook_bot, "MESSAGE_UPDATE", {"id": MESSAGE, "channel_id": CHANNEL, "content": EDITED})

    message = stored(db_session)
    assert result["status"] != "error"
    assert (message.content, message.edited_at) == (EDITED, EDITED_AT)


def test_a_full_update_without_an_edit_time_keeps_the_stored_one(db_session, webhook_bot, qdrant):
    handle(webhook_bot, "MESSAGE_CREATE", message_create(edited_timestamp="2026-11-05T13:00:00+00:00"))

    handle(webhook_bot, "MESSAGE_UPDATE", message_create(content=EDITED))

    assert stored(db_session).edited_at == EDITED_AT



def test_a_squatted_server_gives_its_pusher_nothing(db_session, webhook_bot, attacker_bot, qdrant):
    """Whoever pushes a guild id first creates the row, but owns nothing in it."""
    handle(attacker_bot, "MESSAGE_CREATE", message_create(id="70", author={"id": "5", "username": "x"}))
    handle(webhook_bot, "MESSAGE_CREATE", message_create())

    handle(attacker_bot, "MESSAGE_DELETE", {"id": MESSAGE, "channel_id": CHANNEL})
    handle(attacker_bot, "THREAD_UPDATE", {"id": CHANNEL, "guild_id": GUILD, "name": "renamed", "type": 11})

    db_session.expire_all()
    server = db_session.get(DiscordServer, int(GUILD))
    assert (server.bot_id, server.verified) == (attacker_bot.id, False)
    assert db_session.query(DiscordMessage).filter_by(message_id=int(MESSAGE)).count() == 1
    assert db_session.get(DiscordChannel, int(CHANNEL)).name == f"Channel {CHANNEL}"
