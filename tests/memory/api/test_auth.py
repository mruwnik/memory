"""Tests for authentication helpers and OAuth callback."""

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from memory.api import auth
from memory.api.auth import is_whitelisted_path
from memory.common import settings


# --- AuthenticationMiddleware whitelist matching ----------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/health",
        "/health/metrics",
        "/authorize",
        "/authorize/code",
        "/token",
        "/token/refresh",
        # OAuth Dynamic Client Registration (RFC 7591) and Token Revocation
        # (RFC 7009) endpoints exposed by the MCP SDK. Clients haven't been
        # issued credentials at /register time, so they CANNOT authenticate.
        "/register",
        "/revoke",
        "/mcp",
        "/mcp/tools",
        "/ui",
        "/ui/index.html",
        "/oauth/login",
        "/oauth/callback/google",
        "/.well-known/openid-configuration",
        "/google-drive/callback",
        "/polls/respond",
        "/polls/respond/abc-123",
        "/claude/transfer/pull",
        "/claude/transfer/push",
        # Generic content upload: authorized by the signed ingest token in
        # the query string, not a session cookie. Must bypass the middleware
        # or the minted upload_url is unusable from a non-browser client.
        "/ingest/upload",
        # Slack push-events webhook authenticates via x-slack-signature
        # (HMAC over body), not session cookies. Without this carve-out
        # the AuthenticationMiddleware 401s every Slack POST before the
        # in-endpoint HMAC check fires.
        "/slack/events/5",
        "/slack/events/123?wizard_nonce=abc",
        # /register and /revoke are RFC 7591 / RFC 7009 endpoints exposed
        # at root by the MCP SDK. DCR clients have no credentials at
        # registration time by definition, so the middleware bypass is
        # mandatory. Re-added by master commit bd7d388 ("make sure mcp
        # can register").
        "/register",
        "/register/finish",
        "/revoke",
    ],
)
def test_is_whitelisted_path_lets_real_routes_through(path):
    assert is_whitelisted_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        # Latent prefix-overrun footguns: any one of these added as a real
        # route would bypass auth in the old `startswith` impl.
        "/healthcheck",
        "/health-secret",
        "/healthxxx",
        # /register and /revoke ARE whitelisted (covered above) — but
        # prefix-overrun siblings must still be blocked.
        "/registerme",
        "/registers",
        "/revoked",
        "/revokeall",
        "/authorize-anything",
        "/tokens",
        "/tokenrevoke",
        "/mcphidden",
        "/mcp-admin",
        "/uiAttacker",
        "/uiconfig",
        # Real-but-not-whitelisted endpoints
        "/users",
        "/secrets",
        "/teams/1",
        "/calendar-accounts",
        "/google-drive/config",  # admin-gated; must NOT be whitelisted
        "/polls",  # the public list endpoint is /polls/respond, /polls itself is auth'd
        "/claude/u1-x-deadbeef/logs",  # gated by route-level user check
        # Regression: ``/admin/statics/*`` was previously whitelisted for a
        # SQLAdmin app that never shipped, pre-granting unauthenticated
        # access to anything mounted under that prefix. Pin that nothing
        # under /admin/* is whitelisted by default.
        "/admin/statics/css/main.css",
        "/admin/statics/js/app.js",
        "/admin",
        "/admin/users",
        # Defense pin against the natural-but-wrong operator workaround
        # for the events-webhook 401 (broaden ``/slack/events/`` to
        # ``/slack/`` and accidentally expose every authenticated Slack
        # admin route). These sibling routes MUST stay auth'd.
        "/slack/workspaces",
        "/slack/workspaces/abc",
        "/slack/apps",
        "/slack/apps/123",
        "/slack/channels/456",
        "/slack/eventslog",  # prefix-overrun against /slack/events
        # Prefix-overrun guards against the /ingest/upload carve-out: only
        # the exact upload path is token-authed; siblings stay session-gated.
        "/ingest",
        "/ingest/uploads",  # overrun against /ingest/upload
        "/ingest/upload-config",
        "/ingestxxx",
        "",
    ],
)
def test_is_whitelisted_path_blocks_prefix_overrun(path):
    assert is_whitelisted_path(path) is False


