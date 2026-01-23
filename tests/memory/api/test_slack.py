"""Tests for Slack API endpoints."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from memory.common.db.models import User
from memory.common.db.models.slack import (
    SlackChannel,
    SlackOAuthState,
    SlackUser,
    SlackWorkspace,
)


@pytest.fixture
def other_user(db_session):
    """Create a second user for testing access control."""
    other = User(
        id=999,
        name="Other User",
        email="other@example.com",
    )
    db_session.add(other)
    db_session.commit()
    return other


@pytest.fixture
def slack_workspace(db_session, user):
    """Create a Slack workspace for testing."""
    workspace = SlackWorkspace(
        id="T12345678",
        name="Test Workspace",
        user_id=user.id,
        collect_messages=True,
        sync_interval_seconds=60,
    )
    workspace.access_token = "xoxp-test-token"
    db_session.add(workspace)
    db_session.commit()
    return workspace


@pytest.fixture
def slack_channel(db_session, slack_workspace):
    """Create a Slack channel for testing."""
    channel = SlackChannel(
        id="C12345678",
        workspace_id=slack_workspace.id,
        name="general",
        channel_type="channel",
        is_private=False,
        is_archived=False,
        collect_messages=True,
    )
    db_session.add(channel)
    db_session.commit()
    return channel


@pytest.fixture
def slack_user_record(db_session, slack_workspace):
    """Create a Slack user for testing."""
    slack_user = SlackUser(
        id="U12345678",
        workspace_id=slack_workspace.id,
        username="testuser",
        display_name="Test User",
        real_name="Test User",
        email="slackuser@example.com",
        is_bot=False,
    )
    db_session.add(slack_user)
    db_session.commit()
    return slack_user


# ====== GET /slack/workspaces tests ======


def test_list_workspaces_returns_user_workspaces(client, db_session, user, slack_workspace):
    """List workspaces returns only workspaces belonging to current user."""
    response = client.get("/slack/workspaces")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == slack_workspace.id
    assert data[0]["name"] == "Test Workspace"
    assert data[0]["collect_messages"] is True


def test_list_workspaces_empty_when_no_workspaces(client, db_session, user):
    """List workspaces returns empty list when user has no workspaces."""
    response = client.get("/slack/workspaces")

    assert response.status_code == 200
    assert response.json() == []


def test_list_workspaces_excludes_other_users_workspaces(client, db_session, user, other_user):
    """List workspaces doesn't return workspaces from other users."""
    # Create workspace for other user
    other_workspace = SlackWorkspace(
        id="T_OTHER",
        name="Other Workspace",
        user_id=other_user.id,
    )
    other_workspace.access_token = "xoxp-other-token"
    db_session.add(other_workspace)
    db_session.commit()

    response = client.get("/slack/workspaces")

    assert response.status_code == 200
    assert response.json() == []


# ====== GET /slack/workspaces/{workspace_id} tests ======


