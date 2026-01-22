"""Tests for authentication helpers and OAuth callback."""

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, cast
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

    assert auth.get_bearer_token(cast(Any, request)) == "token123"


def test_get_bearer_token_handles_missing_header():
    request = SimpleNamespace(headers={})

    assert auth.get_bearer_token(cast(Any, request)) is None


def test_get_token_prefers_header_over_cookie():
    request = SimpleNamespace(
        headers={"Authorization": "Bearer header-token"},
        cookies={"session": "cookie-token"},
    )

    assert auth.get_token(cast(Any, request)) == "header-token"


def test_get_token_falls_back_to_cookie():
    request = SimpleNamespace(
        headers={},
        cookies={settings.SESSION_COOKIE_NAME: "cookie-token"},
    )

    assert auth.get_token(cast(Any, request)) == "cookie-token"


@patch("memory.api.auth.get_user_session")
def test_logout_removes_session(mock_get_user_session):
    db = MagicMock()
    session = MagicMock()
    mock_get_user_session.return_value = session
    request = SimpleNamespace()

    result = auth.logout(cast(Any, request), db)

    assert result == {"message": "Logged out successfully"}
    db.delete.assert_called_once_with(session)
    db.commit.assert_called_once()


@patch("memory.api.auth.get_user_session", return_value=None)
def test_logout_handles_missing_session(mock_get_user_session):
    db = MagicMock()
    request = SimpleNamespace()

    result = auth.logout(cast(Any, request), db)

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
    body = cast(bytes, response.body).decode()
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
    body = cast(bytes, response.body).decode()
    assert "Authorization Failed" in body
    assert "Failure" in body
    mock_complete.assert_awaited_once_with(mcp_server, "abc123", "state456")
    assert mock_session.commit.call_count == 2  # Once after complete_oauth_flow, once after tools list


@pytest.mark.asyncio
async def test_oauth_callback_discord_validates_query_params():
    request = make_request("code=&state=")

    response = await auth.oauth_callback_discord(request)

    assert response.status_code == 400
    body = cast(bytes, response.body).decode()
    assert "Missing authorization code" in body


def test_authenticate_bot_finds_matching_bot_via_api_key_table():
    """Test authenticate_bot finds bot via new api_keys table."""
    from memory.common.db.models import APIKey, BotUser

    db = MagicMock()

    # Mock API key record
    api_key_record = MagicMock()
    api_key_record.key = "bot_test123"
    api_key_record.is_valid.return_value = True
    api_key_record.is_one_time = False

    # Mock user
    bot = MagicMock()
    bot.user_type = "bot"
    api_key_record.user = bot

    def mock_query(model):
        if model == APIKey:
            query_mock = MagicMock()
            # Now queries all keys (not filtering by revoked)
            query_mock.all.return_value = [api_key_record]
            return query_mock
        return MagicMock()

    db.query.side_effect = mock_query

    result = auth.authenticate_bot("bot_test123", db)

    assert result is bot


def test_authenticate_bot_finds_matching_bot_via_legacy_field():
    """Test authenticate_bot falls back to legacy User.api_key field."""
    from memory.common.db.models import APIKey, BotUser

    db = MagicMock()
    bot = MagicMock()
    bot.api_key = "bot_test123"

    def mock_query(model):
        if model == APIKey:
            query_mock = MagicMock()
            query_mock.filter.return_value.all.return_value = []
            return query_mock
        elif model == BotUser:
            query_mock = MagicMock()
            query_mock.all.return_value = [bot]
            return query_mock
        return MagicMock()

    db.query.side_effect = mock_query

    result = auth.authenticate_bot("bot_test123", db)

    assert result is bot


def test_authenticate_bot_returns_none_for_invalid_key():
    """Test authenticate_bot returns None for invalid API key."""
    from memory.common.db.models import APIKey, BotUser

    db = MagicMock()
    bot = MagicMock()
    bot.api_key = "bot_test123"

    def mock_query(model):
        if model == APIKey:
            query_mock = MagicMock()
            query_mock.filter.return_value.all.return_value = []
            return query_mock
        elif model == BotUser:
            query_mock = MagicMock()
            query_mock.all.return_value = [bot]
            return query_mock
        return MagicMock()

    db.query.side_effect = mock_query

    result = auth.authenticate_bot("bot_wrong", db)

    assert result is None


def test_authenticate_bot_rejects_non_bot_user_from_api_key_table():
    """Test authenticate_bot rejects keys belonging to non-bot users."""
    from memory.common.db.models import APIKey

    db = MagicMock()

    # Mock API key belonging to a human user
    api_key_record = MagicMock()
    api_key_record.key = "human_key123"
    api_key_record.is_valid.return_value = True
    api_key_record.is_one_time = False

    human_user = MagicMock()
    human_user.user_type = "human"
    api_key_record.user = human_user

    def mock_query(model):
        if model == APIKey:
            query_mock = MagicMock()
            # Now queries all keys (not filtering by revoked)
            query_mock.all.return_value = [api_key_record]
            return query_mock
        return MagicMock()

    db.query.side_effect = mock_query

    result = auth.authenticate_bot("human_key123", db)

    assert result is None


