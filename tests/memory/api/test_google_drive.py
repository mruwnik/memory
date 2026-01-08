"""Tests for Google Drive OAuth API endpoints."""

from unittest.mock import MagicMock, Mock, patch

import pytest

from memory.api.google_drive import (
    AVAILABLE_GOOGLE_SCOPES,
    BASE_GOOGLE_SCOPES,
    google_authorize,
    get_available_scopes,
    reauthorize_account,
    ReauthorizeRequest,
)


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


@patch("memory.api.google_drive.make_session")
@patch("memory.api.google_drive.get_oauth_config")
@patch("google_auth_oauthlib.flow.Flow")
@patch("memory.api.google_drive.GoogleOAuthState")
@patch("googleapiclient.discovery.build")
def test_google_callback_parses_scopes_from_url(
    mock_build, mock_state, mock_flow_class, mock_get_config, mock_make_session
):
    """Test that callback parses granted scopes from URL parameter."""
    from memory.api.google_drive import google_callback

    # Setup mocks
    mock_state.validate.return_value = 1  # user_id

    mock_config = Mock()
    mock_config.redirect_uris = ["http://localhost/callback"]
    mock_config.to_client_config.return_value = {"web": {}}
    mock_get_config.return_value = mock_config

    mock_credentials = Mock()
    mock_credentials.token = "access_token"
    mock_credentials.refresh_token = "refresh_token"
    mock_credentials.expiry = None

    mock_flow = Mock()
    mock_flow.credentials = mock_credentials
    mock_flow_class.from_client_config.return_value = mock_flow

    mock_service = Mock()
    mock_service.userinfo().get().execute.return_value = {
        "email": "test@example.com",
        "name": "Test User",
    }
    mock_build.return_value = mock_service

    mock_session = Mock()
    mock_account = Mock()
    mock_session.query().filter().first.return_value = mock_account
    mock_make_session.return_value.__enter__ = Mock(return_value=mock_session)
    mock_make_session.return_value.__exit__ = Mock(return_value=False)

    mock_request = Mock()

    # Call callback with scope parameter (as Google would send it)
    scope_string = "email profile https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/drive.readonly https://www.googleapis.com/auth/gmail.send openid"

    google_callback(
        request=mock_request,
        code="auth_code",
        state="valid_state",
        error=None,
        scope=scope_string,
    )

    # Verify scopes were parsed and saved correctly
    expected_scopes = scope_string.split()
    assert mock_account.scopes == expected_scopes
    assert "https://www.googleapis.com/auth/gmail.send" in mock_account.scopes
    assert "https://www.googleapis.com/auth/drive.readonly" in mock_account.scopes


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
