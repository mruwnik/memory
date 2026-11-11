"""Tests for Google Drive OAuth API endpoints."""

from datetime import datetime, timezone
from unittest.mock import Mock, patch
from urllib.parse import urlparse

import pytest
from starlette.requests import Request

from memory.api import google_drive
from memory.api.google_drive import (
    AVAILABLE_GOOGLE_SCOPES,
    BASE_GOOGLE_SCOPES,
    GoogleOAuthState,
    google_authorize,
    google_callback,
    get_available_scopes,
    reauthorize_account,
    ReauthorizeRequest,
)
from memory.common import settings
from memory.common.db.models.sources import GoogleAccount


def test_get_available_scopes_returns_all_scopes():
    """Test that available scopes endpoint returns all configured scopes."""
    mock_user = Mock()

    result = get_available_scopes(user=mock_user)

    assert "scopes" in result
    assert result["scopes"] == AVAILABLE_GOOGLE_SCOPES
    assert "drive" in result["scopes"]
    assert "gmail_send" in result["scopes"]
    assert "gmail_read" in result["scopes"]
    assert "calendar" in result["scopes"]


def test_default_redirect_uri_is_the_callback_route():
    assert urlparse(settings.GOOGLE_REDIRECT_URI).path == google_drive.router.url_path_for(
        "google_callback"
    )


def test_available_scopes_have_required_fields():
    """Test that each scope has label, scope, and description."""
    for key, info in AVAILABLE_GOOGLE_SCOPES.items():
        assert "scope" in info, f"Scope {key} missing 'scope' field"
        assert "label" in info, f"Scope {key} missing 'label' field"
        assert "description" in info, f"Scope {key} missing 'description' field"
        assert info["scope"].startswith("https://"), f"Scope {key} has invalid scope URL"


@patch("memory.api.google_drive.get_oauth_config")
@patch("google_auth_oauthlib.flow.Flow")
@patch("memory.api.google_drive.GoogleOAuthState")
def test_google_authorize_with_specific_scopes(mock_state, mock_flow_class, mock_get_config):
    """Test that authorize endpoint uses specified scopes."""
    mock_config = Mock()
    mock_config.redirect_uris = ["http://localhost/callback"]
    mock_config.to_client_config.return_value = {"web": {}}
    mock_get_config.return_value = mock_config

    mock_flow = Mock()
    mock_flow.authorization_url.return_value = ("https://auth.url", None)
    mock_flow_class.from_client_config.return_value = mock_flow

    mock_state.create.return_value = "state123"

    mock_user = Mock(id=1)
    mock_db = Mock()

    result = google_authorize(
        scopes=["drive", "gmail_send"],
        user=mock_user,
        db=mock_db,
    )

    # Verify the flow was created with the correct scopes
    call_args = mock_flow_class.from_client_config.call_args
    requested_scopes = call_args[1]["scopes"]

    # Should include base scopes
    for base_scope in BASE_GOOGLE_SCOPES:
        assert base_scope in requested_scopes

    # Should include requested scopes
    assert AVAILABLE_GOOGLE_SCOPES["drive"]["scope"] in requested_scopes
    assert AVAILABLE_GOOGLE_SCOPES["gmail_send"]["scope"] in requested_scopes

    # Should NOT include non-requested scopes
    assert AVAILABLE_GOOGLE_SCOPES["calendar"]["scope"] not in requested_scopes
    assert AVAILABLE_GOOGLE_SCOPES["gmail_read"]["scope"] not in requested_scopes

    assert "authorization_url" in result


@patch("memory.api.google_drive.get_oauth_config")
@patch("google_auth_oauthlib.flow.Flow")
@patch("memory.api.google_drive.GoogleOAuthState")
def test_google_authorize_disables_pkce(mock_state, mock_flow_class, mock_get_config):
    """PKCE must be off: the callback builds a fresh Flow with no code_verifier,
    so a code_challenge in the auth URL would fail token exchange with
    'invalid_grant: Missing code verifier'."""
    mock_config = Mock()
    mock_config.redirect_uris = ["http://localhost/callback"]
    mock_config.to_client_config.return_value = {"web": {}}
    mock_get_config.return_value = mock_config

    mock_flow = Mock()
    mock_flow.authorization_url.return_value = ("https://auth.url", None)
    mock_flow_class.from_client_config.return_value = mock_flow

    mock_state.create.return_value = "state123"

    google_authorize(scopes=["drive"], user=Mock(id=1), db=Mock())

    assert mock_flow.autogenerate_code_verifier is False


