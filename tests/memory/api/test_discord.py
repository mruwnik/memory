import pytest

from memory.common.db.models import DiscordBot, DiscordChannel, DiscordServer
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
    bot = DiscordBot(id=11111, name="Bot")
    bot.created_by_user_id = user.id
    server = DiscordServer(
        id=44444, name="Guild", bot=bot, project_id=project.id, collect_messages=True
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


def test_bot_registration_does_not_grant_access_to_an_existing_bot(client, db_session, user):
    import base64
    from memory.common.db.models import HumanUser

    owner = HumanUser.create_with_password(email="owner@example.com", name="Owner", password="password123")
    db_session.add(owner)
    db_session.flush()
    bot = DiscordBot(id=11111, name="Owner's bot", created_by_user_id=owner.id)
    db_session.add(bot)
    db_session.commit()
    token = base64.b64encode(b"11111").decode() + ".timestamp.signature"
    response = client.post("/discord/bots", json={"name": "Other", "token": token})
    assert response.status_code == 409
    db_session.refresh(bot)
    assert bot.created_by_user_id == owner.id
    assert not bot.is_authorized(user)


def test_owner_removes_the_bot(client, db_session, user):
    bot = DiscordBot(id=11111, name="Owned bot", created_by_user_id=user.id)
    db_session.add(bot)
    db_session.commit()
    assert client.delete("/discord/bots/11111").status_code == 200
    assert db_session.get(DiscordBot, 11111) is None
