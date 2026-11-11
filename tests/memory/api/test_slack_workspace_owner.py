"""Only a Slack workspace's owner (its first connected user) or an admin may change its
or its channels' settings, and other members see only its public channels."""

import pytest

from memory.agents.provider_keys import ingest_key
from memory.agents.system import SUMMARISE
from memory.api.slack import _persist_oauth_credentials
from memory.common.access_control import get_user_project_roles, user_can_access
from memory.common.db.models import LLMProviderKey, User
from memory.common.db.models.slack import SlackApp, SlackChannel, SlackUserCredentials, SlackWorkspace
from memory.common.db.models.source_items import SlackMessage
from tests.conftest import unique_sha256

WORKSPACE = "T0SHARED"


def connect(db_session, user_id: int) -> SlackApp:
    app = SlackApp(client_id=f"app-{user_id}", name=f"app {user_id}", setup_state="live", created_by_user_id=user_id)
    db_session.add(app)
    db_session.flush()
    cred = SlackUserCredentials(slack_app_id=app.id, workspace_id=WORKSPACE, user_id=user_id, scopes=[])
    cred.access_token = "xoxp-x"
    db_session.add(cred)
    db_session.flush()
    return app


@pytest.fixture
def victim(db_session):
    victim = User(id=999, name="Victim", email="victim@example.com", password_hash="x")
    db_session.add(victim)
    db_session.commit()
    return victim


@pytest.fixture
def shared(db_session, user, victim):
    """A workspace the victim connected first and `user` joined second, with the
    victim's DM, a private channel and a public channel."""
    db_session.add(SlackWorkspace(id=WORKSPACE, name="Shared", collect_messages=False, owner_id=victim.id))
    db_session.flush()
    victim_app = connect(db_session, victim.id)
    connect(db_session, user.id)
    channels = {
        "dm": SlackChannel(id="D0VICTIM", workspace_id=WORKSPACE, name="ceo", channel_type="dm", is_private=True),
        "private": SlackChannel(
            id="G0SECRET", workspace_id=WORKSPACE, name="secret", channel_type="private_channel", is_private=True
        ),
        "public": SlackChannel(id="C0GENERAL", workspace_id=WORKSPACE, name="general", channel_type="channel"),
    }
    db_session.add_all(channels.values())
    db_session.flush()
    message = SlackMessage(
        modality="message",
        sha256=unique_sha256("dm"),
        content="my salary is a secret",
        message_ts="1731200000.000100",
        channel_id="D0VICTIM",
        workspace_id=WORKSPACE,
        slack_app_id=victim_app.id,
        author_id="U_VICTIM",
    )
    db_session.add(message)
    db_session.commit()
    return {**channels, "message": message}


def oauth_connect(db_session, user_id: int) -> None:
    app = SlackApp(client_id=f"oauth-{user_id}", name=f"oauth {user_id}", created_by_user_id=user_id)
    db_session.add(app)
    db_session.flush()
    user = db_session.get(User, user_id)
    _persist_oauth_credentials(db_session, app, user, WORKSPACE, "Shared", {"access_token": "xoxp-x"})


def test_the_first_user_to_connect_owns_the_workspace(db_session, user, victim):
    oauth_connect(db_session, victim.id)
    oauth_connect(db_session, user.id)

    workspace = db_session.get(SlackWorkspace, WORKSPACE)
    assert (workspace.owner_id, workspace.llm_registrant_id) == (victim.id, victim.id)


@pytest.mark.parametrize("leave", ["disconnect", "delete_app"])
def test_the_owner_leaving_does_not_hand_the_workspace_on(member_client, db_session, victim, shared, leave):
    leaving = {
        "disconnect": lambda: db_session.query(SlackUserCredentials)
        .filter(SlackUserCredentials.user_id == victim.id)
        .delete(),
        "delete_app": lambda: db_session.query(SlackApp).filter(SlackApp.created_by_user_id == victim.id).delete(),
    }
    leaving[leave]()
    db_session.commit()

    listing = member_client.get(f"/slack/workspaces/{WORKSPACE}/channels")
    change = member_client.patch(f"/slack/channels/{shared['dm'].id}", json={"collect_messages": True})

    assert [c["id"] for c in listing.json()] == ["C0GENERAL"]
    assert change.status_code == 404