def test_authenticate_by_api_key_returns_user_and_key_record():
    """Test authenticate_by_api_key returns both user and key record."""
    from memory.common.db.models import APIKey

    db = MagicMock()

    api_key_record = MagicMock()
    api_key_record.key = "key_test123"
    api_key_record.key_type = "internal"
    api_key_record.is_valid.return_value = True
    api_key_record.is_one_time = False

    user = MagicMock()
    api_key_record.user = user

    def mock_query(model):
        if model == APIKey:
            query_mock = MagicMock()
            # Now queries all keys (not filtering by revoked)
            query_mock.all.return_value = [api_key_record]
            return query_mock
        return MagicMock()

    db.query.side_effect = mock_query

    result_user, result_key = auth.authenticate_by_api_key("key_test123", db)

    assert result_user is user
    assert result_key is api_key_record


def test_authenticate_by_api_key_respects_allowed_key_types():
    """Test authenticate_by_api_key rejects keys of wrong type."""
    from memory.common.db.models import APIKey

    db = MagicMock()

    api_key_record = MagicMock()
    api_key_record.key = "discord_key123"
    api_key_record.key_type = "discord"
    api_key_record.is_valid.return_value = True

    user = MagicMock()
    api_key_record.user = user

    def mock_query(model):
        if model == APIKey:
            query_mock = MagicMock()
            # Now queries all keys (not filtering by revoked)
            query_mock.all.return_value = [api_key_record]
            return query_mock
        return MagicMock()

    db.query.side_effect = mock_query

    # Should reject when only "internal" is allowed
    result_user, result_key = auth.authenticate_by_api_key(
        "discord_key123", db, allowed_key_types=["internal"]
    )

    assert result_user is None
    assert result_key is None


def test_authenticate_by_api_key_accepts_matching_key_type():
    """Test authenticate_by_api_key accepts keys of correct type."""
    from memory.common.db.models import APIKey

    db = MagicMock()

    api_key_record = MagicMock()
    api_key_record.key = "discord_key123"
    api_key_record.key_type = "discord"
    api_key_record.is_valid.return_value = True
    api_key_record.is_one_time = False

    user = MagicMock()
    api_key_record.user = user

    def mock_query(model):
        if model == APIKey:
            query_mock = MagicMock()
            # Now queries all keys (not filtering by revoked)
            query_mock.all.return_value = [api_key_record]
            return query_mock
        return MagicMock()

    db.query.side_effect = mock_query

    # Should accept when "discord" is in allowed types
    result_user, result_key = auth.authenticate_by_api_key(
        "discord_key123", db, allowed_key_types=["internal", "discord"]
    )

    assert result_user is user
    assert result_key is api_key_record


def test_handle_api_key_use_updates_last_used():
    """Test handle_api_key_use updates last_used_at timestamp."""
    from datetime import datetime, timezone

    db = MagicMock()
    key_record = MagicMock()
    key_record.is_one_time = False
    key_record.last_used_at = None

    auth.handle_api_key_use(key_record, db)

    assert key_record.last_used_at is not None
    db.commit.assert_called_once()
    db.delete.assert_not_called()


def test_handle_api_key_use_deletes_one_time_key():
    """Test handle_api_key_use deletes one-time keys after use."""
    db = MagicMock()
    key_record = MagicMock()
    key_record.is_one_time = True

    auth.handle_api_key_use(key_record, db)

    db.delete.assert_called_once_with(key_record)
    db.commit.assert_called_once()


def test_get_session_user_uses_api_key_auth_for_bot_tokens():
    """Test that get_session_user authenticates API keys via both new table and legacy fallback."""
    from memory.common.db.models import APIKey, User

    request = SimpleNamespace(
        headers={"Authorization": "Bearer bot_test123"},
        cookies={},
    )
    db = MagicMock()
    bot = MagicMock()
    bot.api_key = "bot_test123"

    # The new authenticate_by_api_key queries APIKey table first, then falls back to User table.
    # Set up mock to return empty list for APIKey query and the bot for User query.
    def mock_query(model):
        if model == APIKey:
            query_mock = MagicMock()
            query_mock.filter.return_value.all.return_value = []
            return query_mock
        elif model == User:
            query_mock = MagicMock()
            query_mock.filter.return_value.all.return_value = [bot]
            return query_mock
        return MagicMock()

    db.query.side_effect = mock_query

    result = auth.get_session_user(cast(Any, request), db)

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

    result = auth.get_session_user(cast(Any, request), db)

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
