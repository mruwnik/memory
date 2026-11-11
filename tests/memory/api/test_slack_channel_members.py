"""A workspace member who isn't its owner sees the DMs and private channels their own
token lists, and may switch their collection off, but nothing else of them."""

import pytest

from memory.common.db.models import User
from memory.common.db.models.slack import (
    SlackApp,
    SlackChannel,
    SlackChannelMember,
    SlackUserCredentials,
    SlackWorkspace,
)

WORKSPACE = "T0SHARED"


def connect(db_session, user_id: int) -> None:
    app = SlackApp(client_id=f"app-{user_id}", name=f"app {user_id}", setup_state="live", created_by_user_id=user_id)
    db_session.add(app)
    db_session.flush()
    cred = SlackUserCredentials(slack_app_id=app.id, workspace_id=WORKSPACE, user_id=user_id, scopes=[])
    cred.access_token = "xoxp-x"
    db_session.add(cred)
    db_session.flush()


@pytest.fixture
def shared(db_session, user):
    """A workspace another user connected first and `user` joined second: the other
    user's DM, `user`'s own DM and private channel, and a public channel."""
    owner = User(id=999, name="Owner", email="owner@example.com", password_hash="x")
    db_session.add_all([owner, SlackWorkspace(id=WORKSPACE, name="Shared", collect_messages=True)])
    db_session.flush()
    connect(db_session, owner.id)
    connect(db_session, user.id)
    channels = {
        "theirs": SlackChannel(id="D0THEIRS", workspace_id=WORKSPACE, name="ceo", channel_type="dm", is_private=True),
        "mine": SlackChannel(id="D0MINE", workspace_id=WORKSPACE, name="me", channel_type="dm", is_private=True),
        "my_private": SlackChannel(
            id="G0MINE", workspace_id=WORKSPACE, name="team", channel_type="private_channel", is_private=True
        ),
        "public": SlackChannel(id="C0GENERAL", workspace_id=WORKSPACE, name="general", channel_type="channel"),
    }
    db_session.add_all(channels.values())
    db_session.flush()
    db_session.add_all(
        [
            SlackChannelMember(channel_id="D0THEIRS", user_id=owner.id),
            SlackChannelMember(channel_id="D0MINE", user_id=owner.id),
            SlackChannelMember(channel_id="D0MINE", user_id=user.id),
            SlackChannelMember(channel_id="G0MINE", user_id=user.id),
        ]
    )
    db_session.commit()
    return channels


def test_member_lists_public_channels_and_their_own(member_client, shared):
    response = member_client.get(f"/slack/workspaces/{WORKSPACE}/channels")

    listed = {c["id"]: c["can_switch_off"] for c in response.json()}
    assert listed == {"C0GENERAL": False, "D0MINE": True, "G0MINE": True}


def test_member_counts_public_channels_and_their_own(member_client, shared):
    body = member_client.get(f"/slack/workspaces/{WORKSPACE}").json()

    assert (body["channel_count"], body["can_manage"]) == (3, False)


@pytest.mark.parametrize("channel", ["mine", "my_private"])
def test_member_switches_off_their_own_channel(member_client, db_session, shared, channel):
    target = shared[channel]

    response = member_client.patch(f"/slack/channels/{target.id}", json={"collect_messages": False})

    db_session.refresh(target)
    assert (response.status_code, target.collect_messages, target.should_collect) == (200, False, False)


@pytest.mark.parametrize(
    "change",
    [
        {"collect_messages": True},
        {"collect_messages": None},
        {"sensitivity": "public"},
        {"project_id": None},
        {"collect_messages": False, "sensitivity": "public"},
    ],
)
def test_member_changes_nothing_else_in_their_own_channel(member_client, db_session, shared, change):
    target = shared["mine"]

    response = member_client.patch(f"/slack/channels/{target.id}", json=change)

    db_session.refresh(target)
    assert (response.status_code, target.collect_messages, target.sensitivity) == (403, None, "basic")


@pytest.mark.parametrize("channel, status", [("theirs", 404), ("public", 403)])
def test_member_cannot_switch_off_a_channel_they_are_not_in(member_client, db_session, shared, channel, status):
    target = shared[channel]

    response = member_client.patch(f"/slack/channels/{target.id}", json={"collect_messages": False})

    db_session.refresh(target)
    assert (response.status_code, target.collect_messages) == (status, None)