def test_get_workspace_success(client, db_session, user, slack_workspace):
    """Get workspace by ID returns workspace details."""
    response = client.get(f"/slack/workspaces/{slack_workspace.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == slack_workspace.id
    assert data["name"] == slack_workspace.name
    assert data["collect_messages"] is True
    assert data["sync_interval_seconds"] == 60


def test_get_workspace_not_found(client, db_session, user):
    """Get workspace returns 404 when workspace doesn't exist."""
    response = client.get("/slack/workspaces/T_NONEXISTENT")

    assert response.status_code == 404


def test_get_workspace_access_denied(client, db_session, user, other_user):
    """Get workspace returns 404 for workspace owned by another user."""
    other_workspace = SlackWorkspace(
        id="T_OTHER",
        name="Other Workspace",
        user_id=other_user.id,
    )
    other_workspace.access_token = "xoxp-other-token"
    db_session.add(other_workspace)
    db_session.commit()

    response = client.get(f"/slack/workspaces/{other_workspace.id}")

    assert response.status_code == 404


# ====== PATCH /slack/workspaces/{workspace_id} tests ======


def test_update_workspace_collect_messages(client, db_session, user, slack_workspace):
    """Update workspace collect_messages setting."""
    response = client.patch(
        f"/slack/workspaces/{slack_workspace.id}",
        json={"collect_messages": False},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["collect_messages"] is False

    db_session.refresh(slack_workspace)
    assert slack_workspace.collect_messages is False


def test_update_workspace_sync_interval(client, db_session, user, slack_workspace):
    """Update workspace sync_interval_seconds setting."""
    response = client.patch(
        f"/slack/workspaces/{slack_workspace.id}",
        json={"sync_interval_seconds": 120},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["sync_interval_seconds"] == 120


def test_update_workspace_not_found(client, db_session, user):
    """Update workspace returns 404 when workspace doesn't exist."""
    response = client.patch(
        "/slack/workspaces/T_NONEXISTENT",
        json={"collect_messages": False},
    )

    assert response.status_code == 404


# ====== DELETE /slack/workspaces/{workspace_id} tests ======


def test_delete_workspace_success(client, db_session, user, slack_workspace):
    """Delete workspace succeeds."""
    response = client.delete(f"/slack/workspaces/{slack_workspace.id}")

    assert response.status_code == 200

    # Verify workspace was deleted
    workspace = db_session.get(SlackWorkspace, slack_workspace.id)
    assert workspace is None


def test_delete_workspace_not_found(client, db_session, user):
    """Delete workspace returns 404 when workspace doesn't exist."""
    response = client.delete("/slack/workspaces/T_NONEXISTENT")

    assert response.status_code == 404


def test_delete_workspace_cascades_channels(client, db_session, user, slack_workspace, slack_channel):
    """Delete workspace also deletes associated channels."""
    channel_id = slack_channel.id

    response = client.delete(f"/slack/workspaces/{slack_workspace.id}")

    assert response.status_code == 200

    # Verify channel was also deleted
    channel = db_session.get(SlackChannel, channel_id)
    assert channel is None


# ====== GET /slack/workspaces/{workspace_id}/channels tests ======


def test_list_channels_success(client, db_session, user, slack_workspace, slack_channel):
    """List channels returns channels for workspace."""
    response = client.get(f"/slack/workspaces/{slack_workspace.id}/channels")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == slack_channel.id
    assert data[0]["name"] == "general"
    assert data[0]["channel_type"] == "channel"


def test_list_channels_workspace_not_found(client, db_session, user):
    """List channels returns 404 when workspace doesn't exist."""
    response = client.get("/slack/workspaces/T_NONEXISTENT/channels")

    assert response.status_code == 404


# ====== PATCH /slack/channels/{channel_id} tests ======


def test_update_channel_collect_messages(client, db_session, user, slack_workspace, slack_channel):
    """Update channel collect_messages setting."""
    response = client.patch(
        f"/slack/channels/{slack_channel.id}",
        json={"collect_messages": False},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["collect_messages"] is False

    db_session.refresh(slack_channel)
    assert slack_channel.collect_messages is False


def test_update_channel_inherit_collect_messages(client, db_session, user, slack_workspace, slack_channel):
    """Update channel to inherit collect_messages from workspace."""
    # First set explicit value
    slack_channel.collect_messages = False
    db_session.commit()

    # Then set to None to inherit
    response = client.patch(
        f"/slack/channels/{slack_channel.id}",
        json={"collect_messages": None},
    )

    assert response.status_code == 200

    db_session.refresh(slack_channel)
    assert slack_channel.collect_messages is None
    # should_collect should inherit from workspace
    assert slack_channel.should_collect == slack_workspace.collect_messages


def test_update_channel_not_found(client, db_session, user):
    """Update channel returns 404 when channel doesn't exist."""
    response = client.patch(
        "/slack/channels/C_NONEXISTENT",
        json={"collect_messages": False},
    )

    assert response.status_code == 404


# ====== POST /slack/workspaces/{workspace_id}/sync tests ======


@patch("memory.api.slack.app")
def test_trigger_sync_success(mock_app, client, db_session, user, slack_workspace):
    """Trigger sync sends task to Celery."""
    response = client.post(f"/slack/workspaces/{slack_workspace.id}/sync")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "queued"
    assert data["workspace_id"] == slack_workspace.id

    mock_app.send_task.assert_called_once()


def test_trigger_sync_workspace_not_found(client, db_session, user):
    """Trigger sync returns 404 when workspace doesn't exist."""
    response = client.post("/slack/workspaces/T_NONEXISTENT/sync")

    assert response.status_code == 404


# ====== GET /slack/workspaces/{workspace_id}/users tests ======


def test_list_users_success(client, db_session, user, slack_workspace, slack_user_record):
    """List users returns Slack users for workspace."""
    response = client.get(f"/slack/workspaces/{slack_workspace.id}/users")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == slack_user_record.id
    assert data[0]["username"] == "testuser"
    assert data[0]["display_name"] == "Test User"


def test_list_users_workspace_not_found(client, db_session, user):
    """List users returns 404 when workspace doesn't exist."""
    response = client.get("/slack/workspaces/T_NONEXISTENT/users")

    assert response.status_code == 404


# ====== GET /slack/authorize tests ======


@patch("memory.api.slack.settings")
def test_authorize_not_configured(mock_settings, client, db_session, user):
    """Authorize returns error when Slack is not configured."""
    mock_settings.SLACK_CLIENT_ID = ""
    mock_settings.SLACK_CLIENT_SECRET = ""

    response = client.get("/slack/authorize")

    assert response.status_code == 400
    assert "not configured" in response.json()["detail"].lower()


@patch("memory.api.slack.settings")
def test_authorize_success(mock_settings, client, db_session, user):
    """Authorize returns authorization URL."""
    mock_settings.SLACK_CLIENT_ID = "test_client_id"
    mock_settings.SLACK_CLIENT_SECRET = "test_secret"
    mock_settings.SLACK_REDIRECT_URI = "http://localhost/callback"

    response = client.get("/slack/authorize")

    assert response.status_code == 200
    data = response.json()
    assert "authorization_url" in data
    assert "slack.com" in data["authorization_url"]
    assert "test_client_id" in data["authorization_url"]

    # Verify state was stored
    states = db_session.query(SlackOAuthState).filter_by(user_id=user.id).all()
    assert len(states) == 1


# ====== OAuth State tests ======


def test_oauth_state_expiration(db_session, user):
    """Test OAuth state expiration check."""
    # Create expired state
    expired_state = SlackOAuthState(
        state="expired_state",
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=15),
    )
    db_session.add(expired_state)

    # Create valid state
    valid_state = SlackOAuthState(
        state="valid_state",
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    db_session.add(valid_state)
    db_session.commit()

    # Check expiration
    assert expired_state.expires_at < datetime.now(timezone.utc)
    assert valid_state.expires_at > datetime.now(timezone.utc)


# ====== Model tests ======


def test_slack_workspace_token_encryption(db_session, user):
    """Test that tokens are encrypted when stored."""
    workspace = SlackWorkspace(
        id="T_TEST",
        name="Test",
        user_id=user.id,
    )
    workspace.access_token = "xoxp-secret-token"
    workspace.refresh_token = "xoxr-refresh-token"
    db_session.add(workspace)
    db_session.commit()

    # Raw encrypted values should not equal plaintext
    assert workspace.access_token_encrypted != b"xoxp-secret-token"
    assert workspace.refresh_token_encrypted != b"xoxr-refresh-token"

    # Decrypted values should match
    assert workspace.access_token == "xoxp-secret-token"
    assert workspace.refresh_token == "xoxr-refresh-token"


def test_slack_workspace_token_expiration(db_session, user):
    """Test token expiration check."""
    workspace = SlackWorkspace(
        id="T_TEST",
        name="Test",
        user_id=user.id,
    )
    db_session.add(workspace)
    db_session.commit()

    # No expiration = not expired
    assert workspace.is_token_expired() is False

    # Future expiration = not expired
    workspace.token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    db_session.commit()
    assert workspace.is_token_expired() is False

    # Past expiration = expired
    workspace.token_expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.commit()
    assert workspace.is_token_expired() is True


def test_slack_channel_should_collect_inherit(db_session, slack_workspace):
    """Test channel inherits collect_messages from workspace."""
    channel = SlackChannel(
        id="C_INHERIT",
        workspace_id=slack_workspace.id,
        name="test",
        channel_type="channel",
        collect_messages=None,  # Inherit from workspace
    )
    db_session.add(channel)
    db_session.commit()

    # Should inherit from workspace
    assert channel.should_collect == slack_workspace.collect_messages


def test_slack_channel_should_collect_explicit(db_session, slack_workspace):
    """Test channel explicit collect_messages overrides workspace."""
    # Workspace has collect_messages=True
    channel = SlackChannel(
        id="C_EXPLICIT",
        workspace_id=slack_workspace.id,
        name="test",
        channel_type="channel",
        collect_messages=False,  # Explicitly set to False
    )
    db_session.add(channel)
    db_session.commit()

    # Should use explicit value, not inherit
    assert channel.should_collect is False


def test_slack_user_name_property(db_session, slack_workspace):
    """Test SlackUser.name returns best available name."""
    # All names set
    user1 = SlackUser(
        id="U1",
        workspace_id=slack_workspace.id,
        username="user1",
        display_name="Display",
        real_name="Real Name",
    )
    assert user1.name == "Display"

    # No display name
    user2 = SlackUser(
        id="U2",
        workspace_id=slack_workspace.id,
        username="user2",
        display_name=None,
        real_name="Real Name",
    )
    assert user2.name == "Real Name"

    # Only username
    user3 = SlackUser(
        id="U3",
        workspace_id=slack_workspace.id,
        username="user3",
        display_name=None,
        real_name=None,
    )
    assert user3.name == "user3"
