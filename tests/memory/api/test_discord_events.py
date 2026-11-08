"""Discord Webhook Events receiver: POST /discord/events/{bot_id}."""

import json
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from memory.common.celery_app import HANDLE_DISCORD_EVENT
from memory.common.db.models import DiscordBot, DiscordBotServer, DiscordChannel, DiscordMessage, DiscordServer, DiscordUser
from tests.conftest import raw_events

SIGNING_KEY = Ed25519PrivateKey.generate()
OTHER_KEY = Ed25519PrivateKey.generate()


def public_hex(key: Ed25519PrivateKey) -> str:
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


@pytest.fixture
def webhook_bot(db_session, user):
    bot = DiscordBot(
        application_id=123456789012345678,
        name="Hooked",
        created_by_user_id=user.id,
        realtime="webhook",
        public_key=public_hex(SIGNING_KEY),
    )
    db_session.add(bot)
    db_session.commit()
    return bot


def post_event(client, bot_id, envelope, key=SIGNING_KEY, timestamp=None):
    body = json.dumps(envelope).encode()
    ts = str(int(time.time()) if timestamp is None else timestamp)
    signature = key.sign(ts.encode() + body).hex()
    return client.post(
        f"/discord/events/{bot_id}",
        content=body,
        headers={
            "X-Signature-Ed25519": signature,
            "X-Signature-Timestamp": ts,
            "content-type": "application/json",
        },
    )


@pytest.fixture
def collected_channel(db_session, webhook_bot):
    """A DM of the bot's: events about it need no server."""
    db_session.add(DiscordChannel(id=2, name="loud", channel_type="dm", bot_id=webhook_bot.id, collect_messages=True))
    db_session.commit()


def event(event_type: str, data: dict) -> dict:
    return {
        "version": 1,
        "application_id": "123456789012345678",
        "type": 1,
        "event": {"type": event_type, "timestamp": "2026-11-05T12:00:00Z", "data": data},
    }


PING = {"version": 1, "application_id": "123456789012345678", "type": 0}


def test_ping_gets_an_empty_204(client, webhook_bot):
    response = post_event(client, webhook_bot.id, PING)

    assert (response.status_code, response.content) == (204, b"")


@pytest.mark.parametrize(
    "event_type",
    [
        "MESSAGE_CREATE",
        "MESSAGE_UPDATE",
        "MESSAGE_DELETE",
        "MESSAGE_DELETE_BULK",
        "MESSAGE_REACTION_ADD",
        "MESSAGE_REACTION_REMOVE",
        "MESSAGE_REACTION_REMOVE_ALL",
        "MESSAGE_REACTION_REMOVE_EMOJI",
        "THREAD_CREATE",
        "THREAD_UPDATE",
    ],
)
def test_carried_events_are_queued(client, db_session, webhook_bot, collected_channel, event_type):
    data = {"id": "2", "channel_id": "2"}
    with patch("memory.api.discord.celery_app.send_task") as send:
        response = post_event(client, webhook_bot.id, event(event_type, data))

    assert (response.status_code, response.content) == (204, b"")
    (raw,) = raw_events(db_session)
    assert (raw.source, raw.type, raw.account_id, raw.owner_id, raw.container, raw.outcome) == (
        "discord", event_type, webhook_bot.id, webhook_bot.created_by_user_id, "2", "stored",
    )
    assert raw.payload is None
    send.assert_called_once_with(
        HANDLE_DISCORD_EVENT,
        kwargs={"bot_id": webhook_bot.id, "event_type": event_type, "data": data, "caused_by": raw.id, "pushed": True},
    )


OFF, ON, NEW, THREAD_UNDER_OFF, DM_OFF, THEIRS_OFF, THEIR_DM_OFF, THEIRS_ON = "2", "5", "3", "4", "6", "12", "13", "14"
KNOWN, STRANGER = "70", "71"