def test_a_deleted_owner_leaves_the_workspace_to_admins(member_client, db_session, victim, shared):
    db_session.delete(victim)
    db_session.commit()

    workspace = member_client.patch(f"/slack/workspaces/{WORKSPACE}", json={"sensitivity": "public"})
    listing = member_client.get(f"/slack/workspaces/{WORKSPACE}/channels")

    assert (workspace.status_code, [c["id"] for c in listing.json()]) == (403, ["C0GENERAL"])
    assert db_session.get(SlackWorkspace, WORKSPACE).owner_id is None


def test_a_new_member_does_not_claim_an_ownerless_workspace(db_session, user, victim, shared):
    db_session.delete(victim)
    db_session.query(SlackUserCredentials).delete()
    db_session.commit()

    oauth_connect(db_session, user.id)

    assert db_session.get(SlackWorkspace, WORKSPACE).owner_id is None


def test_member_cannot_take_ownership(member_client, db_session, user, shared):
    response = member_client.patch(f"/slack/workspaces/{WORKSPACE}", json={"owner_id": user.id})

    assert response.status_code == 403
    assert db_session.get(SlackWorkspace, WORKSPACE).owner_id == 999


def test_admin_hands_the_workspace_to_a_member(client, db_session, user, victim, shared):
    db_session.query(SlackWorkspace).filter(SlackWorkspace.id == WORKSPACE).update({"owner_id": None})
    db_session.commit()

    response = client.patch(f"/slack/workspaces/{WORKSPACE}", json={"owner_id": victim.id})

    assert (response.status_code, response.json()["owner_id"]) == (200, victim.id)


def test_ownership_goes_only_to_a_connected_member(client, db_session, admin_user, shared):
    response = client.patch(f"/slack/workspaces/{WORKSPACE}", json={"owner_id": admin_user.id})

    assert response.status_code == 400
    assert db_session.get(SlackWorkspace, WORKSPACE).owner_id == 999


def test_member_lists_only_public_channels(member_client, shared):
    response = member_client.get(f"/slack/workspaces/{WORKSPACE}/channels")

    assert [c["id"] for c in response.json()] == ["C0GENERAL"]


def test_member_sees_the_public_channel_count_and_cannot_manage(member_client, shared):
    body = member_client.get(f"/slack/workspaces/{WORKSPACE}").json()

    assert (body["channel_count"], body["can_manage"]) == (1, False)


def test_owner_lists_every_channel(member_client, db_session, user, shared):
    db_session.query(SlackWorkspace).filter(SlackWorkspace.id == WORKSPACE).update({"owner_id": user.id})
    db_session.commit()

    response = member_client.get(f"/slack/workspaces/{WORKSPACE}/channels")

    assert sorted(c["id"] for c in response.json()) == ["C0GENERAL", "D0VICTIM", "G0SECRET"]


def test_admin_lists_every_channel(client, shared):
    response = client.get(f"/slack/workspaces/{WORKSPACE}/channels")

    assert sorted(c["id"] for c in response.json()) == ["C0GENERAL", "D0VICTIM", "G0SECRET"]


@pytest.mark.parametrize(
    "channel, status",
    [("dm", 404), ("private", 404), ("public", 403)],
)
@pytest.mark.parametrize(
    "change",
    [{"sensitivity": "public"}, {"collect_messages": True}, {"project_id": None}],
)
def test_member_cannot_change_a_channel(member_client, db_session, user, shared, channel, status, change):
    target = shared[channel]

    response = member_client.patch(f"/slack/channels/{target.id}", json=change)

    db_session.refresh(target)
    db_session.refresh(shared["message"])
    db_session.refresh(user)
    assert (response.status_code, target.sensitivity, target.collect_messages) == (status, "basic", None)
    assert not user_can_access(user, shared["message"], get_user_project_roles(db_session, user))


@pytest.mark.parametrize(
    "change",
    [
        {"sensitivity": "public"},
        {"collect_messages": True},
        {"project_id": None},
        {"sync_interval_seconds": 600},
        {"event_reconcile_interval_seconds": 7200},
        {"llm_key": "none"},
    ],
)
def test_member_cannot_change_the_workspace(member_client, db_session, shared, change):
    response = member_client.patch(f"/slack/workspaces/{WORKSPACE}", json=change)

    workspace = db_session.get(SlackWorkspace, WORKSPACE)
    db_session.refresh(workspace)
    assert (response.status_code, workspace.sensitivity, workspace.collect_messages) == (403, "basic", False)
    assert (workspace.sync_interval_seconds, workspace.llm_disabled) == (300, False)


