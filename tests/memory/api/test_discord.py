import base64
from unittest.mock import patch

import pytest

from memory.common.db.models import (
    DiscordBot,
    DiscordBotServer,
    DiscordChannel,
    DiscordMessage,
    DiscordServer,
    DiscordUser,
    HumanUser,
)
from memory.common.db.models.sources import Project


@pytest.mark.parametrize(
    "body, keeps_project",
    [({"project_id": None}, False), ({"sensitivity": "internal"}, True)],
)
@pytest.mark.parametrize("target", ["servers", "channels"])
def test_update_changes_only_the_fields_sent(
    client, db_session, user, target, body, keeps_project
):
    project = Project(title="Discord project", state="open")
    db_session.add(project)
    db_session.flush()
    bot = DiscordBot(id=11111, application_id=11111, name="Bot")
    bot.created_by_user_id = user.id
    server = DiscordServer(
        id=44444, name="Guild", bot=bot, project_id=project.id, collect_messages=True, verified=True
    )
    channel = DiscordChannel(
        id=33333, name="general", channel_type="text", server=server,
        project_id=project.id, collect_messages=False,
    )
    db_session.add_all([bot, server, channel])
    db_session.commit()
    row = {"servers": server, "channels": channel}[target]
    collecting = row.collect_messages

    response = client.patch(f"/discord/{target}/{row.id}", json=body)

    assert response.status_code == 200
    db_session.refresh(row)
    assert (row.project_id is not None) is keeps_project
    assert row.collect_messages is collecting


@pytest.mark.parametrize("method,path", [
    ("get", "/discord/bots/11111/users"),
    ("post", "/discord/bots/11111/users"),
    ("delete", "/discord/bots/11111/users/2"),
])
def test_bot_user_management_routes_removed(client, method, path):
    assert getattr(client, method)(path).status_code == 404


def gateway_token(application_id: int) -> str:
    return base64.b64encode(str(application_id).encode()).decode() + ".timestamp.signature"


PUBLIC_KEY = "ab" * 32


def test_registering_another_users_application_makes_a_separate_bot(client, db_session, user):
    owner = HumanUser.create_with_password(email="owner@example.com", name="Owner", password="password123")
    db_session.add(owner)
    db_session.flush()
    bot = DiscordBot(id=11111, application_id=11111, name="Owner's bot", created_by_user_id=owner.id)
    db_session.add(bot)
    db_session.commit()

    response = client.post("/discord/bots", json={"name": "Mine", "token": gateway_token(11111)})

    assert response.status_code == 200
    body = response.json()
    assert (body["application_id"], body["id"] != "11111") == ("11111", True)
    db_session.refresh(bot)
    assert (bot.created_by_user_id, bot.is_authorized(user)) == (owner.id, False)


def webhook_bot_body(application_id: int, public_key: str = PUBLIC_KEY) -> dict:
    return {"token": gateway_token(application_id), "realtime": "webhook", "public_key": public_key}


@pytest.mark.parametrize(
    "registration",
    [
        {"token": gateway_token(22222)},
        webhook_bot_body(22222),
    ],
)
def test_registering_the_same_application_twice_returns_the_bot(client, db_session, user, registration):
    one = client.post("/discord/bots", json={"name": "A"} | registration).json()
    two = client.post("/discord/bots", json={"name": "B"} | registration).json()

    assert one["id"] == two["id"]
    assert db_session.query(DiscordBot).filter_by(application_id=22222).count() == 1


def test_registering_again_applies_the_new_mode_key_and_token(client, db_session, user):
    first = client.post("/discord/bots", json={"name": "A", "token": gateway_token(22223)}).json()

    again = client.post("/discord/bots", json={"name": "A"} | webhook_bot_body(22223, "cd" * 32)).json()

    bot = db_session.get(DiscordBot, int(first["id"]))
    db_session.refresh(bot)
    assert (again["id"], bot.realtime, bot.public_key) == (first["id"], "webhook", "cd" * 32)


def test_application_id_must_match_the_token(client, db_session, user):
    response = client.post(
        "/discord/bots",
        json={"name": "A", "application_id": "33334"} | webhook_bot_body(44445),
    )

    assert response.status_code == 400


def test_a_webhook_bot_shows_its_events_url(client, db_session, user):
    response = client.post("/discord/bots", json={"name": "Hooked"} | webhook_bot_body(33333))

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["realtime"], body["has_token"], body["public_key"]) == ("webhook", True, PUBLIC_KEY)
    assert body["api_base_url"] == "https://discord.com/api/v10"
    assert body["events_url"].endswith(f"/discord/events/{body['id']}")
    bot = db_session.get(DiscordBot, int(body["id"]))
    assert (bot.application_id, bot.runs_gateway) == (33333, False)