@pytest.mark.parametrize(
    "event_type, data, outcome, queued, container",
    [
        ("MESSAGE_CREATE", {"channel_id": OFF, "guild_id": "7"}, "not_collected", 0, None),
        ("MESSAGE_CREATE", {"channel_id": ON, "guild_id": "7"}, "stored", 1, ON),
        # Channels not stored yet follow their server; a DM is collected from someone sharing a server with the bot.
        ("MESSAGE_CREATE", {"channel_id": NEW, "guild_id": "7"}, "stored", 1, NEW),
        ("MESSAGE_CREATE", {"channel_id": NEW, "guild_id": "9"}, "not_collected", 0, None),
        # Of a channel the pusher may not push into it learns nothing: it is taken for one not stored yet.
        ("MESSAGE_CREATE", {"channel_id": THEIRS_OFF, "guild_id": "10"}, "stored", 1, THEIRS_OFF),
        ("MESSAGE_CREATE", {"channel_id": NEW, "guild_id": "10"}, "stored", 1, NEW),
        ("MESSAGE_CREATE", {"channel_id": THEIR_DM_OFF, "author": {"id": KNOWN}}, "stored", 1, THEIR_DM_OFF),
        ("MESSAGE_CREATE", {"channel_id": THEIR_DM_OFF, "author": {"id": STRANGER}}, "not_collected", 0, None),
        ("MESSAGE_CREATE", {"channel_id": THEIRS_OFF, "author": {"id": STRANGER}}, "not_collected", 0, None),
        ("MESSAGE_REACTION_ADD", {"channel_id": THEIRS_OFF, "guild_id": "10", "message_id": "1"}, "stored", 1, THEIRS_OFF),
        ("THREAD_CREATE", {"id": "52", "parent_id": THEIRS_OFF}, "not_collected", 0, None),
        ("THREAD_CREATE", {"id": "53", "parent_id": THEIRS_ON}, "not_collected", 0, None),
        ("THREAD_CREATE", {"id": "54", "parent_id": "99"}, "not_collected", 0, None),
        ("THREAD_CREATE", {"id": "55", "parent_id": THEIRS_OFF, "guild_id": "10"}, "stored", 1, "55"),
        ("MESSAGE_CREATE", {"channel_id": NEW, "author": {"id": KNOWN}}, "stored", 1, NEW),
        ("MESSAGE_CREATE", {"channel_id": NEW, "author": {"id": STRANGER}}, "not_collected", 0, None),
        ("MESSAGE_CREATE", {"channel_id": NEW}, "not_collected", 0, None),
        ("MESSAGE_CREATE", {"channel_id": DM_OFF, "author": {"id": KNOWN}}, "not_collected", 0, None),
        # A push-only bot stores a server it first hears of as collected.
        ("MESSAGE_CREATE", {"channel_id": NEW, "guild_id": "8"}, "stored", 1, NEW),
        # Threads follow their parent channel before their server.
        ("MESSAGE_CREATE", {"channel_id": THREAD_UNDER_OFF, "guild_id": "7"}, "not_collected", 0, None),
        ("THREAD_CREATE", {"id": "50", "parent_id": OFF, "guild_id": "7"}, "not_collected", 0, None),
        ("THREAD_UPDATE", {"id": THREAD_UNDER_OFF, "parent_id": OFF, "guild_id": "7"}, "not_collected", 0, None),
        ("THREAD_CREATE", {"id": "51", "parent_id": ON, "guild_id": "7"}, "stored", 1, "51"),
        # Deletes still drop Memory's copy; an untracked channel's leave a stub like any other.
        ("MESSAGE_DELETE", {"channel_id": OFF, "guild_id": "7"}, "not_collected", 1, None),
        ("MESSAGE_DELETE_BULK", {"ids": ["1"], "channel_id": DM_OFF}, "not_collected", 1, None),
        ("MESSAGE_DELETE", {"channel_id": ON, "guild_id": "7"}, "stored", 1, ON),
        # Events with no item type are gated by their channel too.
        ("TYPING_START", {"channel_id": OFF, "guild_id": "7"}, "not_collected", 0, None),
        ("TYPING_START", {"channel_id": DM_OFF}, "not_collected", 0, None),
    ],
)
def test_only_events_in_collected_channels_are_queued(
    client, db_session, webhook_bot, event_type, data, outcome, queued, container
):
    other = DiscordBot(application_id=5, name="Theirs", realtime="webhook", public_key=public_hex(OTHER_KEY))
    db_session.add(other)
    db_session.flush()
    db_session.add_all([
        DiscordServer(id=7, name="S", bot_id=webhook_bot.id, collect_messages=True),
        DiscordServer(id=9, name="Q", bot_id=webhook_bot.id),
        DiscordServer(id=10, name="Theirs", bot_id=other.id, verified=True, collect_messages=False),
    ])
    db_session.flush()
    db_session.add_all([
        DiscordChannel(id=int(OFF), name="off", channel_type="text", server_id=7, collect_messages=False),
        DiscordChannel(id=int(ON), name="on", channel_type="text", server_id=7),
        DiscordChannel(id=int(THREAD_UNDER_OFF), name="t", channel_type="thread", server_id=7, parent_id=int(OFF)),
        DiscordChannel(id=int(DM_OFF), name="dm", channel_type="dm", collect_messages=False, bot_id=webhook_bot.id),
        DiscordChannel(id=int(THEIRS_OFF), name="off", channel_type="text", server_id=10, collect_messages=False),
        DiscordChannel(id=int(THEIRS_ON), name="on", channel_type="text", server_id=10, collect_messages=True),
        DiscordChannel(id=int(THEIR_DM_OFF), name="dm", channel_type="dm", collect_messages=False, bot_id=other.id),
        DiscordBotServer(bot_id=webhook_bot.id, server_id=7),
        DiscordUser(id=int(KNOWN), username="known"),
    ])
    db_session.flush()
    db_session.add(DiscordMessage(
        modality="message", sha256=b"\x03" * 32, content="hi", message_id=80, channel_id=int(ON),
        server_id=7, author_id=int(KNOWN), sent_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    ))
    db_session.commit()

    with patch("memory.api.discord.celery_app.send_task") as send:
        post_event(client, webhook_bot.id, event(event_type, {"id": "1", **data}))

    assert [(e.container, e.outcome, e.payload) for e in raw_events(db_session)] == [(container, outcome, None)]
    assert [c.kwargs["kwargs"]["data"] for c in send.call_args_list] == [{"id": "1", **data}] * queued