# --- _CLAUDE_SESSION_PATTERN: middleware auth-bypass regex ------------------
#
# This regex is consulted in ``AuthenticationMiddleware.dispatch`` alongside
# ``is_whitelisted_path``. A path that matches it bypasses session/Bearer
# authentication so that WebSocket routes mounted under ``/claude/{session_id}``
# can authenticate via ``?token=`` query param (HTTP headers don't survive
# the WS upgrade for browser clients). Any future route that lands under
# this prefix and forgets ``Depends(get_current_user)`` would inherit
# unauthenticated access — so the regex MUST anchor on a path-segment
# boundary and reject:
#
#   1. paths whose adjacent characters extend past the session-id (no
#      ``/claude/u1-e2-abcdef012345foo`` smuggling)
#   2. session-ids with too-short hex suffixes (``token_hex(6)`` is 12
#      chars; matching ``[a-fA-F0-9]+`` would let a single hex char pass)
#   3. invalid source segments (only ``e\d+``, ``s\d+``, or ``x``)


# The hex suffix is now ``secrets.token_hex(16)`` = 32 hex chars (128 bits).
_HEX32 = "abcdef0123456789abcdef0123456789"  # exact length 32, hex-only
_HEX32_UPPER = _HEX32.upper()


@pytest.mark.parametrize(
    "path",
    [
        # Bare session-id with no subpath
        f"/claude/u1-e2-{_HEX32}",
        f"/claude/u1-s2-{_HEX32}",
        f"/claude/u1-x-{_HEX32}",
        # Multi-digit user_id and source numbers
        f"/claude/u12345-e98765-{_HEX32}",
        f"/claude/u12345-s98765-{_HEX32}",
        # Subpath under a valid session id (websocket endpoints)
        f"/claude/u1-e2-{_HEX32}/ws",
        f"/claude/u1-x-{_HEX32}/files/foo.txt",
        f"/claude/u123-s456-{_HEX32}/diff/preview",
        # Uppercase hex (regex is IGNORECASE)
        f"/claude/u1-e2-{_HEX32_UPPER}",
        # Longer hex tail (token rotations may extend; floor is the
        # generator length, ceiling is unbounded)
        f"/claude/u1-e2-{_HEX32}{_HEX32}",
    ],
)
def test_claude_session_pattern_matches_valid_paths(path):
    assert auth._CLAUDE_SESSION_PATTERN.match(path) is not None, path


@pytest.mark.parametrize(
    "path",
    [
        # ── Adjacent-character smuggling. Bypass would let an attacker
        # land on a future route mounted at e.g. ``/claude/u1-e2-XXXXfoo``
        # without auth.
        f"/claude/u1-e2-{_HEX32}foo",
        f"/claude/u1-e2-{_HEX32}-shell",
        f"/claude/u1-e2-{_HEX32}admin",
        f"/claude/u1-e2-{_HEX32}.json",
        # ── Hex floor enforcement. Short prefixes shrink the search
        # space dramatically; the regex must reject anything below the
        # actual ``token_hex(16)`` generator length (32).
        "/claude/u1-e2-a",
        "/claude/u1-e2-abc",
        "/claude/u1-e2-abcdef",                            # 6 hex < 32
        "/claude/u1-e2-abcdef0123456789",                  # 16 hex < 32
        "/claude/u1-e2-abcdef0123456789abcdef0123456",    # 31 hex < 32 (one shy)
        # ── Invalid source segments. The auth.py regex used to accept
        # any ``[a-z]\d*`` source; tighten to match cloud_claude's
        # canonical ``(e\d+|s\d+|x)`` source format only.
        f"/claude/u1-q5-{_HEX32}",   # 'q' is not e/s/x
        f"/claude/u1-foo-{_HEX32}",
        f"/claude/u1-ee-{_HEX32}",   # source must be single letter
        # ── Wrong path prefix. Adjacent characters before /claude/
        # must not match either.
        f"/claude-debug/u1-e2-{_HEX32}",
        f"/claudex/u1-e2-{_HEX32}",
        # ── Empty / malformed
        "",
        "/",
        "/claude/",
        "/claude/u1",
        "/claude/u1-e2-",  # no hex tail
        # ── Non-claude paths
        f"/users/u1-e2-{_HEX32}",
        f"/api/claude/u1-e2-{_HEX32}",
    ],
)
def test_claude_session_pattern_rejects_invalid_paths(path):
    assert auth._CLAUDE_SESSION_PATTERN.match(path) is None, path