@patch("memory.api.google_drive.get_oauth_config")
@patch("google_auth_oauthlib.flow.Flow")
@patch("memory.api.google_drive.GoogleOAuthState")
def test_google_authorize_without_scopes_uses_defaults(mock_state, mock_flow_class, mock_get_config):
    """Test that authorize endpoint uses default scopes when none specified."""
    mock_config = Mock()
    mock_config.redirect_uris = ["http://localhost/callback"]
    mock_config.to_client_config.return_value = {"web": {}}
    mock_get_config.return_value = mock_config

    mock_flow = Mock()
    mock_flow.authorization_url.return_value = ("https://auth.url", None)
    mock_flow_class.from_client_config.return_value = mock_flow

    mock_state.create.return_value = "state123"

    mock_user = Mock(id=1)
    mock_db = Mock()

    result = google_authorize(
        scopes=None,
        user=mock_user,
        db=mock_db,
    )

    # Verify the flow was created with all available scopes
    call_args = mock_flow_class.from_client_config.call_args
    requested_scopes = call_args[1]["scopes"]

    # Should include base scopes + all available scopes
    for base_scope in BASE_GOOGLE_SCOPES:
        assert base_scope in requested_scopes
    for scope_info in AVAILABLE_GOOGLE_SCOPES.values():
        assert scope_info["scope"] in requested_scopes
    assert "authorization_url" in result


@patch("memory.api.google_drive.get_oauth_config")
@patch("memory.api.google_drive.get_user_account")
@patch("google_auth_oauthlib.flow.Flow")
@patch("memory.api.google_drive.GoogleOAuthState")
def test_reauthorize_with_specific_scopes(mock_state, mock_flow_class, mock_get_account, mock_get_config):
    """Test that reauthorize endpoint uses specified scopes."""
    mock_config = Mock()
    mock_config.redirect_uris = ["http://localhost/callback"]
    mock_config.to_client_config.return_value = {"web": {}}
    mock_get_config.return_value = mock_config

    mock_account = Mock(email="test@example.com")
    mock_get_account.return_value = mock_account

    mock_flow = Mock()
    mock_flow.authorization_url.return_value = ("https://auth.url", None)
    mock_flow_class.from_client_config.return_value = mock_flow

    mock_state.create.return_value = "state123"

    mock_user = Mock(id=1)
    mock_db = Mock()

    request = ReauthorizeRequest(scopes=["gmail_read", "calendar"])

    result = reauthorize_account(
        account_id=123,
        request=request,
        user=mock_user,
        db=mock_db,
    )

    # Verify the flow was created with the correct scopes
    call_args = mock_flow_class.from_client_config.call_args
    requested_scopes = call_args[1]["scopes"]

    # Should include base scopes
    for base_scope in BASE_GOOGLE_SCOPES:
        assert base_scope in requested_scopes

    # Should include requested scopes
    assert AVAILABLE_GOOGLE_SCOPES["gmail_read"]["scope"] in requested_scopes
    assert AVAILABLE_GOOGLE_SCOPES["calendar"]["scope"] in requested_scopes

    # Should NOT include non-requested scopes
    assert AVAILABLE_GOOGLE_SCOPES["drive"]["scope"] not in requested_scopes
    assert AVAILABLE_GOOGLE_SCOPES["gmail_send"]["scope"] not in requested_scopes

    # Verify login_hint is set for reauthorization
    auth_url_call = mock_flow.authorization_url.call_args
    assert auth_url_call[1]["login_hint"] == "test@example.com"

    assert "authorization_url" in result


@patch("memory.api.google_drive.get_oauth_config")
@patch("memory.api.google_drive.get_user_account")
@patch("google_auth_oauthlib.flow.Flow")
@patch("memory.api.google_drive.GoogleOAuthState")
def test_reauthorize_disables_pkce(mock_state, mock_flow_class, mock_get_account, mock_get_config):
    """Reauthorize must also disable PKCE (same callback-has-no-verifier reason)."""
    mock_config = Mock()
    mock_config.redirect_uris = ["http://localhost/callback"]
    mock_config.to_client_config.return_value = {"web": {}}
    mock_get_config.return_value = mock_config

    mock_get_account.return_value = Mock(email="test@example.com")

    mock_flow = Mock()
    mock_flow.authorization_url.return_value = ("https://auth.url", None)
    mock_flow_class.from_client_config.return_value = mock_flow

    mock_state.create.return_value = "state123"

    reauthorize_account(
        account_id=123,
        request=ReauthorizeRequest(scopes=["drive"]),
        user=Mock(id=1),
        db=Mock(),
    )

    assert mock_flow.autogenerate_code_verifier is False


