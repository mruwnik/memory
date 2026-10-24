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
    bot.authorized_users.append(user)
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