def test_claude_session_pattern_hex_floor_matches_token_hex_16():
    """REGRESSION GUARD: the hex-length floor in the regex must match
    the actual session-id generator's hex length (``secrets.token_hex(16)``
    yields 32 hex chars = 128 bits). If the generator changes
    (e.g. ``token_hex(8)``), this regex's ``{32,}`` floor must change
    in lockstep with ``cloud_claude._SESSION_ID_RE`` — both regexes
    encode the same invariant.
    """
    import secrets

    # Verify the generator parameter still produces exactly 32 hex chars,
    # so the floor in our regex remains tight.
    assert len(secrets.token_hex(16)) == 32

    # And the floor passes when the generator's exact output is used.
    real_session_path = f"/claude/u1-e2-{secrets.token_hex(16)}"
    assert auth._CLAUDE_SESSION_PATTERN.match(real_session_path) is not None


# --- AuthenticationMiddleware end-to-end ------------------------------------
#
# is_whitelisted_path() is just a helper. The bug we're guarding against is
# the middleware's dispatch() returning 401 before the handler runs, so we
# drive the real app + middleware here and assert the request reaches a
# downstream handler instead of being short-circuited.


@pytest.mark.parametrize(
    "method,path",
    [
        # OAuth Dynamic Client Registration (RFC 7591). Brand-new MCP
        # clients have no credentials yet — middleware MUST let this
        # through or enrollment is impossible.
        ("POST", "/register"),
        # OAuth Token Revocation (RFC 7009). Revoking a stolen token
        # by definition can't require that token to authenticate.
        ("POST", "/revoke"),
        # Discovery + flow endpoints, also pre-auth by spec.
        ("GET", "/.well-known/oauth-authorization-server"),
        ("GET", "/.well-known/oauth-protected-resource"),
        ("GET", "/authorize"),
        ("POST", "/token"),
        # The ingest upload endpoint authenticates via its signed ?token=,
        # not a session — the middleware must pass both the documented PUT
        # and the also-accepted POST through to the in-endpoint token check.
        # A bad token reaches the handler and gets a 403 (not a 401 from the
        # middleware); the point of this case is purely "not 401".
        ("PUT", "/ingest/upload?token=v1.bad.sig"),
        ("POST", "/ingest/upload?token=v1.bad.sig"),
    ],
)
def test_oauth_public_endpoints_bypass_auth_middleware(
    client, monkeypatch, method, path
):
    """The middleware must not return 401 for unauthenticated OAuth-public paths.

    We don't care what the OAuth handler returns (400/422 for missing params is
    fine) — only that the middleware passed the request along. A 401 here
    means the middleware blocked it before the handler ran.
    """
    monkeypatch.setattr(settings, "DISABLE_AUTH", False)
    response = client.request(method, path)
    assert response.status_code != 401, (
        f"{method} {path} returned 401 from AuthenticationMiddleware; "
        f"OAuth-public endpoints must be in WHITELIST. Body: {response.text!r}"
    )


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
def test_logout_handles_missing_session(_mock_get_user_session):
    db = MagicMock()
    request = SimpleNamespace()

    result = auth.logout(cast(Any, request), db)

    assert result == {"message": "Logged out successfully"}
    db.delete.assert_not_called()
    db.commit.assert_not_called()
    # No session → no refresh-token revocation either.
    db.execute.assert_not_called()


@patch("memory.api.auth.get_user_session")
def test_logout_revokes_paired_refresh_tokens(mock_get_user_session):
    """Regression for audit-1775774f: deleting the UserSession alone is
    insufficient — any OAuthRefreshToken paired with it via
    ``access_token_session_id`` survives and can mint fresh access tokens
    after "logout". The fix issues a bulk UPDATE that flips every active
    paired refresh token to revoked=True in the same transaction.
    """
    from sqlalchemy import update as sa_update

    from memory.common.db.models import OAuthRefreshToken

    db = MagicMock()
    session = MagicMock()
    session.id = "session-uuid-123"
    mock_get_user_session.return_value = session
    request = SimpleNamespace()

    auth.logout(cast(Any, request), db)

    # Exactly one bulk UPDATE issued before the session delete.
    assert db.execute.call_count == 1
    # The statement must be an UPDATE on OAuthRefreshToken.
    stmt = db.execute.call_args[0][0]
    assert isinstance(stmt, type(sa_update(OAuthRefreshToken).values(revoked=True)))
    # The session delete + commit still happen.
    db.delete.assert_called_once_with(session)
    db.commit.assert_called_once()