@patch("memory.api.google_drive.get_oauth_config")
@patch("memory.api.google_drive.get_user_account")
@patch("google_auth_oauthlib.flow.Flow")
@patch("memory.api.google_drive.GoogleOAuthState")
def test_reauthorize_without_scopes_uses_defaults(mock_state, mock_flow_class, mock_get_account, mock_get_config):
    """Test that reauthorize endpoint uses default scopes when none specified."""
    mock_config = Mock()
    mock_config.redirect_uris = ["http://localhost/callback"]
    mock_config.to_client_config.return_value = {"web": {}}
    mock_get_config.return_value = mock_config

    mock_account = Mock(email="test@example.com")
    mock_get_account.return_value = mock_account

    mock_flow = Mock()
    mock_flow.authorization_url.return_value = ("https://auth.url", None)
    mock_flow_class.from_client_config.return_value = mock_flow

    mock_state.create.return_value = "state123"

    mock_user = Mock(id=1)
    mock_db = Mock()

    result = reauthorize_account(
        account_id=123,
        request=None,
        user=mock_user,
        db=mock_db,
    )

    # Verify the flow was created with all available scopes
    call_args = mock_flow_class.from_client_config.call_args
    requested_scopes = call_args[1]["scopes"]

    # Should include base scopes + all available scopes
    for base_scope in BASE_GOOGLE_SCOPES:
        assert base_scope in requested_scopes
    for scope_info in AVAILABLE_GOOGLE_SCOPES.values():
        assert scope_info["scope"] in requested_scopes
    assert "authorization_url" in result


def test_google_authorize_ignores_invalid_scope_keys():
    """Test that invalid scope keys are silently ignored."""
    # This is a unit test of the scope building logic
    from memory.api.google_drive import AVAILABLE_GOOGLE_SCOPES, BASE_GOOGLE_SCOPES

    scopes = ["drive", "invalid_scope", "gmail_send"]

    requested_scopes = BASE_GOOGLE_SCOPES.copy()
    for scope_key in scopes:
        if scope_key in AVAILABLE_GOOGLE_SCOPES:
            requested_scopes.append(AVAILABLE_GOOGLE_SCOPES[scope_key]["scope"])

    # Should have base scopes + drive + gmail_send (invalid_scope ignored)
    assert len(requested_scopes) == len(BASE_GOOGLE_SCOPES) + 2
    assert AVAILABLE_GOOGLE_SCOPES["drive"]["scope"] in requested_scopes
    assert AVAILABLE_GOOGLE_SCOPES["gmail_send"]["scope"] in requested_scopes


GRANTED_SCOPES = (
    "email profile https://www.googleapis.com/auth/drive.readonly "
    "https://www.googleapis.com/auth/gmail.send openid"
)


def callback_request(token: str | None) -> Request:
    cookie = f"{settings.SESSION_COOKIE_NAME}={token}".encode() if token else b""
    headers = [(b"cookie", cookie)] if token else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


@pytest.fixture
def google_exchange(monkeypatch):
    """Stand in for Google's token endpoint and userinfo API; yields the token fetch."""
    config = Mock(redirect_uris=["https://example.com/callback"])
    monkeypatch.setattr(google_drive, "get_oauth_config", Mock(return_value=config))
    flow = Mock()
    flow.credentials = Mock(token="new-access", refresh_token="new-refresh", expiry=None)
    service = Mock()
    service.userinfo().get().execute.return_value = {
        "email": "drive@example.com",
        "name": "Drive Owner",
    }
    with patch("google_auth_oauthlib.flow.Flow") as flow_class, patch(
        "googleapiclient.discovery.build", return_value=service
    ):
        flow_class.from_client_config.return_value = flow
        yield flow.fetch_token


def run_callback(token: str | None, state: str):
    return google_callback(
        request=callback_request(token),
        code="auth_code",
        state=state,
        error=None,
        scope=GRANTED_SCOPES,
    )


