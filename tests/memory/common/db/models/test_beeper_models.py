from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from memory.common.access_control import (
    apply_access_filter_to_query,
    build_access_filter,
    get_user_project_roles,
)
from memory.common.db.models import (
    BeeperAccount,
    BeeperChat,
    BeeperMessage,
    BeeperNetwork,
    HumanUser,
    Person,
    SourceItem,
    Team,
)
from memory.common.db.models.sources import Project, project_teams, team_members
from tests.conftest import unique_sha256


def make_user(db_session, email: str) -> HumanUser:
    user = HumanUser(email=email, name=email, password_hash="x", scopes=["read"])
    db_session.add(user)
    db_session.flush()
    return user


def make_chat(db_session, owner: HumanUser, **network) -> BeeperChat:
    account = BeeperAccount(created_by_user_id=owner.id, server_url=f"http://beeper-{owner.id}:23373")
    net = BeeperNetwork(
        account=account, network_account_id="slackgo.T1-U1", network="Slack",
        collect_messages=network.pop("collect_messages", True), **network,
    )
    chat = BeeperChat(network=net, chat_id="!chat:beeper.local")
    db_session.add_all([account, net, chat])
    db_session.flush()
    return chat


def make_message(db_session, chat: BeeperChat, message_id: str = "1") -> BeeperMessage:
    message = BeeperMessage(
        chat=chat, message_id=message_id, sha256=unique_sha256(message_id), modality="message",
        content="a message long enough to embed", sent_at=datetime.now(timezone.utc),
        is_sender=False, tags=[],
    )
    db_session.add(message)
    db_session.flush()
    message.apply_inherited_access_control()
    db_session.flush()
    return message


@pytest.mark.parametrize(
    "network_collects, chat_override, is_merged, expected",
    [
        (True, None, False, True),
        (False, None, False, False),
        (False, True, False, True),
        (True, False, False, False),
        (True, True, True, False),
    ],
)
def test_chat_collects_by_override_else_network_and_never_when_merged(
    db_session, regular_user, network_collects, chat_override, is_merged, expected
):
    chat = make_chat(db_session, regular_user, collect_messages=network_collects)
    chat.collect_messages = chat_override
    chat.is_merged = is_merged

    assert chat.should_collect is expected


@pytest.mark.parametrize(
    "chat_sensitivity, network_sensitivity, expected",
    [
        (None, "confidential", "confidential"),
        ("public", "confidential", "public"),
        ("confidential", "internal", "confidential"),
        (None, None, "basic"),
    ],
)
def test_message_inherits_chat_then_network_access_and_owner(
    db_session, regular_user, chat_sensitivity, network_sensitivity, expected
):
    project = Project(title="P", state="open")
    db_session.add(project)
    db_session.flush()
    network = {"sensitivity": network_sensitivity} if network_sensitivity else {}
    chat = make_chat(db_session, regular_user, project_id=project.id, **network)
    chat.sensitivity = chat_sensitivity

    message = make_message(db_session, chat)

    assert (message.project_id, message.sensitivity, message.creator_id) == (
        project.id, expected, regular_user.id,
    )
    assert message.llm_key_holder is chat.network
    assert message.llm_registrant_id == regular_user.id


def visible_ids(db_session, user) -> set[int]:
    roles = get_user_project_roles(db_session, user)
    query = apply_access_filter_to_query(db_session.query(SourceItem), build_access_filter(user, roles))
    return {item.id for item in query}


@pytest.mark.parametrize(
    "viewer, has_project, sees",
    [
        ("owner", False, True),
        ("owner", True, True),
        ("team member", True, True),
        ("team member", False, False),
        ("outsider", True, False),
    ],
)
def test_access_filter_follows_owner_team_role_and_project(
    db_session, regular_user, viewer, has_project, sees
):
    project = Project(title="P", state="open")
    team = Team(name="T", slug="t", is_active=True)
    db_session.add_all([project, team])
    db_session.flush()
    member = make_user(db_session, "member@example.com")
    person = Person(identifier="member", display_name="Member")
    member.person = person
    outsider = make_user(db_session, "outsider@example.com")
    db_session.flush()
    db_session.execute(team_members.insert().values(team_id=team.id, person_id=person.id, role="member"))
    db_session.execute(project_teams.insert().values(project_id=project.id, team_id=team.id))
    chat = make_chat(db_session, regular_user, project_id=project.id if has_project else None)
    message = make_message(db_session, chat)

    user = {"owner": regular_user, "team member": member, "outsider": outsider}[viewer]
    assert (message.id in visible_ids(db_session, user)) is sees


def test_deleting_an_account_deletes_its_messages_source_items(db_session, regular_user):
    chat = make_chat(db_session, regular_user)
    make_message(db_session, chat)

    db_session.execute(text("DELETE FROM beeper_accounts"))
    db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

    assert db_session.execute(text("SELECT count(*) FROM source_item")).scalar() == 0


def test_token_is_stored_encrypted(db_session, regular_user):
    account = BeeperAccount(created_by_user_id=regular_user.id, server_url="http://beeper:23373")
    account.token = "secret-token"
    db_session.add(account)
    db_session.flush()

    assert b"secret-token" not in (account.token_encrypted or b"")
    assert account.token == "secret-token"
    assert account.signed_in