def _mock_oauth_make_session():
    """Build a make_session double that yields a fresh mock session per call.

    The OAuth callback now uses three short DB scopes (locate-and-detach,
    persist-tokens, persist-tools) rather than one big session held across
    the awaits. The factory tracks every session it creates so the test
    can assert per-session commit/expunge behaviour.
    """
    sessions: list[MagicMock] = []

    def _make_session_factory(*_a, **_k):
        sess = MagicMock()
        sessions.append(sess)

        @contextmanager
        def _ctx():
            yield sess

        return _ctx()

    return _make_session_factory, sessions


@pytest.mark.asyncio
@patch("memory.api.auth.mcp_tools_list", new_callable=AsyncMock)
@patch("memory.api.auth.complete_oauth_flow", new_callable=AsyncMock)
@patch("memory.api.auth.make_session")
async def test_oauth_callback_discord_success(mock_make_session, mock_complete, mock_mcp_tools):
    factory, sessions = _mock_oauth_make_session()
    mock_make_session.side_effect = factory

    mcp_server = MagicMock()
    mcp_server.id = 7
    mcp_server.mcp_server_url = "https://example.com"
    mcp_server.client_id = "cid"
    mcp_server.code_verifier = "verifier"
    mcp_server.access_token = "token123"

    mock_complete.return_value = (200, "Authorized")
    mock_mcp_tools.return_value = [{"name": "test_tool"}]

    request = make_request("code=abc123&state=state456")

    # First session: SELECT by state returns the row.
    # Subsequent sessions: get() returns the same row.
    # We don't know how many sessions will be created up front (depends on
    # success/failure), so wire each as it's created.
    def wired_factory(*a, **k):
        cm = factory(*a, **k)
        sess = sessions[-1]
        sess.query.return_value.filter.return_value.first.return_value = mcp_server
        sess.get.return_value = mcp_server
        return cm

    mock_make_session.side_effect = wired_factory

    response = await auth.oauth_callback_discord(request)

    assert response.status_code == 200
    body = cast(bytes, response.body).decode()
    assert "Authorization Successful" in body
    assert "Authorized" in body
    mock_complete.assert_awaited_once_with(mcp_server, "abc123", "state456")
    # Three sessions: locate-and-detach, persist-tokens, persist-tools.
    assert len(sessions) == 3
    # The first session expunges, then closes — no commit there.
    sessions[0].commit.assert_not_called()
    sessions[0].expunge.assert_called_once_with(mcp_server)
    # Subsequent sessions commit once each.
    sessions[1].commit.assert_called_once()
    sessions[2].commit.assert_called_once()


@pytest.mark.asyncio
@patch("memory.api.auth.mcp_tools_list", new_callable=AsyncMock)
@patch("memory.api.auth.complete_oauth_flow", new_callable=AsyncMock)
@patch("memory.api.auth.make_session")
async def test_oauth_callback_discord_handles_failures(
    mock_make_session, mock_complete, mock_mcp_tools
):
    factory, sessions = _mock_oauth_make_session()

    mcp_server = MagicMock()
    mcp_server.id = 7
    mcp_server.mcp_server_url = "https://example.com"
    mcp_server.client_id = "cid"
    mcp_server.code_verifier = "verifier"
    mcp_server.access_token = "token123"

    def wired_factory(*a, **k):
        cm = factory(*a, **k)
        sess = sessions[-1]
        sess.query.return_value.filter.return_value.first.return_value = mcp_server
        sess.get.return_value = mcp_server
        return cm

    mock_make_session.side_effect = wired_factory

    mock_complete.return_value = (500, "Failure")
    mock_mcp_tools.return_value = []

    request = make_request("code=abc123&state=state456")
    response = await auth.oauth_callback_discord(request)

    assert response.status_code == 500
    body = cast(bytes, response.body).decode()
    assert "Authorization Failed" in body
    assert "Failure" in body
    mock_complete.assert_awaited_once_with(mcp_server, "abc123", "state456")
    # On failure: only two sessions (locate-and-detach + persist-tokens).
    # The tools-list phase is skipped because we never reach the success
    # branch, so the third session is never opened.
    assert len(sessions) == 2
    sessions[0].expunge.assert_called_once_with(mcp_server)
    sessions[1].commit.assert_called_once()
    # mcp_tools_list must NOT have been called on failure — that's the
    # whole point of gating phase 4 on the status code.
    mock_mcp_tools.assert_not_called()


@pytest.mark.asyncio
async def test_oauth_callback_discord_validates_query_params():
    request = make_request("code=&state=")

    response = await auth.oauth_callback_discord(request)

    assert response.status_code == 400
    body = cast(bytes, response.body).decode()
    assert "Missing authorization code" in body