def accounts_for(db_session, email: str) -> list[GoogleAccount]:
    db_session.expire_all()
    return db_session.query(GoogleAccount).filter(GoogleAccount.email == email).all()


def test_google_callback_connects_account_for_session_user(
    db_session, regular_user, user_session, google_exchange
):
    state = GoogleOAuthState.create(regular_user.id)

    response = run_callback("test-session-token", state)

    assert response.status_code == 200
    assert f'new BroadcastChannel("google-oauth-{regular_user.id}")' in bytes(
        response.body
    ).decode()
    [account] = accounts_for(db_session, "drive@example.com")
    assert account.user_id == regular_user.id
    assert account.scopes == GRANTED_SCOPES.split()
    assert account.access_token == "new-access"


def test_google_callback_logged_out_keeps_state(
    db_session, regular_user, google_exchange
):
    state = GoogleOAuthState.create(regular_user.id)

    response = run_callback(None, state)

    assert response.status_code == 401
    assert google_exchange.call_count == 0
    assert GoogleOAuthState.owner(state) == regular_user.id
    assert accounts_for(db_session, "drive@example.com") == []


def test_google_callback_rejects_state_of_another_user(
    db_session, admin_user, regular_user, user_session, google_exchange
):
    state = GoogleOAuthState.create(admin_user.id)

    response = run_callback("test-session-token", state)

    assert response.status_code == 403
    assert google_exchange.call_count == 0
    assert GoogleOAuthState.owner(state) == admin_user.id
    assert accounts_for(db_session, "drive@example.com") == []


def test_google_callback_state_is_single_use(
    db_session, regular_user, user_session, google_exchange
):
    state = GoogleOAuthState.create(regular_user.id)
    run_callback("test-session-token", state)

    response = run_callback("test-session-token", state)

    assert response.status_code == 400
    assert google_exchange.call_count == 1


def test_google_callback_refuses_account_owned_by_another_user(
    db_session, admin_user, regular_user, user_session, google_exchange
):
    owned = GoogleAccount(
        user_id=admin_user.id, name="Admin Drive", email="drive@example.com"
    )
    owned.access_token = "admin-access"
    db_session.add(owned)
    db_session.commit()
    state = GoogleOAuthState.create(regular_user.id)

    response = run_callback("test-session-token", state)

    assert response.status_code == 409
    [account] = accounts_for(db_session, "drive@example.com")
    assert account.user_id == admin_user.id
    assert account.access_token == "admin-access"


def test_google_callback_updates_own_account(
    db_session, regular_user, user_session, google_exchange
):
    own = GoogleAccount(
        user_id=regular_user.id, name="My Drive", email="drive@example.com"
    )
    own.access_token = "old-access"
    db_session.add(own)
    db_session.commit()
    state = GoogleOAuthState.create(regular_user.id)

    response = run_callback("test-session-token", state)

    assert response.status_code == 200
    [account] = accounts_for(db_session, "drive@example.com")
    assert account.access_token == "new-access"
    assert account.scopes == GRANTED_SCOPES.split()


def test_callback_scope_parsing_handles_empty_scope():
    """Test that empty scope parameter results in empty list."""
    scope_string = None
    granted_scopes = scope_string.split() if scope_string else []
    assert granted_scopes == []


def test_callback_scope_parsing_handles_space_separated():
    """Test that scopes are correctly split by spaces."""
    scope_string = "email profile https://www.googleapis.com/auth/drive.readonly"
    granted_scopes = scope_string.split()
    assert len(granted_scopes) == 3
    assert "email" in granted_scopes
    assert "profile" in granted_scopes
    assert "https://www.googleapis.com/auth/drive.readonly" in granted_scopes


# Access-control tests for /google-drive/config (admin-only)
#
# This config is a system-wide singleton: every user's Google OAuth flow
# uses it. A non-admin who can write or delete it can re-target every
# subsequent OAuth consent screen at their own client_id and capture
# tokens for any Drive/Gmail/Calendar user. POST and DELETE must therefore
# require admin scope; GET is informational (no secrets) and stays open.


def _credentials_json_bytes() -> bytes:
    """Minimal valid Google OAuth credentials JSON payload."""
    import json as _json

    return _json.dumps(
        {
            "web": {
                "client_id": "fake-client-id",
                "client_secret": "fake-client-secret",
                "project_id": "fake-project",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["https://example.com/callback"],
                "javascript_origins": [],
            }
        }
    ).encode("utf-8")