def test_owner_changes_a_dm_and_the_workspace(member_client, db_session, user, shared):
    db_session.query(SlackWorkspace).filter(SlackWorkspace.id == WORKSPACE).update({"owner_id": user.id})
    db_session.commit()

    channel = member_client.patch(f"/slack/channels/{shared['dm'].id}", json={"collect_messages": True})
    workspace = member_client.patch(f"/slack/workspaces/{WORKSPACE}", json={"sensitivity": "internal"})

    assert (channel.status_code, channel.json()["collect_messages"]) == (200, True)
    assert (workspace.status_code, workspace.json()["sensitivity"], workspace.json()["can_manage"]) == (
        200,
        "internal",
        True,
    )


def test_admin_without_a_credential_changes_a_channel(client, db_session, user, shared):
    db_session.query(SlackUserCredentials).filter(SlackUserCredentials.user_id == user.id).delete()
    db_session.commit()

    response = client.patch(f"/slack/channels/{shared['private'].id}", json={"sensitivity": "internal"})

    assert (response.status_code, response.json()["sensitivity"]) == (200, "internal")


def test_stranger_gets_404_for_a_public_channel(member_client, db_session, user, shared):
    db_session.query(SlackUserCredentials).filter(SlackUserCredentials.user_id == user.id).delete()
    db_session.commit()

    response = member_client.patch(f"/slack/channels/{shared['public'].id}", json={"sensitivity": "internal"})

    assert response.status_code == 404


def own_key(db_session, user_id: int) -> int:
    key = LLMProviderKey(user_id=user_id, provider="anthropic", label="mine", key_hint="x")
    key.key = "sk-test"
    db_session.add(key)
    db_session.commit()
    return key.id


def test_handing_over_a_default_key_workspace_switches_llm_off(member_client, db_session, user, victim, shared):
    db_session.query(SlackWorkspace).filter(SlackWorkspace.id == WORKSPACE).update({"owner_id": user.id})
    db_session.commit()

    response = member_client.patch(
        f"/slack/workspaces/{WORKSPACE}",
        json={"collect_messages": True, "llm_key": "default", "owner_id": victim.id},
    )

    workspace = db_session.get(SlackWorkspace, WORKSPACE)
    db_session.refresh(workspace)
    assert response.status_code == 200
    assert (workspace.owner_id, workspace.llm_key_id, workspace.llm_disabled) == (victim.id, None, True)
    assert ingest_key(db_session, shared["message"], SUMMARISE) is None


def test_handing_over_keeps_the_callers_own_key(member_client, db_session, user, victim, shared):
    db_session.query(SlackWorkspace).filter(SlackWorkspace.id == WORKSPACE).update({"owner_id": user.id})
    db_session.commit()
    key_id = own_key(db_session, user.id)

    response = member_client.patch(f"/slack/workspaces/{WORKSPACE}", json={"llm_key": key_id, "owner_id": victim.id})

    workspace = db_session.get(SlackWorkspace, WORKSPACE)
    db_session.refresh(workspace)
    assert response.status_code == 200
    assert (workspace.owner_id, workspace.llm_key_id, workspace.llm_disabled) == (victim.id, key_id, False)


def test_admin_taking_over_switches_off_the_old_owners_key(client, db_session, user, victim, shared):
    key_id = own_key(db_session, victim.id)
    db_session.query(SlackWorkspace).filter(SlackWorkspace.id == WORKSPACE).update({"llm_key_id": key_id})
    db_session.commit()

    response = client.patch(f"/slack/workspaces/{WORKSPACE}", json={"owner_id": user.id})

    workspace = db_session.get(SlackWorkspace, WORKSPACE)
    db_session.refresh(workspace)
    assert response.status_code == 200
    assert (workspace.owner_id, workspace.llm_key_id, workspace.llm_disabled) == (user.id, None, True)


def test_admin_taking_over_on_default_keeps_llm_on(client, db_session, user, victim, shared):
    response = client.patch(f"/slack/workspaces/{WORKSPACE}", json={"owner_id": user.id})

    workspace = db_session.get(SlackWorkspace, WORKSPACE)
    db_session.refresh(workspace)
    assert response.status_code == 200
    assert (workspace.owner_id, workspace.llm_key_id, workspace.llm_disabled) == (user.id, None, False)
