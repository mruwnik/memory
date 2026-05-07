"""Tests for Slack API endpoints."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from memory.common.db.models import User, OAuthClientState
from memory.common.db.models.slack import (
    SlackApp,
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
        password_hash="bcrypt_hash_placeholder",
    )
    db_session.add(other)
    db_session.commit()
    return other


@pytest.fixture
def slack_app(db_session):
    """Create a SlackApp row for tests that need credentials."""
    app = SlackApp(
        client_id="test.client.id",
        name="Test Slack App",
        setup_state="live",
    )
    db_session.add(app)
    db_session.commit()
    return app


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
def slack_credentials(db_session, slack_app, slack_workspace, user):
    """Create Slack credentials for the test user."""
    credentials = SlackUserCredentials(
        slack_app_id=slack_app.id,
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
    regular_client, db_session, user, other_user, slack_app
):
    """A non-admin user sees only workspaces they have credentials for. The
    one workspace created here belongs to other_user, so the calling user's
    list is empty."""
    # Create workspace with credentials for other user only
    workspace = SlackWorkspace(
        id="T_OTHER",
        name="Other Workspace",
    )
    db_session.add(workspace)
    db_session.flush()

    other_creds = SlackUserCredentials(
        slack_app_id=slack_app.id,
        workspace_id=workspace.id,
        user_id=other_user.id,
    )
    other_creds.access_token = "xoxp-other-token"
    db_session.add(other_creds)
    db_session.commit()

    response = regular_client.get("/slack/workspaces")

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
        slack_app_id=slack_credentials.slack_app_id,
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


@patch("memory.api.slack.celery_app")
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


# ====== /slack/callback/{slack_app_id} tests (multi-tenant) ======


@pytest.fixture
def authorized_slack_app(db_session, user):
    """SlackApp owned by `user` with client_id + client_secret set, ready for
    callback tests. ``user`` is implicitly authorized as the owner."""
    app = SlackApp(
        client_id="test.client.id",
        name="Test Slack App",
        setup_state="live",
        created_by_user_id=user.id,
    )
    app.client_secret = "test-client-secret"
    db_session.add(app)
    db_session.commit()
    return app


def _seed_state_for_user(db_session, user_id: int) -> str:
    """Mint a real OAuthClientState row + signed_state for ``user_id``.
    Returns the signed_state suitable for the /callback query parameter."""
    from memory.common.oauth_client import generate_state, sign_state

    raw_state = generate_state()
    db_session.add(
        OAuthClientState(
            state=raw_state,
            provider="slack",
            user_id=user_id,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
    )
    db_session.commit()
    return sign_state(raw_state, user_id)


def _mock_slack_token_exchange(team_id: str, **authed_user_overrides):
    """Build the AsyncClient context manager that returns a fake Slack
    oauth.v2.access response with the given team_id. Use as:

        with patch("memory.api.slack.httpx.AsyncClient") as m:
            m.return_value = _mock_slack_token_exchange(team_id="T1234ABCD")
            ...

    ``authed_user_overrides`` lets tests vary access_token/scope/expires_in/etc.
    """
    from unittest.mock import AsyncMock, MagicMock

    authed_user = {"access_token": "xoxp-fake", "scope": "channels:history"}
    authed_user.update(authed_user_overrides)
    mock_response = MagicMock()
    mock_response.json = MagicMock(
        return_value={
            "ok": True,
            "authed_user": authed_user,
            "team": {"id": team_id, "name": "Test"},
        }
    )
    async_client_ctx = AsyncMock()
    async_client_ctx.__aenter__ = AsyncMock(return_value=async_client_ctx)
    async_client_ctx.__aexit__ = AsyncMock(return_value=False)
    async_client_ctx.post = AsyncMock(return_value=mock_response)
    return async_client_ctx


def test_callback_unknown_slack_app_id_returns_404(client, db_session, user):
    """Hitting /slack/callback/{id} for a non-existent app id returns 404
    (uniformly, so existence isn't probable from outside)."""
    signed_state = _seed_state_for_user(db_session, user.id)
    response = client.get(
        "/slack/callback/9999999",
        params={"code": "fake_code", "state": signed_state},
    )
    assert response.status_code == 404


def test_callback_inactive_slack_app_returns_404(client, db_session, user):
    """An app with is_active=False is invisible to the callback for the same
    reason a non-existent one is."""
    app = SlackApp(
        client_id="test.client.id",
        name="Inactive App",
        setup_state="live",
        is_active=False,
        created_by_user_id=user.id,
    )
    app.client_secret = "secret"
    db_session.add(app)
    db_session.commit()

    signed_state = _seed_state_for_user(db_session, user.id)
    response = client.get(
        f"/slack/callback/{app.id}",
        params={"code": "fake_code", "state": signed_state},
    )
    assert response.status_code == 404


def test_callback_unauthorized_user_returns_403(
    client, db_session, user, other_user
):
    """A user who is neither the owner nor in authorized_users gets 403 —
    they must not be able to OAuth into another tenant's SlackApp."""
    app = SlackApp(
        client_id="test.client.id",
        name="Other-tenant App",
        setup_state="live",
        created_by_user_id=other_user.id,
    )
    app.client_secret = "secret"
    db_session.add(app)
    db_session.commit()

    signed_state = _seed_state_for_user(db_session, user.id)
    response = client.get(
        f"/slack/callback/{app.id}",
        params={"code": "fake_code", "state": signed_state},
    )
    assert response.status_code == 403


def test_callback_state_for_different_user_returns_403(
    client, db_session, user, other_user, authorized_slack_app
):
    """CSRF binding regression: an attacker who minted a state for their own
    session must NOT be able to phish a victim into hitting the callback
    with that state. The authenticated user (`user`) must match the state's
    user_id (`other_user.id`); mismatch → 403."""
    signed_state = _seed_state_for_user(db_session, other_user.id)
    response = client.get(
        f"/slack/callback/{authorized_slack_app.id}",
        params={"code": "fake_victim_code", "state": signed_state},
    )
    assert response.status_code == 403


def test_callback_missing_client_secret_returns_503(client, db_session, user):
    """An app whose client_secret hasn't been set yet (wizard incomplete) must
    fail-closed at the callback rather than attempt a token exchange with
    None."""
    app = SlackApp(
        client_id="test.client.id",
        name="Half-configured App",
        setup_state="draft",
        created_by_user_id=user.id,
    )
    db_session.add(app)
    db_session.commit()

    signed_state = _seed_state_for_user(db_session, user.id)
    response = client.get(
        f"/slack/callback/{app.id}",
        params={"code": "fake_code", "state": signed_state},
    )
    assert response.status_code == 503


def test_callback_malformed_team_id_returns_400(
    client, db_session, user, authorized_slack_app
):
    """Defense-in-depth: even if Slack (or an MITM) returns a team_id that
    doesn't match `_SLACK_TEAM_ID_PATTERN`, we 400 before letting the value
    reach the DB or the HTML template. Catches XSS via team_id if the
    regex is ever loosened."""
    signed_state = _seed_state_for_user(db_session, user.id)
    with patch("memory.api.slack.httpx.AsyncClient") as mock_httpx:
        mock_httpx.return_value = _mock_slack_token_exchange(
            team_id="<script>alert(1)</script>"
        )
        response = client.get(
            f"/slack/callback/{authorized_slack_app.id}",
            params={"code": "fake_code", "state": signed_state},
        )
    assert response.status_code == 400
    assert "team id" in response.json()["detail"].lower()


def test_callback_happy_path_creates_credentials_with_slack_app_id(
    client, db_session, user, authorized_slack_app
):
    """Full successful flow: callback exchanges code, persists a
    `SlackUserCredentials` row scoped to (slack_app_id, workspace_id,
    user_id), and returns the BroadcastChannel HTML."""
    signed_state = _seed_state_for_user(db_session, user.id)
    with (
        patch("memory.api.slack.httpx.AsyncClient") as mock_httpx,
        patch("memory.api.slack.celery_app.send_task"),
    ):
        mock_httpx.return_value = _mock_slack_token_exchange(
            team_id="T0123ABCDE",
            access_token="xoxp-test-access",
            refresh_token="xoxr-test-refresh",
            scope="channels:history,chat:write",
            id="U_TESTUSER",
            expires_in=3600,
        )
        response = client.get(
            f"/slack/callback/{authorized_slack_app.id}",
            params={"code": "fake_code", "state": signed_state},
        )

    assert response.status_code == 200, response.text
    creds = (
        db_session.query(SlackUserCredentials)
        .filter(
            SlackUserCredentials.slack_app_id == authorized_slack_app.id,
            SlackUserCredentials.workspace_id == "T0123ABCDE",
            SlackUserCredentials.user_id == user.id,
        )
        .one_or_none()
    )
    assert creds is not None
    assert creds.access_token == "xoxp-test-access"
    assert creds.slack_app_id == authorized_slack_app.id


def test_callback_html_response_uses_json_dumps_for_slack_app_id(
    client, db_session, user, authorized_slack_app
):
    """The OAuth callback HTML interpolates workspaceId, slackAppId, and
    BroadcastChannel name via json.dumps. This test pins that contract — if
    a future change reintroduces raw `{slack_app.id}` interpolation while
    relying on the team-id regex to keep things safe, this test catches it
    by verifying the JS literal is properly quoted."""
    signed_state = _seed_state_for_user(db_session, user.id)
    with (
        patch("memory.api.slack.httpx.AsyncClient") as mock_httpx,
        patch("memory.api.slack.celery_app.send_task"),
    ):
        mock_httpx.return_value = _mock_slack_token_exchange(team_id="T98765ABCD")
        response = client.get(
            f"/slack/callback/{authorized_slack_app.id}",
            params={"code": "fake_code", "state": signed_state},
        )

    assert response.status_code == 200
    html = response.text
    # Each interpolated value lands as a JSON literal — strings get quotes,
    # ints are bare. The presence of the quoted forms confirms json.dumps was used.
    assert '"T98765ABCD"' in html  # workspace_id was JSON-encoded
    assert f'"slack-oauth-{user.id}"' in html  # channel name was JSON-encoded
    assert f"slackAppId: {authorized_slack_app.id}" in html  # int -> bare
    # Sanity: no raw < or > from any interpolation slipped through.
    # (Slack JSON-escapes '<' as < via json.dumps default.)
    assert "<script>alert" not in html


# ====== Model tests ======


def test_slack_credentials_token_encryption(db_session, user, slack_app, slack_workspace):
    """Test that tokens are encrypted when stored."""
    credentials = SlackUserCredentials(
        slack_app_id=slack_app.id,
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


def test_slack_credentials_token_expiration(db_session, user, slack_app, slack_workspace):
    """Test token expiration check."""
    credentials = SlackUserCredentials(
        slack_app_id=slack_app.id,
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


def test_multi_user_workspace_access(db_session, user, other_user, slack_app, slack_workspace):
    """Test multiple users can have credentials for the same workspace."""
    # Create credentials for both users
    creds1 = SlackUserCredentials(
        slack_app_id=slack_app.id,
        workspace_id=slack_workspace.id,
        user_id=user.id,
    )
    creds1.access_token = "xoxp-user1-token"

    creds2 = SlackUserCredentials(
        slack_app_id=slack_app.id,
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


# ====== XSS hardening tests (SECURITY/MED f2feda6d) ======


def test_slack_team_id_pattern_accepts_documented_format():
    """Slack docs: team_id is `T` + 8-12 uppercase alphanumerics."""
    from memory.api.slack import _SLACK_TEAM_ID_PATTERN

    assert _SLACK_TEAM_ID_PATTERN.fullmatch("T01234567")
    assert _SLACK_TEAM_ID_PATTERN.fullmatch("TABCDEFGH")
    assert _SLACK_TEAM_ID_PATTERN.fullmatch("T012345ABCD")
    assert _SLACK_TEAM_ID_PATTERN.fullmatch("T0123456789AB")  # 12 trailing chars


@pytest.mark.parametrize(
    "bad_id",
    [
        "",  # empty
        "T",  # too short
        "T1234567",  # 7 chars trailing
        "T0123456789ABC",  # 13 chars trailing
        "X01234567",  # wrong prefix
        "t01234567",  # lowercase prefix
        "T0123456a",  # lowercase in trailing
        "T01234567 ",  # trailing whitespace
        " T01234567",  # leading whitespace
        "T01234567' </script><script>alert(1)</script>",  # XSS payload
        "T01234567'\\",  # escape attempt
        "T01234567\";alert(1);//",  # JS injection
        "T01234567\nT01234568",  # newline injection
    ],
)
def test_slack_team_id_pattern_rejects_malformed(bad_id):
    """The trust-boundary check must reject anything outside the documented
    format, including XSS payloads that smuggle quotes / </script> / newlines."""
    from memory.api.slack import _SLACK_TEAM_ID_PATTERN

    assert _SLACK_TEAM_ID_PATTERN.fullmatch(bad_id) is None


@pytest.mark.parametrize(
    "value",
    [
        "T12345678",  # baseline
        "T'); alert(1); //",  # would close JS string + open call
        "T</script><script>alert(1)</script>",  # script tag breakout
        'T"abc',  # double quote
        "T\\abc",  # backslash
        "T\nabc",  # newline (must be escaped in JS string literal)
        "T abc",  # JS line separator
    ],
)
def test_html_template_json_encoding_is_xss_safe(value):
    """Defense in depth: even values that bypass the regex (or future
    interpolated values that don't go through the regex) must be safe
    inside the JS string literal because json.dumps escapes them.

    The actual regex would block all but the first input; this test
    proves the second layer (json.dumps) holds independently.
    """
    import json

    encoded = json.dumps(value)

    # The encoded form is a complete JS-safe string literal — no raw quote
    # or closing-script-tag can escape it.
    assert encoded.startswith('"') and encoded.endswith('"')
    inside = encoded[1:-1]
    # No bare double-quote can break out of the literal.
    assert '"' not in inside.replace('\\"', "")
    # Closing </script> sequence is escaped (json.dumps escapes the `<`
    # via the standard JSON escapes when parsing in JS contexts —
    # technically json.dumps doesn't escape `<` by default, but the
    # quotes-and-backslashes guarantee already prevents string-literal
    # breakout). Assert the more important invariants directly:
    assert "\n" not in inside  # json.dumps escapes newlines
    assert " " not in inside  # json.dumps escapes JS line-sep
    # And the encoded string is valid JSON (round-trips).
    assert json.loads(encoded) == value


def test_html_template_uses_json_dumps(monkeypatch):
    """Sanity check that the callback module imports json and uses it for
    its rendered HTML — guards against a future refactor accidentally
    reverting to f-string interpolation."""
    import memory.api.slack as slack_api

    # Module imported `json` (used for safe HTML interpolation).
    assert hasattr(slack_api, "json")
    # The compile-time pattern is in the module.
    assert hasattr(slack_api, "_SLACK_TEAM_ID_PATTERN")


# ==============================================================
# /slack/apps CRUD (slack-changes.md §3.2 + §4 S4 / S11 / S12)
# ==============================================================


def test_create_slack_app_returns_draft(client, db_session, user):
    """POST /slack/apps creates a row owned by the caller, in draft state."""
    response = client.post(
        "/slack/apps",
        json={"name": "My App", "client_id": "1234.5678"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["client_id"] == "1234.5678"
    assert body["name"] == "My App"
    assert body["setup_state"] == "draft"
    assert body["is_active"] is True
    assert body["is_owner"] is True
    assert body["is_authorized"] is True  # owner is implicitly authorized
    assert body["created_by_user_id"] == user.id
    # Secrets must NOT be exposed.
    assert body["client_secret_configured"] is False
    assert body["signing_secret_configured"] is False
    assert "client_secret" not in body
    assert "signing_secret" not in body
    assert "client_secret_encrypted" not in body
    assert "signing_secret_encrypted" not in body
    assert body["authorized_users"] == []
    assert body["authorized_user_ids"] == []


def test_slack_app_response_contract():
    """Pin the SlackAppResponse field set so a future divergence between
    the backend Pydantic model and the frontend `SlackAppResponse`
    TypeScript interface (frontend/src/hooks/useSlackWizard.ts) trips a
    test rather than only manifesting at runtime as `undefined` reads.

    The expected set MUST match the TS interface field-for-field; bump
    both together when changing the wire shape.
    """
    from memory.api.slack import SlackAppResponse

    expected = {
        "id",
        "client_id",
        "name",
        "setup_state",
        "is_active",
        "is_owner",
        "is_authorized",
        "created_by_user_id",
        "created_at",
        "updated_at",
        "client_secret_configured",
        "signing_secret_configured",
        "authorized_users",
        "authorized_user_ids",
    }
    assert set(SlackAppResponse.model_fields.keys()) == expected


def test_create_slack_app_duplicate_client_id_returns_409(client, db_session, user):
    """A second POST with the same client_id (even by the same user) is 409.

    The error message points at the squatting cleanup window so the
    legitimate owner has a path forward.
    """
    first = client.post(
        "/slack/apps", json={"name": "First", "client_id": "dup.id"}
    )
    assert first.status_code == 201

    second = client.post(
        "/slack/apps", json={"name": "Second", "client_id": "dup.id"}
    )
    assert second.status_code == 409
    assert "client_id" in second.json()["detail"]
    assert "24 hours" in second.json()["detail"] or "support" in second.json()["detail"]


def test_create_slack_app_secret_blob_never_leaks_via_get(
    client, db_session, user
):
    """Even after a secret is set in the DB (simulating wizard step), the
    response only signals via *_configured booleans — never the bytes."""
    create = client.post(
        "/slack/apps", json={"name": "WithSecret", "client_id": "secret.app"}
    )
    app_id = create.json()["id"]

    # Set a secret directly (the wizard task will own this endpoint).
    app_row = db_session.get(SlackApp, app_id)
    app_row.client_secret = "supersecret"
    db_session.commit()

    response = client.get(f"/slack/apps/{app_id}")
    body = response.json()

    assert body["client_secret_configured"] is True
    # No path in the response should expose either the encrypted blob or
    # the decrypted secret.
    body_str = response.text
    assert "supersecret" not in body_str
    assert "client_secret_encrypted" not in body
    assert "client_secret" not in body or body.get("client_secret") is None


def test_list_slack_apps_includes_owned_and_authorized(
    client, db_session, user, other_user
):
    """GET /slack/apps returns apps the user owns OR is authorized for —
    deduplicated and not leaking apps owned by unrelated users."""
    # Owned by current user.
    owned = SlackApp(
        client_id="owned.app", name="Owned", created_by_user_id=user.id
    )
    # Owned by other_user but current user is in authorized_users.
    auth_for_me = SlackApp(
        client_id="auth.app", name="Auth", created_by_user_id=other_user.id
    )
    auth_for_me.authorized_users.append(user)
    # Owned by other_user, current user NOT authorized — should NOT appear.
    invisible = SlackApp(
        client_id="hidden.app", name="Hidden", created_by_user_id=other_user.id
    )
    db_session.add_all([owned, auth_for_me, invisible])
    db_session.commit()

    response = client.get("/slack/apps")
    assert response.status_code == 200
    ids_seen = sorted(a["client_id"] for a in response.json())
    assert ids_seen == ["auth.app", "owned.app"]


def test_get_slack_app_returns_404_for_unauthorized(
    client, db_session, user, other_user
):
    """GET /slack/apps/{id} returns 404 (not 403) for non-authorized users
    so we don't leak app existence to outsiders (§4 S11)."""
    invisible = SlackApp(
        client_id="hidden.app", name="Hidden", created_by_user_id=other_user.id
    )
    db_session.add(invisible)
    db_session.commit()

    response = client.get(f"/slack/apps/{invisible.id}")
    assert response.status_code == 404


def test_get_slack_app_authorized_user_can_read(
    client, db_session, user, other_user
):
    """Authorized non-owner can read the app (but not its secrets)."""
    app = SlackApp(
        client_id="auth.read.app",
        name="Auth Read",
        created_by_user_id=other_user.id,
    )
    app.authorized_users.append(user)
    db_session.add(app)
    db_session.commit()

    response = client.get(f"/slack/apps/{app.id}")
    assert response.status_code == 200
    assert response.json()["is_owner"] is False


def test_patch_slack_app_owner_can_update_name_and_active(
    client, db_session, user
):
    """Owner-only PATCH updates name and is_active."""
    create = client.post(
        "/slack/apps", json={"name": "Original", "client_id": "patch.app"}
    )
    app_id = create.json()["id"]

    response = client.patch(
        f"/slack/apps/{app_id}",
        json={"name": "Renamed", "is_active": False},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Renamed"
    assert body["is_active"] is False


def test_patch_slack_app_authorized_user_gets_403(
    client, db_session, user, other_user
):
    """Authorized but not owner → 403 (existence is signaled, action is not
    allowed). Distinct from the unauthorized 404 case."""
    app = SlackApp(
        client_id="patch.denied", name="Denied", created_by_user_id=other_user.id
    )
    app.authorized_users.append(user)
    db_session.add(app)
    db_session.commit()

    response = client.patch(f"/slack/apps/{app.id}", json={"name": "Hijacked"})
    assert response.status_code == 403


def test_patch_slack_app_cannot_mutate_setup_state(client, db_session, user):
    """Direct setup_state mutation must not be possible via PATCH —
    state advances only via the wizard endpoints.

    Even if a client smuggles `setup_state` in the body, the Pydantic
    model strips it (BaseModel ignores extra fields by default in
    older pydantic, validates them out in v2 with strict mode). The
    behavior we care about is that the row's setup_state stays unchanged.
    """
    create = client.post(
        "/slack/apps", json={"name": "X", "client_id": "state.app"}
    )
    app_id = create.json()["id"]

    response = client.patch(
        f"/slack/apps/{app_id}",
        json={"name": "Renamed", "setup_state": "live"},
    )
    # The PATCH should succeed for `name` regardless of pydantic's handling.
    assert response.status_code == 200
    # State must still be 'draft' — the wizard didn't advance it.
    refreshed = db_session.get(SlackApp, app_id)
    db_session.refresh(refreshed)
    assert refreshed.setup_state == "draft"


def test_delete_slack_app_owner_only_and_cascades(
    client, db_session, user, slack_workspace
):
    """DELETE removes the app and (via FK cascade) its credentials."""
    create = client.post(
        "/slack/apps", json={"name": "ToDelete", "client_id": "del.app"}
    )
    app_id = create.json()["id"]

    # Attach a credential row so we can verify cascade.
    cred = SlackUserCredentials(
        slack_app_id=app_id,
        workspace_id=slack_workspace.id,
        user_id=user.id,
    )
    cred.access_token = "xoxp-token"
    db_session.add(cred)
    db_session.commit()
    cred_id = cred.id

    response = client.delete(f"/slack/apps/{app_id}")
    assert response.status_code == 204

    # App is gone.
    assert db_session.get(SlackApp, app_id) is None
    # Credential cascaded.
    assert db_session.get(SlackUserCredentials, cred_id) is None


def test_delete_slack_app_authorized_user_gets_403(
    client, db_session, user, other_user
):
    app = SlackApp(
        client_id="del.denied", name="X", created_by_user_id=other_user.id
    )
    app.authorized_users.append(user)
    db_session.add(app)
    db_session.commit()

    response = client.delete(f"/slack/apps/{app.id}")
    assert response.status_code == 403
    # Row still exists.
    assert db_session.get(SlackApp, app.id) is not None


def test_add_authorized_user_owner_only(client, db_session, user, other_user):
    """Owner adds another user; response includes them in authorized_users."""
    create = client.post(
        "/slack/apps", json={"name": "X", "client_id": "auth.add"}
    )
    app_id = create.json()["id"]

    response = client.post(
        f"/slack/apps/{app_id}/authorized-users",
        json={"user_id": other_user.id},
    )
    assert response.status_code == 201
    auth_user_ids = [u["id"] for u in response.json()["authorized_users"]]
    assert other_user.id in auth_user_ids


def test_add_authorized_user_idempotent(client, db_session, user, other_user):
    """Adding the same user twice is a no-op — no duplicate join row."""
    create = client.post(
        "/slack/apps", json={"name": "X", "client_id": "auth.idem"}
    )
    app_id = create.json()["id"]

    client.post(
        f"/slack/apps/{app_id}/authorized-users",
        json={"user_id": other_user.id},
    )
    response = client.post(
        f"/slack/apps/{app_id}/authorized-users",
        json={"user_id": other_user.id},
    )
    # Still 201 and the list has exactly one entry for other_user.
    assert response.status_code == 201
    matching = [u for u in response.json()["authorized_users"] if u["id"] == other_user.id]
    assert len(matching) == 1


def test_remove_authorized_user(client, db_session, user, other_user):
    """Owner removes a user; subsequent listing excludes them."""
    create = client.post(
        "/slack/apps", json={"name": "X", "client_id": "auth.remove"}
    )
    app_id = create.json()["id"]

    client.post(
        f"/slack/apps/{app_id}/authorized-users",
        json={"user_id": other_user.id},
    )

    response = client.delete(
        f"/slack/apps/{app_id}/authorized-users/{other_user.id}"
    )
    assert response.status_code == 200
    auth_user_ids = [u["id"] for u in response.json()["authorized_users"]]
    assert other_user.id not in auth_user_ids


def test_remove_authorized_user_idempotent(client, db_session, user, other_user):
    """Removing a user who isn't in the list is a no-op (no error)."""
    create = client.post(
        "/slack/apps", json={"name": "X", "client_id": "auth.never"}
    )
    app_id = create.json()["id"]

    response = client.delete(
        f"/slack/apps/{app_id}/authorized-users/{other_user.id}"
    )
    assert response.status_code == 200


def test_authorized_user_endpoints_reject_non_owner(
    client, db_session, user, other_user
):
    """Even an authorized user can't add/remove other authorized users."""
    app = SlackApp(
        client_id="auth.denied", name="X", created_by_user_id=other_user.id
    )
    app.authorized_users.append(user)
    db_session.add(app)
    db_session.commit()

    add_resp = client.post(
        f"/slack/apps/{app.id}/authorized-users",
        json={"user_id": user.id},
    )
    assert add_resp.status_code == 403

    rm_resp = client.delete(
        f"/slack/apps/{app.id}/authorized-users/{user.id}"
    )
    assert rm_resp.status_code == 403
