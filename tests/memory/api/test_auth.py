"""Tests for authentication helpers and OAuth callback."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from memory.api import auth
from memory.common import settings


def make_request(query: str) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/auth/callback/discord",
        "headers": [],
        "query_string": query.encode(),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


def test_get_bearer_token_parses_header():
    request = SimpleNamespace(headers={"Authorization": "Bearer token123"})

    assert auth.get_bearer_token(request) == "token123"


def test_get_bearer_token_handles_missing_header():
    request = SimpleNamespace(headers={})

    assert auth.get_bearer_token(request) is None


def test_get_token_prefers_header_over_cookie():
    request = SimpleNamespace(
        headers={"Authorization": "Bearer header-token"},
        cookies={"session": "cookie-token"},
    )

    assert auth.get_token(request) == "header-token"


def test_get_token_falls_back_to_cookie():
    request = SimpleNamespace(
        headers={},
        cookies={settings.SESSION_COOKIE_NAME: "cookie-token"},
    )

    assert auth.get_token(request) == "cookie-token"


@patch("memory.api.auth.get_user_session")
def test_logout_removes_session(mock_get_user_session):
    db = MagicMock()
    session = MagicMock()
    mock_get_user_session.return_value = session
    request = SimpleNamespace()

    result = auth.logout(request, db)

    assert result == {"message": "Logged out successfully"}
    db.delete.assert_called_once_with(session)
    db.commit.assert_called_once()


@patch("memory.api.auth.get_user_session", return_value=None)
def test_logout_handles_missing_session(mock_get_user_session):
    db = MagicMock()
    request = SimpleNamespace()

    result = auth.logout(request, db)

    assert result == {"message": "Logged out successfully"}
    db.delete.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
@patch("memory.api.auth.mcp_tools_list", new_callable=AsyncMock)
@patch("memory.api.auth.complete_oauth_flow", new_callable=AsyncMock)
@patch("memory.api.auth.make_session")
async def test_oauth_callback_discord_success(mock_make_session, mock_complete, mock_mcp_tools):
    mock_session = MagicMock()

    @contextmanager
    def session_cm():
        yield mock_session

    mock_make_session.return_value = session_cm()

    mcp_server = MagicMock()
    mcp_server.mcp_server_url = "https://example.com"
    mcp_server.access_token = "token123"
    mock_session.query.return_value.filter.return_value.first.return_value = mcp_server

    mock_complete.return_value = (200, "Authorized")
    mock_mcp_tools.return_value = [{"name": "test_tool"}]

    request = make_request("code=abc123&state=state456")
    response = await auth.oauth_callback_discord(request)

    assert response.status_code == 200
    body = response.body.decode()
    assert "Authorization Successful" in body
    assert "Authorized" in body
    mock_complete.assert_awaited_once_with(mcp_server, "abc123", "state456")
    assert mock_session.commit.call_count == 2  # Once after complete_oauth_flow, once after tools list


@pytest.mark.asyncio
@patch("memory.api.auth.mcp_tools_list", new_callable=AsyncMock)
@patch("memory.api.auth.complete_oauth_flow", new_callable=AsyncMock)
@patch("memory.api.auth.make_session")
async def test_oauth_callback_discord_handles_failures(
    mock_make_session, mock_complete, mock_mcp_tools
):
    mock_session = MagicMock()

    @contextmanager
    def session_cm():
        yield mock_session

    mock_make_session.return_value = session_cm()

    mcp_server = MagicMock()
    mcp_server.mcp_server_url = "https://example.com"
    mcp_server.access_token = "token123"
    mock_session.query.return_value.filter.return_value.first.return_value = mcp_server

    mock_complete.return_value = (500, "Failure")
    mock_mcp_tools.return_value = []

    request = make_request("code=abc123&state=state456")
    response = await auth.oauth_callback_discord(request)

    assert response.status_code == 500
    body = response.body.decode()
    assert "Authorization Failed" in body
    assert "Failure" in body
    mock_complete.assert_awaited_once_with(mcp_server, "abc123", "state456")
    assert mock_session.commit.call_count == 2  # Once after complete_oauth_flow, once after tools list


@pytest.mark.asyncio
async def test_oauth_callback_discord_validates_query_params():
    request = make_request("code=&state=")

    response = await auth.oauth_callback_discord(request)

    assert response.status_code == 400
    body = response.body.decode()
    assert "Missing authorization code" in body


@patch("memory.api.auth.authenticate_with_api_key")
def test_authenticate_bot_finds_matching_bot_via_new_system(mock_authenticate_new):
    """Test that authenticate_bot tries the new api_keys table first."""
    db = MagicMock()
    bot = MagicMock(spec=["api_key"])
    mock_authenticate_new.return_value = (bot, MagicMock())

    result = auth.authenticate_bot("bot_test123", db)

    assert result is bot
    mock_authenticate_new.assert_called_once_with(db, "bot_test123")


@patch("memory.api.auth.authenticate_with_api_key")
def test_authenticate_bot_falls_back_to_legacy(mock_authenticate_new):
    """Test that authenticate_bot falls back to legacy api_key column."""
    from memory.common.db.models import BotUser

    db = MagicMock()
    mock_authenticate_new.return_value = (None, None)

    # Set up legacy bot user
    bot = MagicMock(spec=BotUser)
    bot.api_key = "bot_test123"
    db.query.return_value.filter.return_value.all.return_value = [bot]

    result = auth.authenticate_bot("bot_test123", db)

    assert result is bot


@patch("memory.api.auth.authenticate_with_api_key")
def test_authenticate_bot_returns_none_for_invalid_key(mock_authenticate_new):
    db = MagicMock()
    mock_authenticate_new.return_value = (None, None)

    bot = MagicMock()
    bot.api_key = "bot_test123"
    db.query.return_value.filter.return_value.all.return_value = [bot]

    result = auth.authenticate_bot("bot_wrong", db)

    assert result is None


def test_get_session_user_uses_api_key_auth_for_bot_tokens():
    request = SimpleNamespace(
        headers={"Authorization": "Bearer bot_test123"},
        cookies={},
    )
    db = MagicMock()
    bot = MagicMock()
    bot.api_key = "bot_test123"
    db.query.return_value.filter.return_value.all.return_value = [bot]

    result = auth.get_session_user(request, db)

    assert result is bot


@patch("memory.api.auth.get_user_session")
def test_get_session_user_falls_back_to_session_for_non_bot_tokens(mock_get_user_session):
    request = SimpleNamespace(
        headers={"Authorization": "Bearer session-uuid"},
        cookies={},
    )
    db = MagicMock()
    session = MagicMock()
    session.user = MagicMock()
    mock_get_user_session.return_value = session

    result = auth.get_session_user(request, db)

    assert result is session.user
    mock_get_user_session.assert_called_once_with(request, db)


def test_get_user_account_returns_account_when_user_owns_it():
    db = MagicMock()
    user = MagicMock()
    user.id = 42
    account = MagicMock()
    account.user_id = 42
    db.get.return_value = account

    result = auth.get_user_account(db, MagicMock, 1, user)

    assert result is account
    db.get.assert_called_once()


def test_get_user_account_raises_404_when_account_not_found():
    from fastapi import HTTPException

    db = MagicMock()
    user = MagicMock()
    user.id = 42
    db.get.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        auth.get_user_account(db, MagicMock, 999, user)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Account not found"


def test_get_user_account_raises_404_when_user_does_not_own_account():
    from fastapi import HTTPException

    db = MagicMock()
    user = MagicMock()
    user.id = 42
    account = MagicMock()
    account.user_id = 99  # Different user
    db.get.return_value = account

    with pytest.raises(HTTPException) as exc_info:
        auth.get_user_account(db, MagicMock, 1, user)

    # Returns same error to avoid leaking info about account existence
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Account not found"


# --- Tests for new API key authentication ---


@patch("memory.api.auth.authenticate_with_api_key")
def test_authenticate_by_api_key_uses_new_system_first(mock_authenticate_new):
    """Test that authenticate_by_api_key tries the new api_keys table first."""
    db = MagicMock()
    mock_user = MagicMock()
    mock_key = MagicMock()
    mock_authenticate_new.return_value = (mock_user, mock_key)

    result = auth.authenticate_by_api_key("key_test123", db)

    assert result is mock_user
    mock_authenticate_new.assert_called_once_with(db, "key_test123")


@patch("memory.api.auth.authenticate_with_api_key")
def test_authenticate_by_api_key_falls_back_to_legacy(mock_authenticate_new):
    """Test that authenticate_by_api_key falls back to legacy api_key column."""
    db = MagicMock()
    mock_authenticate_new.return_value = (None, None)

    # Set up legacy bot user
    legacy_bot = MagicMock()
    legacy_bot.api_key = "bot_legacy123"
    db.query.return_value.filter.return_value.all.return_value = [legacy_bot]

    result = auth.authenticate_by_api_key("bot_legacy123", db)

    assert result is legacy_bot


@patch("memory.api.auth.authenticate_with_api_key")
def test_authenticate_by_api_key_respects_allowed_key_types(mock_authenticate_new):
    """Test that authenticate_by_api_key respects allowed_key_types filter."""
    from memory.common.db.models import ApiKeyType

    db = MagicMock()
    mock_user = MagicMock()
    mock_key = MagicMock()
    mock_key.key_type = ApiKeyType.DISCORD  # Key is Discord type

    mock_authenticate_new.return_value = (mock_user, mock_key)

    # Should reject because Discord is not in allowed types
    result = auth.authenticate_by_api_key(
        "key_test123", db, allowed_key_types=[ApiKeyType.INTERNAL, ApiKeyType.MCP]
    )

    assert result is None


@patch("memory.api.auth.authenticate_with_api_key")
def test_authenticate_by_api_key_accepts_allowed_key_type(mock_authenticate_new):
    """Test that authenticate_by_api_key accepts key when type is allowed."""
    from memory.common.db.models import ApiKeyType

    db = MagicMock()
    mock_user = MagicMock()
    mock_key = MagicMock()
    mock_key.key_type = ApiKeyType.MCP

    mock_authenticate_new.return_value = (mock_user, mock_key)

    result = auth.authenticate_by_api_key(
        "key_test123", db, allowed_key_types=[ApiKeyType.INTERNAL, ApiKeyType.MCP]
    )

    assert result is mock_user


def test_get_session_user_handles_new_key_prefix():
    """Test that get_session_user recognizes the new key_ prefix."""
    request = SimpleNamespace(
        headers={"Authorization": "Bearer key_test123"},
        cookies={},
    )
    db = MagicMock()

    with patch("memory.api.auth.authenticate_by_api_key") as mock_auth:
        mock_auth.return_value = MagicMock()
        auth.get_session_user(request, db)

        mock_auth.assert_called_once_with("key_test123", db, None)


def test_get_user_from_token_handles_new_key_prefix():
    """Test that get_user_from_token recognizes the new key_ prefix."""
    db = MagicMock()

    with patch("memory.api.auth.authenticate_by_api_key") as mock_auth:
        mock_auth.return_value = MagicMock()
        auth.get_user_from_token("key_test123", db)

        mock_auth.assert_called_once_with("key_test123", db, None)


@patch("memory.api.auth.hash_api_key")
@patch("memory.api.auth.ApiKey")
def test_get_api_key_scopes_returns_key_scopes(mock_api_key_class, mock_hash):
    """Test that get_api_key_scopes returns key-specific scopes."""
    db = MagicMock()
    mock_hash.return_value = "hashed_key"

    mock_key = MagicMock()
    mock_key.is_valid.return_value = True
    mock_key.get_effective_scopes.return_value = ["read", "observe"]

    db.query.return_value.filter.return_value.first.return_value = mock_key

    result = auth.get_api_key_scopes("key_test123", db)

    assert result == ["read", "observe"]


@patch("memory.api.auth.hash_api_key")
@patch("memory.api.auth.ApiKey")
def test_get_api_key_scopes_returns_none_for_invalid_key(mock_api_key_class, mock_hash):
    """Test that get_api_key_scopes returns None for invalid keys."""
    db = MagicMock()
    mock_hash.return_value = "hashed_key"

    mock_key = MagicMock()
    mock_key.is_valid.return_value = False

    db.query.return_value.filter.return_value.first.return_value = mock_key
    # No legacy users
    db.query.return_value.filter.return_value.all.return_value = []

    result = auth.get_api_key_scopes("key_invalid", db)

    assert result is None
