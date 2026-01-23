"""Tests for Slack API endpoints."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from memory.common.db.models import User, OAuthClientState
from memory.common.db.models.slack import (
    SlackChannel,
    SlackUserCredentials,
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
def slack_workspace(db_session):
    """Create a Slack workspace for testing."""
    workspace = SlackWorkspace(
        id="T12345678",
        name="Test Workspace",
        collect_messages=True,
        sync_interval_seconds=60,
    )
    db_session.add(workspace)
    db_session.commit()
    return workspace


@pytest.fixture
def slack_credentials(db_session, slack_workspace, user):
    """Create Slack credentials for the test user."""
    credentials = SlackUserCredentials(
        workspace_id=slack_workspace.id,
        user_id=user.id,
        scopes=["channels:read", "chat:write"],
        slack_user_id="U_TEST_USER",
    )
    credentials.access_token = "xoxp-test-token"
    db_session.add(credentials)
    db_session.commit()
    return credentials


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


# ====== GET /slack/workspaces tests ======


def test_list_workspaces_returns_user_workspaces(
    client, db_session, user, slack_workspace, slack_credentials
):
    """List workspaces returns only workspaces the user has credentials for."""
    response = client.get("/slack/workspaces")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == slack_workspace.id
    assert data[0]["name"] == "Test Workspace"
    assert data[0]["collect_messages"] is True
    assert data[0]["user_connected"] is True


def test_list_workspaces_empty_when_no_credentials(client, db_session, user):
    """List workspaces returns empty list when user has no credentials."""
    response = client.get("/slack/workspaces")

    assert response.status_code == 200
    assert response.json() == []


def test_list_workspaces_excludes_workspaces_without_credentials(
    client, db_session, user, other_user
):
    """List workspaces doesn't return workspaces the user doesn't have credentials for."""
    # Create workspace with credentials for other user only
    workspace = SlackWorkspace(
        id="T_OTHER",
        name="Other Workspace",
    )
    db_session.add(workspace)
    db_session.flush()

    other_creds = SlackUserCredentials(
        workspace_id=workspace.id,
        user_id=other_user.id,
    )
    other_creds.access_token = "xoxp-other-token"
    db_session.add(other_creds)
    db_session.commit()

    response = client.get("/slack/workspaces")

    assert response.status_code == 200
    assert response.json() == []


# ====== GET /slack/workspaces/{workspace_id} tests ======


def test_get_workspace_success(
    client, db_session, user, slack_workspace, slack_credentials
):
    """Get workspace by ID returns workspace details."""
    response = client.get(f"/slack/workspaces/{slack_workspace.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == slack_workspace.id
    assert data["name"] == slack_workspace.name
    assert data["collect_messages"] is True
    assert data["sync_interval_seconds"] == 60
    assert data["connected_users"] == 1


def test_get_workspace_not_found(client, db_session, user):
    """Get workspace returns 404 when workspace doesn't exist."""
    response = client.get("/slack/workspaces/T_NONEXISTENT")

    assert response.status_code == 404


def test_get_workspace_access_denied_no_credentials(
    client, db_session, user, slack_workspace
):
    """Get workspace returns 404 when user has no credentials for workspace."""
    # Don't create credentials for user
    response = client.get(f"/slack/workspaces/{slack_workspace.id}")

    assert response.status_code == 404


# ====== PATCH /slack/workspaces/{workspace_id} tests ======


def test_update_workspace_collect_messages(
    client, db_session, user, slack_workspace, slack_credentials
):
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


def test_update_workspace_sync_interval(
    client, db_session, user, slack_workspace, slack_credentials
):
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


def test_disconnect_workspace_success(
    client, db_session, user, slack_workspace, slack_credentials
):
    """Disconnect workspace removes user's credentials."""
    response = client.delete(f"/slack/workspaces/{slack_workspace.id}")

    assert response.status_code == 200

    # Verify credentials were deleted
    creds = db_session.get(SlackUserCredentials, slack_credentials.id)
    assert creds is None

    # Workspace should also be deleted (no other users)
    workspace = db_session.get(SlackWorkspace, slack_workspace.id)
    assert workspace is None


def test_disconnect_workspace_keeps_for_other_users(
    client, db_session, user, other_user, slack_workspace, slack_credentials
):
    """Disconnect doesn't delete workspace if other users have credentials."""
    # Add credentials for other user
    other_creds = SlackUserCredentials(
        workspace_id=slack_workspace.id,
        user_id=other_user.id,
    )
    other_creds.access_token = "xoxp-other-token"
    db_session.add(other_creds)
    db_session.commit()

    response = client.delete(f"/slack/workspaces/{slack_workspace.id}")

    assert response.status_code == 200

    # User's credentials deleted
    creds = db_session.get(SlackUserCredentials, slack_credentials.id)
    assert creds is None

    # Workspace still exists (other user has credentials)
    workspace = db_session.get(SlackWorkspace, slack_workspace.id)
    assert workspace is not None


def test_disconnect_workspace_not_found(client, db_session, user):
    """Disconnect workspace returns 404 when user has no credentials."""
    response = client.delete("/slack/workspaces/T_NONEXISTENT")

    assert response.status_code == 404