def test_a_bot_runs_the_gateway_by_default(client, db_session, user):
    body = client.post("/discord/bots", json={"name": "G", "token": gateway_token(44444)}).json()

    assert (body["realtime"], body["has_token"]) == ("gateway", True)
    assert db_session.get(DiscordBot, int(body["id"])).runs_gateway is True


@pytest.mark.parametrize(
    "body",
    [
        {"name": "x"},
        {"name": "x", "application_id": "55555", "public_key": PUBLIC_KEY},
        {"name": "x", "application_id": "55555", "realtime": "webhook", "public_key": PUBLIC_KEY},
        {"name": "x", "token": gateway_token(55555), "realtime": "webhook"},
        {"name": "x", "token": gateway_token(55555), "realtime": "carrier-pigeon"},
        {"name": "x"} | webhook_bot_body(55555, "not-hex"),
        {"name": "x"} | webhook_bot_body(55555, "ab" * 31),
        {"name": "x", "application_id": "not-a-number", "token": gateway_token(55555)},
        {"name": "x", "token": gateway_token(55555), "api_base_url": "http://x.example.com"},
        {"name": "x", "token": "garbage"},
    ],
)
def test_bot_registration_rejects_bad_input(client, db_session, user, body):
    response = client.post("/discord/bots", json=body)

    assert response.status_code in (400, 422)
    assert db_session.query(DiscordBot).count() == 0


def test_patch_switches_a_bot_to_webhook_and_back(client, db_session, user):
    bot_id = client.post("/discord/bots", json={"name": "G", "token": gateway_token(66666)}).json()["id"]

    hooked = client.patch(
        f"/discord/bots/{bot_id}",
        json={"realtime": "webhook", "public_key": PUBLIC_KEY, "api_base_url": "https://chat.example.com/discord/api/v10"},
    ).json()
    back = client.patch(f"/discord/bots/{bot_id}", json={"realtime": "gateway"}).json()

    assert (hooked["realtime"], hooked["public_key"], hooked["api_base_url"]) == (
        "webhook", PUBLIC_KEY, "https://chat.example.com/discord/api/v10",
    )
    assert hooked["events_url"].endswith(f"/discord/events/{bot_id}")
    assert (back["realtime"], back["public_key"]) == ("gateway", PUBLIC_KEY)


def test_patch_refuses_webhook_mode_without_a_public_key(client, db_session, user):
    bot_id = client.post("/discord/bots", json={"name": "G", "token": gateway_token(66670)}).json()["id"]

    response = client.patch(f"/discord/bots/{bot_id}", json={"realtime": "webhook"})

    assert response.status_code == 400
    assert db_session.get(DiscordBot, int(bot_id)).realtime == "gateway"


def test_a_tokenless_bot_resumes_only_with_a_token(client, db_session, user):
    bot = DiscordBot(application_id=77778, name="Old", created_by_user_id=user.id, is_active=False)
    db_session.add(bot)
    db_session.commit()

    refused = client.patch(f"/discord/bots/{bot.id}", json={"is_active": True})
    resumed = client.patch(f"/discord/bots/{bot.id}", json={"token": gateway_token(77778)})

    assert (refused.status_code, resumed.status_code, resumed.json()["is_active"]) == (400, 200, True)


def test_a_new_token_leaves_a_paused_bot_paused(client, db_session, user):
    bot_id = client.post("/discord/bots", json={"name": "G", "token": gateway_token(77779)}).json()["id"]
    client.patch(f"/discord/bots/{bot_id}", json={"is_active": False})

    body = client.patch(f"/discord/bots/{bot_id}", json={"token": gateway_token(77779)}).json()

    assert body["is_active"] is False


@pytest.mark.parametrize("realtime, expected", [(None, "webhook"), ("gateway", "gateway")])
def test_registering_again_changes_the_mode_only_when_asked(client, db_session, user, realtime, expected):
    client.post("/discord/bots", json={"name": "A"} | webhook_bot_body(22224))
    again = {"name": "A", "token": gateway_token(22224)} | ({"realtime": realtime} if realtime else {})

    body = client.post("/discord/bots", json=again).json()

    assert body["realtime"] == expected


