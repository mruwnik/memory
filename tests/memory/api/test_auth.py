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
@patch("memory.api.auth.complete_oauth_flow", new_callable=AsyncMock)
@patch("memory.api.auth.make_session")
async def test_oauth_callback_discord_success(mock_make_session, mock_complete):
    mock_session = MagicMock()

    @contextmanager
    def session_cm():
        yield mock_session

    mock_make_session.return_value = session_cm()

    mcp_server = MagicMock()
    mock_session.query.return_value.filter.return_value.first.return_value = mcp_server

    mock_complete.return_value = (200, "Authorized")

    request = make_request("code=abc123&state=state456")
    response = await auth.oauth_callback_discord(request)

    assert response.status_code == 200
    body = response.body.decode()
    assert "Authorization Successful" in body
    assert "Authorized" in body
    mock_complete.assert_awaited_once_with(mcp_server, "abc123", "state456")
    mock_session.commit.assert_called_once()


@pytest.mark.asyncio
@patch("memory.api.auth.complete_oauth_flow", new_callable=AsyncMock)
@patch("memory.api.auth.make_session")
async def test_oauth_callback_discord_handles_failures(
    mock_make_session, mock_complete
):
    mock_session = MagicMock()

    @contextmanager
    def session_cm():
        yield mock_session

    mock_make_session.return_value = session_cm()

    mcp_server = MagicMock()
    mock_session.query.return_value.filter.return_value.first.return_value = mcp_server

    mock_complete.return_value = (500, "Failure")

    request = make_request("code=abc123&state=state456")
    response = await auth.oauth_callback_discord(request)

    assert response.status_code == 500
    body = response.body.decode()
    assert "Authorization Failed" in body
    assert "Failure" in body
    mock_complete.assert_awaited_once_with(mcp_server, "abc123", "state456")
    mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_oauth_callback_discord_validates_query_params():
    request = make_request("code=&state=")

    response = await auth.oauth_callback_discord(request)

    assert response.status_code == 400
    body = response.body.decode()
    assert "Missing authorization code" in body