@pytest.mark.parametrize(
    "data",
    [
        {"channel_id": "2", "author": "alice"},
        {"channel_id": "2", "guild_id": {"id": "7"}},
        {"id": "50", "parent_id": ["2"]},
        {"channel_id": "1" * 40},
        {"channel_id": "2", "ids": ["x"]},
        {"channel_id": "2", "message_reference": "1"},
        {"channel_id": "2", "content": {"text": "hi"}},
    ],
)
def test_a_signed_payload_of_the_wrong_shape_gets_400(client, db_session, webhook_bot, data):
    with patch("memory.api.discord.celery_app.send_task") as send:
        response = post_event(client, webhook_bot.id, event("MESSAGE_CREATE", {"id": "1", **data}))

    assert response.status_code == 400
    send.assert_not_called()
    assert raw_events(db_session) == []


@pytest.mark.parametrize(
    "body",
    [
        '{"version": 1, "application_id": "123456789012345678", "type": 1, '
        '"event": {"type": "TYPING_START", "data": {"channel_id": "2", "nonce": "a\\u0000b"}}}',
        '{"version": 1, "application_id": "123456789012345678", "type": 1, '
        '"event": {"type": "TYPING_START", "data": {"channel_id": "2", "nonce": NaN}}}',
        '{"version": 1, "application_id": "123456789012345678", "type": 1, '
        '"event": {"type": "TYPING\\u0000", "data": {"channel_id": "2", "nonce": Infinity}}}',
        '{"version": 1, "application_id": "123456789012345678", "type": 1, '
        '"event": {"type": "MESSAGE_CREATE", "data": {"channel_id": "2", "content": "\\ud800"}}}',
        '{"version": 1, "application_id": "123456789012345678", "type": 1, '
        '"event": {"type": "TYPING_START", "data": {"channel_id": "2", "x": ' + '{"a": ' * 1500 + "1" + "}" * 1500 + "}}}",
    ],
    ids=["nul", "nan", "infinity", "lone surrogate", "nested deeper than Python recurses"],
)
def test_a_body_json_columns_cannot_hold_gets_400(client, db_session, webhook_bot, body):
    ts = str(int(time.time()))
    response = client.post(
        f"/discord/events/{webhook_bot.id}",
        content=body.encode(),
        headers={"X-Signature-Ed25519": SIGNING_KEY.sign(ts.encode() + body.encode()).hex(), "X-Signature-Timestamp": ts},
    )

    assert response.status_code == 400
    assert raw_events(db_session) == []