def test_disconnect_workspace_cascades_channels(
    client, db_session, user, slack_workspace, slack_credentials, slack_channel
):
    """Disconnect workspace also deletes channels when workspace is removed."""
    channel_id = slack_channel.id

    response = client.delete(f"/slack/workspaces/{slack_workspace.id}")

    assert response.status_code == 200

    # Verify channel was also deleted
    channel = db_session.get(SlackChannel, channel_id)
    assert channel is None


# ====== GET /slack/workspaces/{workspace_id}/channels tests ======


def test_list_channels_success(
    client, db_session, user, slack_workspace, slack_credentials, slack_channel
):
    """List channels returns channels for workspace."""
    response = client.get(f"/slack/workspaces/{slack_workspace.id}/channels")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == slack_channel.id
    assert data[0]["name"] == "general"
    assert data[0]["channel_type"] == "channel"


def test_list_channels_workspace_not_found(client, db_session, user):
    """List channels returns 404 when user has no access."""
    response = client.get("/slack/workspaces/T_NONEXISTENT/channels")

    assert response.status_code == 404


# ====== PATCH /slack/channels/{channel_id} tests ======


def test_update_channel_collect_messages(
    client, db_session, user, slack_workspace, slack_credentials, slack_channel
):
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


def test_update_channel_inherit_collect_messages(
    client, db_session, user, slack_workspace, slack_credentials, slack_channel
):
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
def test_trigger_sync_success(
    mock_app, client, db_session, user, slack_workspace, slack_credentials
):
    """Trigger sync sends task to Celery."""
    response = client.post(f"/slack/workspaces/{slack_workspace.id}/sync")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "sync_triggered"
    assert data["workspace_id"] == slack_workspace.id

    mock_app.send_task.assert_called_once()


def test_trigger_sync_workspace_not_found(client, db_session, user):
    """Trigger sync returns 404 when user has no access."""
    response = client.post("/slack/workspaces/T_NONEXISTENT/sync")

    assert response.status_code == 404


# ====== GET /slack/authorize tests ======


@patch("memory.api.slack.settings")
def test_authorize_not_configured(mock_settings, client, db_session, user):
    """Authorize returns error when Slack is not configured."""
    mock_settings.SLACK_CLIENT_ID = ""
    mock_settings.SLACK_CLIENT_SECRET = ""

    response = client.get("/slack/authorize")

    assert response.status_code == 503
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
    states = db_session.query(OAuthClientState).filter_by(user_id=user.id, provider="slack").all()
    assert len(states) == 1


# ====== OAuth State tests ======


def test_oauth_state_expiration(db_session, user):
    """Test OAuth state expiration check."""
    # Create expired state
    expired_state = OAuthClientState(
        state="expired_state",
        provider="slack",
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=15),
    )
    db_session.add(expired_state)

    # Create valid state
    valid_state = OAuthClientState(
        state="valid_state",
        provider="slack",
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    db_session.add(valid_state)
    db_session.commit()

    # Check expiration
    assert expired_state.expires_at < datetime.now(timezone.utc)
    assert valid_state.expires_at > datetime.now(timezone.utc)


# ====== Model tests ======


def test_slack_credentials_token_encryption(db_session, user, slack_workspace):
    """Test that tokens are encrypted when stored."""
    credentials = SlackUserCredentials(
        workspace_id=slack_workspace.id,
        user_id=user.id,
    )
    credentials.access_token = "xoxp-secret-token"
    credentials.refresh_token = "xoxr-refresh-token"
    db_session.add(credentials)
    db_session.commit()

    # Raw encrypted values should not equal plaintext
    assert credentials.access_token_encrypted != b"xoxp-secret-token"
    assert credentials.refresh_token_encrypted != b"xoxr-refresh-token"

    # Decrypted values should match
    assert credentials.access_token == "xoxp-secret-token"
    assert credentials.refresh_token == "xoxr-refresh-token"


def test_slack_credentials_token_expiration(db_session, user, slack_workspace):
    """Test token expiration check."""
    credentials = SlackUserCredentials(
        workspace_id=slack_workspace.id,
        user_id=user.id,
    )
    db_session.add(credentials)
    db_session.commit()

    # No expiration = not expired
    assert credentials.is_token_expired() is False

    # Future expiration = not expired
    credentials.token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    db_session.commit()
    assert credentials.is_token_expired() is False

    # Past expiration = expired
    credentials.token_expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.commit()
    assert credentials.is_token_expired() is True


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


def test_multi_user_workspace_access(db_session, user, other_user, slack_workspace):
    """Test multiple users can have credentials for the same workspace."""
    # Create credentials for both users
    creds1 = SlackUserCredentials(
        workspace_id=slack_workspace.id,
        user_id=user.id,
    )
    creds1.access_token = "xoxp-user1-token"

    creds2 = SlackUserCredentials(
        workspace_id=slack_workspace.id,
        user_id=other_user.id,
    )
    creds2.access_token = "xoxp-user2-token"

    db_session.add_all([creds1, creds2])
    db_session.commit()

    # Both credentials should exist
    all_creds = db_session.query(SlackUserCredentials).filter_by(
        workspace_id=slack_workspace.id
    ).all()
    assert len(all_creds) == 2

    # Verify user tokens are different
    user_ids = {c.user_id for c in all_creds}
    assert user.id in user_ids
    assert other_user.id in user_ids