@patch("memory.api.auth.lookup_api_key")
def test_authenticate_bot_finds_matching_bot_via_api_key_table(mock_lookup):
    """Test authenticate_bot finds bot via new api_keys table."""
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

    mock_lookup.return_value = api_key_record

    result = auth.authenticate_bot("bot_test123", db)

    assert result is bot
    mock_lookup.assert_called_once_with("bot_test123", db)


@patch("memory.api.auth.lookup_api_key")
def test_authenticate_bot_returns_none_for_invalid_key(mock_lookup):
    """Test authenticate_bot returns None for invalid API key."""
    db = MagicMock()

    mock_lookup.return_value = None  # No matching key

    result = auth.authenticate_bot("nonexistent_key", db)

    assert result is None
    mock_lookup.assert_called_once_with("nonexistent_key", db)


@patch("memory.api.auth.lookup_api_key")
def test_authenticate_bot_rejects_non_bot_user_from_api_key_table(mock_lookup):
    """Test authenticate_bot rejects keys belonging to non-bot users."""
    db = MagicMock()

    # Mock API key belonging to a human user
    api_key_record = MagicMock()
    api_key_record.key = "human_key123"
    api_key_record.is_valid.return_value = True
    api_key_record.is_one_time = False

    human_user = MagicMock()
    human_user.user_type = "human"
    api_key_record.user = human_user

    mock_lookup.return_value = api_key_record

    result = auth.authenticate_bot("human_key123", db)

    assert result is None
    mock_lookup.assert_called_once_with("human_key123", db)


@patch("memory.api.auth.lookup_api_key")
def test_authenticate_by_api_key_returns_user_and_key_record(mock_lookup):
    """Test authenticate_by_api_key returns both user and key record."""
    db = MagicMock()

    api_key_record = MagicMock()
    api_key_record.key = "key_test123"
    api_key_record.key_type = "internal"
    api_key_record.is_valid.return_value = True
    api_key_record.is_one_time = False

    user = MagicMock()
    api_key_record.user = user

    mock_lookup.return_value = api_key_record

    result_user, result_key = auth.authenticate_by_api_key("key_test123", db)

    assert result_user is user
    assert result_key is api_key_record
    mock_lookup.assert_called_once_with("key_test123", db)


@patch("memory.api.auth.lookup_api_key")
def test_authenticate_by_api_key_respects_allowed_key_types(mock_lookup):
    """Test authenticate_by_api_key rejects keys of wrong type."""
    db = MagicMock()

    api_key_record = MagicMock()
    api_key_record.key = "discord_key123"
    api_key_record.key_type = "discord"
    api_key_record.is_valid.return_value = True

    user = MagicMock()
    api_key_record.user = user

    mock_lookup.return_value = api_key_record

    # Should reject when only "internal" is allowed
    result_user, result_key = auth.authenticate_by_api_key(
        "discord_key123", db, allowed_key_types=["internal"]
    )

    assert result_user is None
    assert result_key is None
    mock_lookup.assert_called_once_with("discord_key123", db)


@patch("memory.api.auth.lookup_api_key")
def test_authenticate_by_api_key_accepts_matching_key_type(mock_lookup):
    """Test authenticate_by_api_key accepts keys of correct type."""
    db = MagicMock()

    api_key_record = MagicMock()
    api_key_record.key = "discord_key123"
    api_key_record.key_type = "discord"
    api_key_record.is_valid.return_value = True
    api_key_record.is_one_time = False

    user = MagicMock()
    api_key_record.user = user

    mock_lookup.return_value = api_key_record

    # Should accept when "discord" is in allowed types
    result_user, result_key = auth.authenticate_by_api_key(
        "discord_key123", db, allowed_key_types=["internal", "discord"]
    )

    assert result_user is user
    assert result_key is api_key_record
    mock_lookup.assert_called_once_with("discord_key123", db)


def test_handle_api_key_use_updates_last_used():
    """Test handle_api_key_use updates last_used_at timestamp."""
    db = MagicMock()
    key_record = MagicMock()
    key_record.is_one_time = False
    key_record.last_used_at = None

    auth.handle_api_key_use(key_record, db)

    assert key_record.last_used_at is not None
    db.commit.assert_called_once()
    db.delete.assert_not_called()