def test_an_event_with_no_item_type_in_a_collected_channel_keeps_its_payload(
    client, db_session, webhook_bot, collected_channel
):
    envelope = event("TYPING_START", {"channel_id": "2", "user_id": "9"})
    with patch("memory.api.discord.celery_app.send_task") as send:
        post_event(client, webhook_bot.id, envelope)

    send.assert_not_called()
    assert [(e.outcome, e.payload) for e in raw_events(db_session)] == [("no_item_type", envelope)]


def test_a_delivery_that_could_not_be_queued_is_taken_on_retry(client, db_session, webhook_bot, collected_channel):
    envelope = event("MESSAGE_DELETE", {"id": "1", "channel_id": "2"})
    with patch("memory.api.discord.celery_app.send_task", side_effect=[ConnectionError("broker down"), None]) as send:
        failed = post_event(client, webhook_bot.id, envelope)
        retried = post_event(client, webhook_bot.id, envelope)

    assert (failed.status_code, retried.status_code, send.call_count) == (500, 204, 2)
    assert [e.outcome for e in raw_events(db_session)] == ["stored"]


@pytest.mark.parametrize(
    "envelope",
    [
        event("APPLICATION_AUTHORIZED", {"user": {"id": "1"}}),
        event("MESSAGE_POLL_VOTE_ADD", {"user_id": "1"}),
        {"version": 1, "application_id": "123456789012345678", "type": 7},
        PING,
    ],
)
def test_other_events_are_acknowledged_and_ignored(client, db_session, webhook_bot, envelope):
    with patch("memory.api.discord.celery_app.send_task") as send:
        response = post_event(client, webhook_bot.id, envelope)

    assert response.status_code == 204
    send.assert_not_called()
    assert [(e.outcome, e.payload) for e in raw_events(db_session)] == [("no_item_type", envelope)]


@pytest.mark.parametrize(
    "key, timestamp_offset",
    [
        (OTHER_KEY, 0),
        (SIGNING_KEY, -301),
        (SIGNING_KEY, 301),
    ],
)
def test_bad_signatures_and_stale_timestamps_get_401(client, db_session, webhook_bot, key, timestamp_offset):
    with patch("memory.api.discord.celery_app.send_task") as send:
        response = post_event(
            client, webhook_bot.id, PING, key=key, timestamp=int(time.time()) + timestamp_offset
        )

    assert response.status_code == 401
    send.assert_not_called()
    assert raw_events(db_session) == []