def test_members_cannot_point_a_bot_outside_the_allowlist(member_client, db_session, user):
    with patch("memory.common.chat_urls.settings.CHAT_API_ALLOWED_ORIGINS", []):
        created = member_client.post(
            "/discord/bots",
            json={"name": "x", "token": gateway_token(88888), "api_base_url": "https://chat.example.com/api"},
        )
        bot_id = member_client.post("/discord/bots", json={"name": "G", "token": gateway_token(66667)}).json()["id"]
        patched = member_client.patch(f"/discord/bots/{bot_id}", json={"api_base_url": "https://chat.example.com/api"})

    assert (created.status_code, patched.status_code) == (400, 400)
    assert db_session.get(DiscordBot, int(bot_id)).api_base_url == "https://discord.com/api/v10"


@pytest.mark.parametrize("dm_bot_is_mine, status", [(True, 200), (False, 404)])
def test_a_dm_is_managed_by_the_owner_of_the_bot_that_delivered_it(
    member_client, db_session, user, dm_bot_is_mine, status
):
    other = HumanUser(email="other@example.com", name="Other", password_hash="x")
    db_session.add(other)
    db_session.flush()
    mine = DiscordBot(application_id=1, name="mine", created_by_user_id=user.id)
    theirs = DiscordBot(application_id=2, name="theirs", created_by_user_id=other.id)
    db_session.add_all([mine, theirs])
    db_session.flush()
    dm_bot = mine if dm_bot_is_mine else theirs
    db_session.add(DiscordChannel(id=999, server_id=None, bot_id=dm_bot.id, name="dm", channel_type="dm"))
    db_session.commit()

    response = member_client.patch("/discord/channels/999", json={"collect_messages": False})

    assert response.status_code == status


def two_owners_bots(db_session, user):
    other = HumanUser(email="other@example.com", name="Other", password_hash="x")
    db_session.add(other)
    db_session.flush()
    mine = DiscordBot(application_id=1, name="mine", created_by_user_id=user.id)
    theirs = DiscordBot(application_id=2, name="theirs", created_by_user_id=other.id)
    db_session.add_all([mine, theirs])
    db_session.flush()
    return mine, theirs


def test_servers_list_follows_verified_membership_not_deliveries(member_client, db_session, user):
    mine, theirs = two_owners_bots(db_session, user)
    db_session.add_all([
        DiscordServer(id=1, name="member", bot_id=theirs.id, verified=True),
        DiscordServer(id=2, name="delivered only", bot_id=theirs.id, verified=True),
        DiscordServer(id=3, name="pushed by me", bot_id=mine.id, verified=False),
        DiscordServer(id=4, name="owned", bot_id=mine.id, verified=True),
        DiscordUser(id=100, username="alice"),
    ])
    db_session.flush()
    db_session.add_all([
        DiscordBotServer(bot_id=mine.id, server_id=1),
        DiscordChannel(id=20, server_id=2, name="general", channel_type="text"),
    ])
    db_session.flush()
    db_session.add(DiscordMessage(
        modality="message", sha256=b"\x01" * 32, content="hi", message_id=5, channel_id=20,
        server_id=2, author_id=100, bot_id=mine.id, sent_at="2024-01-01T00:00:00+00:00",
    ))
    db_session.commit()

    names = sorted(s["name"] for s in member_client.get("/discord/servers").json())

    assert names == ["member", "owned"]


@pytest.mark.parametrize("verified, status", [(True, 200), (False, 404)])
def test_only_a_verified_owner_manages_a_server(member_client, db_session, user, verified, status):
    mine, _ = two_owners_bots(db_session, user)
    db_session.add(DiscordServer(id=9, name="s", bot_id=mine.id, verified=verified))
    db_session.commit()

    response = member_client.patch("/discord/servers/9", json={"sensitivity": "public"})

    assert response.status_code == status


def test_patch_rejects_a_token_for_another_application(client, db_session, user):
    bot_id = client.post("/discord/bots", json={"name": "G", "token": gateway_token(66668)}).json()["id"]

    response = client.patch(f"/discord/bots/{bot_id}", json={"token": gateway_token(66669)})

    assert response.status_code == 400


def test_invite_url_uses_the_application_id(client, db_session, user):
    bot = DiscordBot(application_id=77777, name="Owned", created_by_user_id=user.id)
    db_session.add(bot)
    db_session.commit()

    url = client.get(f"/discord/bots/{bot.id}/invite").json()["invite_url"]

    assert "client_id=77777&" in url


def test_owner_removes_the_bot(client, db_session, user):
    bot = DiscordBot(id=11111, application_id=11111, name="Owned bot", created_by_user_id=user.id)
    db_session.add(bot)
    db_session.commit()
    assert client.delete("/discord/bots/11111").status_code == 200
    assert db_session.get(DiscordBot, 11111) is None
