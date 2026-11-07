from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from memory.common import notify_delivery
from memory.common.db.models.discord import DiscordChannel, DiscordServer
from memory.workers.tasks import notifications


def make_notification_params(channel, target, **kw):
    return notify_delivery.NotificationParams(
        notification_channel=channel,
        notification_target=target,
        message=kw.get("message", "hello"),
        user_id=kw.get("user_id", 1),
        topic=kw.get("topic"),
        data=kw.get("data", {}),
    )


@contextmanager
def null_session():
    yield None


# --- delivery-method derivation ---


@pytest.mark.parametrize(
    "target,is_dm",
    [
        ("U12345", True),
        ("W12345", True),
        ("C12345", False),
        ("G12345", False),
        ("D12345", False),
        ("u-lowercase-ok", True),
    ],
)
def test_slack_target_is_dm(target, is_dm):
    assert notify_delivery.slack_target_is_dm(target) is is_dm


@pytest.mark.parametrize(
    "target,is_channel",
    [
        ("424242", True),  # known channel id (created below)
        (424242, True),  # int snowflake (celery JSON boundary) ⇒ same answer
        ("999999", False),  # unknown id ⇒ treated as user DM
        (999999, False),  # int unknown id ⇒ user DM, not a crash
        ("not-a-number", False),
    ],
)
def test_discord_target_is_channel(db_session, target, is_channel):
    server = DiscordServer(id=700, name="G", collect_messages=False)
    db_session.add(server)
    db_session.add(
        DiscordChannel(id=424242, server_id=700, name="general", channel_type="text")
    )
    db_session.commit()
    assert notify_delivery.discord_target_is_channel(db_session, target) is is_channel


def test_send_via_discord_routes_to_channel(monkeypatch):
    calls = {}
    monkeypatch.setattr(notify_delivery, "make_session", null_session)
    monkeypatch.setattr(notify_delivery, "resolve_discord_bot_id", lambda s, p: 7)
    monkeypatch.setattr(notify_delivery, "discord_target_is_channel", lambda s, t: True)
    monkeypatch.setattr(
        notify_delivery.discord_utils,
        "send_to_channel",
        lambda bot, target, msg: calls.update(channel=(bot, target, msg)) or True,
    )
    monkeypatch.setattr(
        notify_delivery.discord_utils,
        "send_dm",
        lambda *a: calls.update(dm=a) or True,
    )
    assert (
        notify_delivery.send_via_discord(make_notification_params("discord", "555"))
        is True
    )
    assert calls["channel"] == (7, "555", "hello")
    assert "dm" not in calls


def test_send_via_discord_routes_to_dm(monkeypatch):
    calls = {}
    monkeypatch.setattr(notify_delivery, "make_session", null_session)
    monkeypatch.setattr(notify_delivery, "resolve_discord_bot_id", lambda s, p: 7)
    monkeypatch.setattr(
        notify_delivery, "discord_target_is_channel", lambda s, t: False
    )
    monkeypatch.setattr(
        notify_delivery.discord_utils,
        "send_to_channel",
        lambda *a: calls.update(channel=a) or True,
    )
    monkeypatch.setattr(
        notify_delivery.discord_utils,
        "send_dm",
        lambda bot, target, msg: calls.update(dm=(bot, target, msg)) or True,
    )
    assert (
        notify_delivery.send_via_discord(make_notification_params("discord", "778899"))
        is True
    )
    assert calls["dm"] == (7, "778899", "hello")
    assert "channel" not in calls


def test_send_via_discord_normalizes_int_target(monkeypatch):
    # An int snowflake target (raw from serialize() across the celery JSON
    # boundary) must not crash and must reach the collector API as a str.
    calls = {}
    monkeypatch.setattr(notify_delivery, "make_session", null_session)
    monkeypatch.setattr(notify_delivery, "resolve_discord_bot_id", lambda s, p: 7)
    monkeypatch.setattr(
        notify_delivery, "discord_target_is_channel", lambda s, t: False
    )
    monkeypatch.setattr(
        notify_delivery.discord_utils,
        "send_dm",
        lambda bot, target, msg: calls.update(dm=(bot, target, msg)) or True,
    )
    assert (
        notify_delivery.send_via_discord(make_notification_params("discord", 778899))
        is True
    )
    assert calls["dm"] == (7, "778899", "hello")


@pytest.mark.parametrize(
    "target,expect_open",
    [("U777", True), ("C500", False)],
)
def test_send_via_slack_routes(monkeypatch, target, expect_open):
    methods = []

    creds = Mock(access_token="tok")
    sess = Mock()
    sess.query.return_value.filter.return_value.first.return_value = creds

    @contextmanager
    def fake_session():
        yield sess

    async def fake_call(token, method, **kw):
        methods.append((method, kw))
        if method == "conversations.open":
            return {"channel": {"id": "D-opened"}}
        return {"ok": True}

    monkeypatch.setattr(notify_delivery, "make_session", fake_session)
    monkeypatch.setattr(notify_delivery, "async_slack_call", fake_call)
    monkeypatch.setattr(notify_delivery, "slack_api_base_url", lambda app: "https://slack.com/api")

    assert (
        notify_delivery.send_via_slack(make_notification_params("slack", target))
        is True
    )

    called = [m for m, _ in methods]
    assert ("conversations.open" in called) is expect_open
    post = next(kw for m, kw in methods if m == "chat.postMessage")
    assert post["channel"] == ("D-opened" if expect_open else target)


@pytest.mark.parametrize(
    "channel, sender",
    [("discord", "send_via_discord"), ("slack", "send_via_slack"), ("email", "send_via_email")],
)
def test_deliver_notification_routes_by_channel(monkeypatch, channel, sender):
    sent = []
    for name in ("send_via_discord", "send_via_slack", "send_via_email"):
        monkeypatch.setattr(notify_delivery, name, lambda p, name=name: sent.append(name) or True)

    assert notify_delivery.deliver_notification(make_notification_params(channel, "x")) is True
    assert sent == [sender]


def test_deliver_notification_refuses_an_unknown_channel():
    assert notify_delivery.deliver_notification(make_notification_params("sms", "1")) is False


def test_send_notification_task_delivers_its_arguments(monkeypatch):
    delivered = []
    monkeypatch.setattr(notifications, "deliver_notification", lambda p: delivered.append(p) or True)

    result = notifications.send_notification(
        channel="discord",
        target="123456789",
        message="**Hi**\n\nthere",
        user_id=5,
        topic="Hi",
        data={"discord_bot_id": 7},
    )

    assert result == {"sent": True}
    assert delivered == [make_notification_params(
        "discord", "123456789", message="**Hi**\n\nthere", user_id=5, topic="Hi",
        data={"discord_bot_id": 7},
    )]