def test_handle_api_key_use_deletes_one_time_key():
    """Test handle_api_key_use atomically deletes one-time keys after use.

    The delete uses ``DELETE ... WHERE id=:id RETURNING id`` (SQLAlchemy core)
    rather than ``db.delete(record)`` so concurrent consumers can't both
    succeed — see CWE-367 / task 45825913.
    """
    db = MagicMock()
    # The atomic DELETE...RETURNING returns the deleted row's id
    result = MagicMock()
    result.scalar_one_or_none.return_value = 1
    db.execute.return_value = result

    key_record = MagicMock()
    key_record.id = 1
    key_record.is_one_time = True

    won = auth.handle_api_key_use(key_record, db)

    assert won is True
    # ORM-level delete is NOT used (it would non-atomic and racy)
    db.delete.assert_not_called()
    # Atomic SQL DELETE was issued exactly once
    db.execute.assert_called_once()
    db.commit.assert_called_once()


@patch("memory.api.auth.lookup_api_key")
def test_get_session_user_uses_api_key_auth_for_api_key_tokens(mock_lookup):
    """Test that get_session_user authenticates API keys via the api_keys table."""
    # Use a token with a valid API key prefix
    request = SimpleNamespace(
        headers={"Authorization": "Bearer internal_test123"},
        cookies={},
    )
    db = MagicMock()

    # Mock API key record
    api_key_record = MagicMock()
    api_key_record.key = "internal_test123"
    api_key_record.is_valid.return_value = True
    api_key_record.is_one_time = False
    api_key_record.key_type = "internal"

    user = MagicMock()
    api_key_record.user = user

    mock_lookup.return_value = api_key_record

    result = auth.get_session_user(cast(Any, request), db)

    assert result is user
    mock_lookup.assert_called_once_with("internal_test123", db)


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


def test_get_current_user_uses_request_state_authenticated_user_id():
    """Test that get_current_user uses pre-authenticated user from request.state.

    This is important for one-time keys: the middleware authenticates and deletes
    the key, storing user_id in request.state. Endpoints must use this rather
    than re-authenticating (which would fail since key is deleted).
    """
    from memory.common.db.models import User

    db = MagicMock()
    user = MagicMock(spec=User)
    user.id = 42

    # Simulate middleware having already authenticated
    request = SimpleNamespace(
        headers={},
        cookies={},
        state=SimpleNamespace(authenticated_user_id=42),
    )

    db.get.return_value = user

    result = auth.get_current_user(cast(Any, request), db)

    assert result is user
    db.get.assert_called_once_with(User, 42)


def test_get_current_user_falls_back_to_session_auth_without_request_state():
    """Test that get_current_user falls back to normal auth when request.state is empty.

    When request.state doesn't have authenticated_user_id, the code skips the
    middleware-auth path and goes directly to get_session_user(). This happens
    for whitelisted paths or when middleware didn't run.
    """
    from memory.common.db.models import User

    db = MagicMock()
    user = MagicMock(spec=User)

    # No authenticated_user_id in request state - simulates whitelisted path
    # where middleware didn't run and set authenticated_user_id
    request = SimpleNamespace(
        headers={"Authorization": "Bearer session-uuid"},
        cookies={},
        state=SimpleNamespace(),  # Empty state, no authenticated_user_id
    )

    # Mock get_session_user to return our user (this is the fallback path)
    with patch("memory.api.auth.get_session_user", return_value=user) as mock_get_session:
        result = auth.get_current_user(cast(Any, request), db)

    assert result is user
    # Verify db.get was never called (no authenticated_user_id to look up)
    db.get.assert_not_called()
    # Verify fallback auth was used
    mock_get_session.assert_called_once_with(request, db)


# ====== One-time API key race condition (CWE-367) ======


def _one_time_key_fixture(key_id: int = 99) -> MagicMock:
    """Build a MagicMock that walks like a valid one-time APIKey row."""
    record = MagicMock()
    record.id = key_id
    record.key = "ot_secret"
    record.key_type = "one_time"
    record.is_valid.return_value = True
    record.is_one_time = True
    record.user = MagicMock()
    return record


def _make_db_for_atomic_delete(rows_returned: int) -> MagicMock:
    """Mock SQLAlchemy DB whose execute(delete()) returns one row, then None.

    Models the real Postgres semantics: the first DELETE...RETURNING wins
    and gets back the row id; the second sees zero rows.
    """
    db = MagicMock()
    results = []
    for _ in range(rows_returned):
        result = MagicMock()
        result.scalar_one_or_none.return_value = 99  # id of the deleted row
        results.append(result)
    # Subsequent calls return "no rows" — the row is already gone.
    no_row = MagicMock()
    no_row.scalar_one_or_none.return_value = None
    db.execute.side_effect = results + [no_row, no_row, no_row]
    return db