def test_signature_covers_the_body(client, webhook_bot):
    ts = str(int(time.time()))
    signature = SIGNING_KEY.sign(ts.encode() + json.dumps(PING).encode()).hex()

    response = client.post(
        f"/discord/events/{webhook_bot.id}",
        content=json.dumps(event("MESSAGE_DELETE", {"id": "1"})).encode(),
        headers={"X-Signature-Ed25519": signature, "X-Signature-Timestamp": ts},
    )

    assert response.status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Signature-Ed25519": "zz", "X-Signature-Timestamp": "1"},
        {"X-Signature-Ed25519": "ab" * 64, "X-Signature-Timestamp": "not-a-number"},
    ],
)
def test_malformed_signature_headers_get_401(client, webhook_bot, headers):
    response = client.post(f"/discord/events/{webhook_bot.id}", content=b"{}", headers=headers)

    assert response.status_code == 401


@pytest.mark.parametrize(
    "change",
    [
        {"realtime": "gateway"},
        {"is_active": False},
    ],
)
def test_bots_that_cannot_receive_events_get_401(client, db_session, webhook_bot, change):
    for field, value in change.items():
        setattr(webhook_bot, field, value)
    db_session.commit()

    assert post_event(client, webhook_bot.id, PING).status_code == 401


@pytest.mark.parametrize("application_id", ["999", None])
def test_events_for_another_application_get_401(client, db_session, webhook_bot, application_id):
    with patch("memory.api.discord.celery_app.send_task") as send:
        response = post_event(
            client,
            webhook_bot.id,
            event("MESSAGE_DELETE", {"id": "1"}) | {"application_id": application_id},
        )

    assert response.status_code == 401
    send.assert_not_called()
    assert raw_events(db_session) == []


def test_setup_check_pings_accepted_then_badly_signed_rejected(client, webhook_bot):
    good = post_event(client, webhook_bot.id, PING)
    bad = post_event(client, webhook_bot.id, PING, key=OTHER_KEY)

    assert (good.status_code, bad.status_code) == (204, 401)


def test_a_replayed_delivery_is_acknowledged_once(client, db_session, webhook_bot, collected_channel):
    envelope = event("MESSAGE_DELETE", {"id": "31337", "channel_id": "2"})
    with patch("memory.api.discord.celery_app.send_task") as send:
        first = post_event(client, webhook_bot.id, envelope)
        second = post_event(client, webhook_bot.id, envelope)

    assert (first.status_code, second.status_code, send.call_count) == (204, 204, 1)
    assert len(raw_events(db_session)) == 1


def test_unknown_bot_gets_401(client, webhook_bot):
    assert post_event(client, webhook_bot.id + 1000, PING).status_code == 401


def test_event_records_when_it_arrived(client, db_session, webhook_bot):
    with patch("memory.api.discord.celery_app.send_task"):
        post_event(client, webhook_bot.id, event("MESSAGE_DELETE", {"id": "1", "channel_id": "2"}))

    db_session.refresh(webhook_bot)
    assert webhook_bot.last_event_at is not None



@pytest.mark.parametrize(
    "event_type, data, links_items",
    [
        ("MESSAGE_CREATE", {"id": "9", "channel_id": "2"}, True),
        ("MESSAGE_UPDATE", {"id": "9", "channel_id": "2", "content": "edited"}, True),
        # An embed-only update leaves the stored message as it was.
        ("MESSAGE_UPDATE", {"id": "9", "channel_id": "2"}, False),
        ("MESSAGE_DELETE", {"id": "9", "channel_id": "2"}, True),
        ("MESSAGE_DELETE_BULK", {"ids": ["9"], "channel_id": "2"}, True),
        ("MESSAGE_REACTION_ADD", {"message_id": "9", "channel_id": "2"}, False),
        ("THREAD_CREATE", {"id": "2"}, False),
        ("THREAD_UPDATE", {"id": "2"}, False),
    ],
)
def test_a_raw_event_says_whether_its_task_writes_item_events(
    client, db_session, webhook_bot, collected_channel, event_type, data, links_items
):
    with patch("memory.api.discord.celery_app.send_task"):
        post_event(client, webhook_bot.id, event(event_type, data))

    (raw,) = raw_events(db_session)
    assert bool((raw.data or {}).get("links_items")) is links_items