def test_upload_oauth_config_forbidden_for_non_admin(regular_client, db_session):
    """A non-admin user must not be able to overwrite the global Google OAuth config."""
    from memory.common.db.models.sources import GoogleOAuthConfig

    response = regular_client.post(
        "/google-drive/config",
        files={"file": ("creds.json", _credentials_json_bytes(), "application/json")},
    )

    assert response.status_code == 403
    # No row should have been created.
    assert (
        db_session.query(GoogleOAuthConfig)
        .filter(GoogleOAuthConfig.name == "default")
        .count()
        == 0
    )


def test_delete_oauth_config_forbidden_for_non_admin(regular_client, db_session):
    """A non-admin user must not be able to delete the global Google OAuth config."""
    from memory.common.db.models.sources import GoogleOAuthConfig

    config = GoogleOAuthConfig(
        name="default",
        client_id="legit-client-id",
        client_secret="legit-secret",
        redirect_uris=["https://example.com/callback"],
    )
    db_session.add(config)
    db_session.commit()

    response = regular_client.delete("/google-drive/config")

    assert response.status_code == 403
    # Config must still exist.
    assert (
        db_session.query(GoogleOAuthConfig)
        .filter(GoogleOAuthConfig.name == "default")
        .count()
        == 1
    )


def test_upload_oauth_config_allowed_for_admin(client, db_session):
    """Admin (default test client uses scopes=['*']) can write the OAuth config."""
    from memory.common.db.models.sources import GoogleOAuthConfig

    response = client.post(
        "/google-drive/config",
        files={"file": ("creds.json", _credentials_json_bytes(), "application/json")},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["client_id"] == "fake-client-id"
    assert (
        db_session.query(GoogleOAuthConfig)
        .filter(GoogleOAuthConfig.name == "default")
        .count()
        == 1
    )


def test_delete_oauth_config_allowed_for_admin(client, db_session):
    """Admin can delete the OAuth config."""
    from memory.common.db.models.sources import GoogleOAuthConfig

    config = GoogleOAuthConfig(
        name="default",
        client_id="legit-client-id",
        client_secret="legit-secret",
        redirect_uris=["https://example.com/callback"],
    )
    db_session.add(config)
    db_session.commit()

    response = client.delete("/google-drive/config")

    assert response.status_code == 200
    assert response.json() == {"status": "deleted"}
    assert (
        db_session.query(GoogleOAuthConfig)
        .filter(GoogleOAuthConfig.name == "default")
        .count()
        == 0
    )


def test_list_accounts_reports_folder_sync_failure(client, db_session, user):
    from memory.common.db.models.sources import GoogleAccount, GoogleFolder

    since = datetime(2026, 10, 20, tzinfo=timezone.utc)
    account = GoogleAccount(user_id=user.id, name="Drive", email="drive@example.com")
    db_session.add(
        GoogleFolder(
            account=account,
            folder_id="gone",
            folder_name="Gone",
            sync_error="<HttpError 404 File not found>",
            sync_failing_since=since,
        )
    )
    db_session.commit()

    response = client.get("/google-drive/accounts")

    folder = response.json()[0]["folders"][0]
    assert (folder["sync_error"], folder["sync_failing_since"]) == (
        "<HttpError 404 File not found>",
        since.isoformat(),
    )


@pytest.mark.parametrize(
    "body, keeps_project",
    [
        ({"project_id": None}, False),
        ({"active": False}, True),
    ],
)
def test_update_folder_null_clears_project_and_omitted_keeps_it(
    client, db_session, user, body, keeps_project
):
    from memory.common.db.models.sources import GoogleAccount, GoogleFolder, Project

    project = Project(title="Drive project", state="open")
    account = GoogleAccount(user_id=user.id, name="Drive", email="drive@example.com")
    folder = GoogleFolder(account=account, folder_id="f", folder_name="F", project=project)
    db_session.add(folder)
    db_session.commit()

    response = client.patch(
        f"/google-drive/accounts/{account.id}/folders/{folder.id}", json=body
    )

    assert response.status_code == 200
    db_session.refresh(folder)
    assert (folder.project_id is not None) is keeps_project


def test_google_callback_escapes_error():
    from memory.api.google_drive import google_callback

    response = google_callback(request=Mock(), error="<script>x</script>")

    assert "<script>x" not in bytes(response.body).decode()