@patch("memory.api.auth.lookup_api_key")
def test_one_time_key_race_only_one_winner(mock_lookup):
    """Two concurrent authentications with the same ot_* key must not both succeed."""
    db = _make_db_for_atomic_delete(rows_returned=1)
    record = _one_time_key_fixture()
    mock_lookup.return_value = record

    # First call wins the atomic delete.
    user1, key1 = auth.authenticate_by_api_key("ot_secret", db)
    # Second concurrent call: the row is gone, RETURNING yields nothing.
    user2, key2 = auth.authenticate_by_api_key("ot_secret", db)

    assert (user1, key1) == (record.user, record)
    assert (user2, key2) == (None, None), (
        "Race-loser must not authenticate — one-time means single use"
    )


def test_handle_api_key_use_one_time_atomic_delete():
    """handle_api_key_use uses DELETE...RETURNING for one-time keys."""
    db = _make_db_for_atomic_delete(rows_returned=1)
    record = _one_time_key_fixture()

    won = auth.handle_api_key_use(record, db)

    assert won is True
    # The atomic statement was issued exactly once
    assert db.execute.call_count == 1
    # And we committed
    db.commit.assert_called_once()
    # And we did NOT use the ORM-level delete (which is non-atomic)
    db.delete.assert_not_called()


def test_handle_api_key_use_regular_key_no_delete():
    """Regular keys: no DELETE statement; just last_used_at + commit."""
    db = MagicMock()
    record = _one_time_key_fixture()
    record.is_one_time = False

    won = auth.handle_api_key_use(record, db)

    assert won is True
    db.execute.assert_not_called()
    db.delete.assert_not_called()
    db.commit.assert_called_once()


def test_handle_api_key_use_one_time_loser_returns_false():
    """If the row is already gone (race-loser), handle_api_key_use returns False."""
    db = _make_db_for_atomic_delete(rows_returned=0)  # delete returns no row
    record = _one_time_key_fixture()

    won = auth.handle_api_key_use(record, db)

    assert won is False


# ====== is_expired (tz-aware UTC handling) ======


from datetime import datetime, timedelta, timezone  # noqa: E402


def test_is_expired_naive_assumed_utc_in_past():
    # Strip tzinfo to mimic how Postgres returns naive UTC datetimes.
    past_naive = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None)
    assert auth.is_expired(past_naive) is True


def test_is_expired_naive_assumed_utc_in_future():
    future_naive = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None)
    assert auth.is_expired(future_naive) is False


def test_is_expired_aware_utc_in_past():
    past_utc = datetime.now(timezone.utc) - timedelta(hours=1)
    assert auth.is_expired(past_utc) is True


def test_is_expired_aware_utc_in_future():
    future_utc = datetime.now(timezone.utc) + timedelta(hours=1)
    assert auth.is_expired(future_utc) is False


def test_is_expired_aware_non_utc_converts_correctly():
    """A datetime tagged with a non-UTC zone must be converted, not relabeled.

    Repro for the get_user_from_token bug: a token expiring at
    2025-01-01T01:00:00+02:00 represents 2024-12-31T23:00:00 UTC. The old
    `.replace(tzinfo=UTC)` form would treat it as 2025-01-01T01:00:00 UTC,
    granting two extra hours of validity.
    """
    plus_two = timezone(timedelta(hours=2))
    # 1 hour from "now" UTC, expressed in +02:00. astimezone-correct path
    # treats this as ~1h in the future; relabel-incorrect path would treat
    # it as ~3h in the future (still future) — we use a tighter check.
    far_future_in_plus2 = (datetime.now(timezone.utc) + timedelta(hours=1)).astimezone(plus_two)
    assert auth.is_expired(far_future_in_plus2) is False

    # Now a value that's in the past in UTC but would *look* future if you
    # only relabeled. 1 hour ago UTC, expressed in -05:00:
    minus_five = timezone(timedelta(hours=-5))
    one_hour_ago_in_minus5 = (datetime.now(timezone.utc) - timedelta(hours=1)).astimezone(minus_five)
    # If we only relabeled the tzinfo (the bug), we'd take the wall-clock
    # time of the -05:00 representation and compare it to UTC `now` — that
    # comparison is wrong. The fixed code converts and gets True (expired).
    assert auth.is_expired(one_hour_ago_in_minus5) is True


