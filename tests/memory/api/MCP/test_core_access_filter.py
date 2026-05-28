# pyright: reportArgumentType=false
"""Tests for the data-layer scope source in get_current_user_access_filter.

The audit finding (d4c8278e) was: APIKey.scopes is honored by the
visibility middleware but ignored by ``get_current_user_access_filter``,
which read ``user.scopes`` directly from the DB and so let an admin's
``[\"read\"]``-scoped API key inherit full superadmin data access.
The fix routes the function through ``access_token.scopes`` instead,
which already carries the resolved override (api_key.scopes if set,
else user.scopes) from SimpleOAuthProvider.verify_token.

These tests mock the token and DB lookups to keep the assertions
focused on the scope source — the broader access_control machinery is
covered elsewhere.
"""

from unittest.mock import MagicMock, patch

import pytest

from memory.api.MCP.servers.core import get_current_user_access_filter
from memory.common.access_control import AccessFilter


def _fake_access_token(token: str, scopes: list[str]):
    tok = MagicMock()
    tok.token = token
    tok.scopes = scopes
    return tok


def _fake_user(user_id: int = 1, user_scopes: list[str] | None = None):
    user = MagicMock()
    user.id = user_id
    user.scopes = user_scopes or []
    return user


def _fake_user_session(user):
    sess = MagicMock()
    sess.user = user
    return sess


def _fake_api_key(user):
    key = MagicMock()
    key.user = user
    return key


@pytest.fixture
def patched_session():
    """Mock the make_session context manager and yield the inner session mock."""
    with patch(
        "memory.api.MCP.servers.core.make_session"
    ) as make_session_mock:
        session = MagicMock()
        make_session_mock.return_value.__enter__.return_value = session
        make_session_mock.return_value.__exit__.return_value = False
        yield session


def test_no_access_token_returns_empty_filter():
    with patch(
        "memory.api.MCP.servers.core.get_access_token", return_value=None
    ):
        result = get_current_user_access_filter()
    assert isinstance(result, AccessFilter)
    assert result.conditions == []


def test_session_token_admin_returns_none(patched_session):
    """Admin user (\"*\" in token scopes) bypasses filtering."""
    user = _fake_user(user_id=42, user_scopes=["*"])
    patched_session.get.return_value = _fake_user_session(user)

    with patch(
        "memory.api.MCP.servers.core.get_access_token",
        return_value=_fake_access_token("sess-tok", ["*", "read", "write"]),
    ):
        result = get_current_user_access_filter()

    # Superadmin → None (no filter applied)
    assert result is None


def test_session_token_non_admin_returns_filter(patched_session):
    """Non-admin gets a real AccessFilter (project-scoped)."""
    user = _fake_user(user_id=43, user_scopes=["read"])
    patched_session.get.return_value = _fake_user_session(user)

    with patch(
        "memory.api.MCP.servers.core.get_access_token",
        return_value=_fake_access_token("sess-tok-2", ["read", "write"]),
    ), patch(
        "memory.api.MCP.servers.core.build_user_access_filter_from_dict",
        return_value=AccessFilter(conditions=[]),
    ) as builder:
        get_current_user_access_filter()

    builder.assert_called_once()
    user_dict = builder.call_args.args[0]
    assert user_dict["id"] == 43
    # Token scopes (not user.scopes) should be the source of truth.
    assert user_dict["scopes"] == ["read", "write"]


def test_api_key_with_restricted_scopes_does_not_inherit_admin(patched_session):
    """The HIGH bug: admin user mints a [\"read\"]-scoped API key.

    Pre-fix, get_current_user_access_filter saw user.scopes=[\"*\"] and
    returned None (full admin) for that key. After the fix, the
    function consults the access token's scopes — which oauth_provider
    already resolved to api_key.scopes=[\"read\"] — and the user does
    NOT get admin treatment. ``build_user_access_filter_from_dict`` is
    therefore called with non-admin scopes; the resulting filter is
    NOT None.
    """
    admin_user = _fake_user(user_id=99, user_scopes=["*"])
    # Session-token lookup misses; API-key lookup hits.
    patched_session.get.return_value = None

    with patch(
        "memory.api.MCP.servers.core.get_access_token",
        return_value=_fake_access_token("api-key-tok", ["read"]),
    ), patch(
        "memory.api.MCP.servers.core.lookup_api_key",
        return_value=_fake_api_key(admin_user),
    ), patch(
        "memory.api.MCP.servers.core.build_user_access_filter_from_dict",
        return_value=AccessFilter(conditions=[{"project_id": 1}]),
    ) as builder:
        result = get_current_user_access_filter()

    builder.assert_called_once()
    user_dict = builder.call_args.args[0]
    assert user_dict["id"] == 99
    # Critical: the dict carries ["read"], NOT ["*"]
    assert user_dict["scopes"] == ["read"]
    # And the filter is the real (project-scoped) one, not None.
    assert result is not None
    assert result.conditions == [{"project_id": 1}]


def test_api_key_with_admin_override_does_grant_admin(patched_session):
    """An API key whose scopes still include \"*\" still grants admin."""
    user = _fake_user(user_id=99, user_scopes=["*"])
    patched_session.get.return_value = None

    with patch(
        "memory.api.MCP.servers.core.get_access_token",
        return_value=_fake_access_token("api-key-tok", ["*"]),
    ), patch(
        "memory.api.MCP.servers.core.lookup_api_key",
        return_value=_fake_api_key(user),
    ):
        result = get_current_user_access_filter()

    # Admin scope on the key → None (no filter)
    assert result is None


def test_unknown_token_returns_empty_filter(patched_session):
    """If neither session nor api-key lookups find anything, return no-access."""
    patched_session.get.return_value = None

    with patch(
        "memory.api.MCP.servers.core.get_access_token",
        return_value=_fake_access_token("ghost-tok", ["*"]),
    ), patch(
        "memory.api.MCP.servers.core.lookup_api_key", return_value=None
    ):
        result = get_current_user_access_filter()

    assert isinstance(result, AccessFilter)
    assert result.conditions == []