def test_is_expired_none_treated_as_expired():
    """Defense-in-depth: a NULL expires_at is expired (fail-closed)."""
    assert auth.is_expired(None) is True


def test_get_user_from_token_uses_is_expired():
    """Smoke test: get_user_from_token reads session.user only when fresh."""
    db = MagicMock()
    session = MagicMock()
    session.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    session.user = MagicMock()
    db.get.return_value = session

    result = auth.get_user_from_token("session-uuid", db)
    assert result is None  # Expired → no user


def test_get_user_from_token_returns_user_when_session_fresh():
    db = MagicMock()
    session = MagicMock()
    session.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    user = MagicMock()
    session.user = user
    db.get.return_value = session

    result = auth.get_user_from_token("session-uuid", db)
    assert result is user


# ====== create_user TOCTOU on duplicate email ======


from fastapi import HTTPException as _HTTPException  # noqa: E402
from sqlalchemy.exc import IntegrityError as _IntegrityError  # noqa: E402


def test_create_user_early_check_returns_400_when_user_exists():
    """Existing user → 400 without ever calling commit()."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = MagicMock()  # existing

    with pytest.raises(_HTTPException) as exc:
        auth.create_user("a@b", "pw", "Name", db)

    assert exc.value.status_code == 400
    db.commit.assert_not_called()


def test_create_user_commit_integrityerror_returns_400():
    """Race: existence check passes, but a parallel commit landed first
    and the unique index rejects ours. Must not surface as 500."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None  # no existing
    db.commit.side_effect = _IntegrityError("INSERT", {}, Exception("simulated"))

    with patch.object(auth.HumanUser, "create_with_password", return_value=MagicMock()):
        with pytest.raises(_HTTPException) as exc:
            auth.create_user("a@b", "pw", "Name", db)

    assert exc.value.status_code == 400
    db.rollback.assert_called_once()


def test_create_user_happy_path_commits_and_refreshes():
    """No race, no existing user → user is added, committed, and refreshed."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    fake_user = MagicMock()
    with patch.object(auth.HumanUser, "create_with_password", return_value=fake_user):
        result = auth.create_user("a@b", "pw", "Name", db)

    assert result is fake_user
    db.add.assert_called_once_with(fake_user)
    db.commit.assert_called_once()
    db.refresh.assert_called_once_with(fake_user)
    db.rollback.assert_not_called()


# --- dummy_password_hash (timing-attack mitigation) -------------------------


def test_dummy_password_hash_is_valid_bcrypt_format():
    """The dummy hash must be a real, well-formed bcrypt hash.

    The previous implementation used a 46-char hard-coded string that bcrypt
    rejected as malformed, returning False in microseconds and defeating the
    whole purpose of the dummy check (timing-based user enumeration).
    A valid bcrypt hash is exactly 60 chars: ``$2b$<rounds>$<22-char salt><31-char hash>``.
    """
    import bcrypt

    digest = auth.dummy_password_hash()

    assert len(digest) == 60
    # The autouse `_fast_bcrypt` fixture rewrites gensalt to rounds=4 to keep
    # the test suite under a minute, so the cost-factor field varies between
    # environments. Production gensalt(rounds=12) is what
    # ``dummy_password_hash`` actually requests; this assertion just pins the
    # rest of the bcrypt envelope.
    assert digest.startswith("$2b$")
    # bcrypt.checkpw must NOT raise on this hash — that's the whole point.
    # And the hash should not match an arbitrary password (no fluke truthy return).
    assert bcrypt.checkpw(b"a-password-that-isnt-the-original", digest.encode()) is False


def test_dummy_password_hash_is_cached():
    """Subsequent calls return the same value (avoids re-paying ~250ms bcrypt cost)."""
    assert auth.dummy_password_hash() is auth.dummy_password_hash()


def test_dummy_password_hash_actually_verifies():
    """verify_password against the dummy must run a full bcrypt check (returns False, no exception)."""
    from memory.common.db.models.users import verify_password

    # Any password, any number of times — must consistently return False
    # without raising. If the underlying hash were malformed, verify_password
    # would still return False but via a fast exception path.
    assert verify_password("any-password", auth.dummy_password_hash()) is False
    assert verify_password("", auth.dummy_password_hash()) is False
