"""Tests for MCP OAuth provider."""

import pytest
from datetime import datetime, timedelta
from typing import cast
from unittest.mock import MagicMock, patch

from sqlalchemy.pool import QueuePool

from memory.api.MCP.oauth_provider import (
    SimpleOAuthProvider,
    resolve_session_scopes,
    make_token,
)
from memory.common.db.models.users import (
    APIKey,
    APIKeyType,
    OAuthClientInformation,
    OAuthState,
    OAuthRefreshToken,
    User,
    UserSession,
)


def create_test_user(db_session, email="test@example.com", name="Test User", scopes=None):
    """Create a test user with a password hash to satisfy the auth constraint."""
    user = User(
        email=email,
        name=name,
        scopes=scopes or ["read"],
        password_hash="test_hash",
    )
    db_session.add(user)
    db_session.commit()
    return user


def create_oauth_client(db_session, client_id="test-client"):
    """Create a test OAuth client."""
    client = OAuthClientInformation(
        client_id=client_id,
        client_secret="test-secret",
        client_id_issued_at=datetime.now().timestamp(),
        redirect_uris=["http://localhost/callback"],
        token_endpoint_auth_method="client_secret_post",
        grant_types=["authorization_code"],
        response_types=["code"],
        scope="read write",
        client_name="Test Client",
    )
    db_session.add(client)
    db_session.commit()
    return client


# --- make_token tests ---


def test_make_token_from_oauth_state_sets_oauth_state_id(db_session):
    """Test that make_token sets oauth_state_id when given an OAuthState."""
    user = create_test_user(db_session)
    create_oauth_client(db_session)

    oauth_state = OAuthState(
        state="test-state",
        client_id="test-client",
        redirect_uri="http://localhost/callback",
        redirect_uri_provided_explicitly=True,
        code_challenge="test-challenge",
        scopes=["read"],
        user_id=user.id,
        expires_at=datetime.now() + timedelta(hours=1),
    )
    db_session.add(oauth_state)
    db_session.commit()

    token = make_token(db_session, oauth_state, ["read"])

    session = db_session.query(UserSession).filter(
        UserSession.id == token.access_token
    ).first()
    assert session is not None
    assert session.oauth_state_id == oauth_state.id
    assert session.user_id == user.id


def test_make_token_from_refresh_token_does_not_set_oauth_state_id(db_session):
    """Test that make_token sets oauth_state_id to None when given a refresh token."""
    user = create_test_user(db_session)
    create_oauth_client(db_session)

    refresh_token = OAuthRefreshToken(
        token="rt_test_token",
        client_id="test-client",
        user_id=user.id,
        scopes=["read"],
        expires_at=datetime.now() + timedelta(days=30),
    )
    db_session.add(refresh_token)
    db_session.commit()

    token = make_token(db_session, refresh_token, ["read"])

    session = db_session.query(UserSession).filter(
        UserSession.id == token.access_token
    ).first()
    assert session is not None
    assert session.oauth_state_id is None
    assert session.user_id == user.id


# --- verify_token tests ---


@pytest.mark.asyncio
async def test_verify_token_expands_wildcard_scopes(db_session):
    """Test that verify_token includes read/write for users with * scope."""
    user = create_test_user(db_session, scopes=["*"])

    session = UserSession(
        user_id=user.id,
        expires_at=datetime.now() + timedelta(hours=1),
    )
    db_session.add(session)
    db_session.commit()
    session_id = str(session.id)

    provider = SimpleOAuthProvider()
    result = await provider.verify_token(session_id)

    assert result is not None
    assert "*" in result.scopes
    assert "read" in result.scopes
    assert "write" in result.scopes


@pytest.mark.asyncio
async def test_verify_token_combines_user_scopes_with_oauth_scopes(db_session):
    """Test that verify_token combines user's MCP scopes with OAuth scopes."""
    user = create_test_user(db_session, scopes=["organizer", "github", "people"])

    session = UserSession(
        user_id=user.id,
        expires_at=datetime.now() + timedelta(hours=1),
    )
    db_session.add(session)
    db_session.commit()
    session_id = str(session.id)

    provider = SimpleOAuthProvider()
    result = await provider.verify_token(session_id)

    assert result is not None
    assert "organizer" in result.scopes
    assert "github" in result.scopes
    assert "people" in result.scopes
    assert "read" in result.scopes
    assert "write" in result.scopes


@pytest.mark.asyncio
async def test_verify_token_uses_user_scopes_not_oauth_state_scopes(db_session):
    """OAuth-authenticated users get their admin-configured system scopes,
    not the narrower scopes the OAuth client requested at registration.

    Regression guard for the bug introduced by PR #76 and reverted later:
    an admin user (``user.scopes == ["*"]``) authenticating via an MCP client
    that only requested ``["read"]`` would briefly lose access to every
    scope-gated tool because verify_token was sourcing scopes from
    ``OAuthState.scopes`` rather than ``User.scopes``. OAuth scopes gate the
    handshake; system scopes (admin-set on the user) gate tool visibility.
    They are distinct concepts and the user has no say in the latter.
    """
    user = create_test_user(db_session, scopes=["*"])
    create_oauth_client(db_session, client_id="some-mcp-client")

    oauth_state = OAuthState(
        state="state-token",
        client_id="some-mcp-client",
        user_id=user.id,
        # MCP client only asked for read at registration time.
        scopes=["read"],
        redirect_uri="http://localhost/callback",
        redirect_uri_provided_explicitly=True,
        code_challenge="abc",
        expires_at=datetime.now() + timedelta(hours=1),
    )
    db_session.add(oauth_state)
    db_session.commit()

    session = UserSession(
        user_id=user.id,
        oauth_state_id=oauth_state.id,
        expires_at=datetime.now() + timedelta(hours=1),
    )
    db_session.add(session)
    db_session.commit()
    session_id = str(session.id)

    provider = SimpleOAuthProvider()
    result = await provider.verify_token(session_id)

    assert result is not None
    # Admin scope must reach the visibility middleware so admin users see
    # every tool, even when the OAuth client only requested read.
    assert "*" in result.scopes
    # And the OAuth-flow scopes are still present so FastMCP's auth gate
    # accepts the token.
    assert "read" in result.scopes
    assert "write" in result.scopes
    # client_id still comes from the OAuth state (audit trail).
    assert result.client_id == "some-mcp-client"


@pytest.mark.asyncio
async def test_verify_token_returns_none_for_expired_session(db_session):
    """Test that verify_token returns None for expired sessions."""
    user = create_test_user(db_session)

    session = UserSession(
        user_id=user.id,
        # Production compares against naive UTC (now_naive_utc); match that
        # so the test isn't dependent on the local timezone.
        expires_at=datetime.utcnow() - timedelta(hours=1),
    )
    db_session.add(session)
    db_session.commit()
    session_id = str(session.id)

    provider = SimpleOAuthProvider()
    result = await provider.verify_token(session_id)

    assert result is None


@pytest.mark.asyncio
async def test_verify_token_returns_none_for_invalid_token(db_session):  # noqa: ARG001
    """Test that verify_token returns None for non-existent tokens."""
    provider = SimpleOAuthProvider()
    result = await provider.verify_token("non-existent-token")

    assert result is None


@pytest.mark.asyncio
async def test_verify_token_uses_frontend_client_id_without_oauth_state(db_session):
    """Test that sessions without oauth_state use 'frontend' as client_id."""
    user = create_test_user(db_session)

    session = UserSession(
        user_id=user.id,
        oauth_state_id=None,
        expires_at=datetime.now() + timedelta(hours=1),
    )
    db_session.add(session)
    db_session.commit()
    session_id = str(session.id)

    provider = SimpleOAuthProvider()
    result = await provider.verify_token(session_id)

    assert result is not None
    assert result.client_id == "frontend"


@pytest.mark.asyncio
async def test_verify_token_defaults_to_read_for_user_without_scopes(db_session):
    """Test that users without configured scopes get default read/write."""
    user = User(
        email="test@example.com",
        name="Test User",
        scopes=None,
        password_hash="test_hash",
    )
    db_session.add(user)
    db_session.commit()

    session = UserSession(
        user_id=user.id,
        expires_at=datetime.now() + timedelta(hours=1),
    )
    db_session.add(session)
    db_session.commit()
    session_id = str(session.id)

    provider = SimpleOAuthProvider()
    result = await provider.verify_token(session_id)

    assert result is not None
    assert "read" in result.scopes
    assert "write" in result.scopes


# --- API key tests ---


@pytest.mark.asyncio
async def test_verify_token_with_api_key_returns_scopes(db_session):
    """Test that verify_token works with API keys and returns proper scopes."""
    user = create_test_user(db_session, scopes=["organizer", "people"])

    api_key = APIKey.create(
        user_id=user.id,
        key_type=APIKeyType.INTERNAL,
        name="Test Key",
    )
    db_session.add(api_key)
    db_session.commit()
    key_value = api_key.key

    provider = SimpleOAuthProvider()
    result = await provider.verify_token(key_value)

    assert result is not None
    assert "organizer" in result.scopes
    assert "people" in result.scopes
    assert result.client_id == "Test User"


@pytest.mark.asyncio
async def test_verify_token_with_api_key_uses_key_scopes_when_set(db_session):
    """Test that verify_token uses API key scopes when explicitly set."""
    user = create_test_user(db_session, scopes=["*"])

    api_key = APIKey.create(
        user_id=user.id,
        key_type=APIKeyType.INTERNAL,
        name="Limited Key",
        scopes=["read", "write", "organizer"],
    )
    db_session.add(api_key)
    db_session.commit()
    key_value = api_key.key

    provider = SimpleOAuthProvider()
    result = await provider.verify_token(key_value)

    assert result is not None
    assert "read" in result.scopes
    assert "write" in result.scopes
    assert "organizer" in result.scopes
    assert "*" not in result.scopes


# --- One-time API key tests ---


@pytest.mark.asyncio
async def test_verify_token_deletes_one_time_api_key_after_use(db_session):
    """Test that verify_token deletes one-time API keys after successful verification."""
    user = create_test_user(db_session)

    api_key = APIKey.create(
        user_id=user.id,
        key_type=APIKeyType.ONE_TIME,
        name="One Time Key",
        scopes=["read", "write"],
    )
    db_session.add(api_key)
    db_session.commit()
    key_value = api_key.key
    key_id = api_key.id

    assert db_session.get(APIKey, key_id) is not None

    provider = SimpleOAuthProvider()
    result = await provider.verify_token(key_value)

    assert result is not None
    assert "read" in result.scopes
    assert "write" in result.scopes

    # Refresh to see changes from verify_token's session
    db_session.expire_all()
    assert db_session.get(APIKey, key_id) is None


@pytest.mark.asyncio
async def test_verify_token_one_time_key_fails_on_second_use(db_session):
    """Test that one-time API keys cannot be reused."""
    user = create_test_user(db_session)

    api_key = APIKey.create(
        user_id=user.id,
        key_type=APIKeyType.ONE_TIME,
        name="One Time Key",
        scopes=["read", "write"],
    )
    db_session.add(api_key)
    db_session.commit()
    key_value = api_key.key

    provider = SimpleOAuthProvider()

    # First use should succeed
    result1 = await provider.verify_token(key_value)
    assert result1 is not None

    # Second use should fail (key was deleted)
    result2 = await provider.verify_token(key_value)
    assert result2 is None


# --- resolve_session_scopes tests ---


def testresolve_session_scopes_uses_oauth_state_when_present(db_session):
    """When the session has an OAuthState, return its client_id and scopes."""
    user = create_test_user(db_session, scopes=["organizer", "github"])

    oauth_state = OAuthState(
        client_id="my-mcp-client",
        user_id=user.id,
        scopes=["read"],
        redirect_uri="http://localhost/callback",
        code_challenge="abc",
        code_challenge_method="S256",
        state="state-token",
    )
    db_session.add(oauth_state)
    db_session.commit()

    session = UserSession(
        user_id=user.id,
        oauth_state_id=oauth_state.id,
        expires_at=datetime.now() + timedelta(hours=1),
    )
    db_session.add(session)
    db_session.commit()

    client_id, scopes = resolve_session_scopes(session)

    assert client_id == "my-mcp-client"
    assert scopes == ["read"]
    # OAuth-issued tokens must NOT silently get the user's full scopes.
    assert "organizer" not in scopes
    assert "github" not in scopes


def testresolve_session_scopes_falls_back_to_user_scopes_for_frontend(db_session):
    """Sessions without OAuthState fall back to client_id='frontend' + user scopes."""
    user = create_test_user(db_session, scopes=["organizer", "github"])

    session = UserSession(
        user_id=user.id,
        oauth_state_id=None,
        expires_at=datetime.now() + timedelta(hours=1),
    )
    db_session.add(session)
    db_session.commit()

    client_id, scopes = resolve_session_scopes(session)

    assert client_id == "frontend"
    assert sorted(scopes) == ["github", "organizer"]


@pytest.mark.asyncio
async def test_load_access_token_does_not_grant_user_scopes_to_oauth_session(db_session):
    """Regression: load_access_token must NOT widen OAuth-issued tokens to user scopes.

    A future change that creates UserSession rows with oauth_state_id=None and
    expects them to be downscoped would break here — the right answer is an
    explicit per-session scope column, not implicit user-scope grant.
    """
    user = create_test_user(db_session, scopes=["organizer", "people"])
    create_oauth_client(db_session, client_id="downscoped-client")
    oauth_state = OAuthState(
        client_id="downscoped-client",
        user_id=user.id,
        scopes=["read"],
        redirect_uri="http://localhost/callback",
        redirect_uri_provided_explicitly=False,
        code_challenge="abc",
        state="state-token-2",
        expires_at=datetime.utcnow() + timedelta(hours=1),
    )
    db_session.add(oauth_state)
    db_session.commit()

    session = UserSession(
        user_id=user.id,
        oauth_state_id=oauth_state.id,
        expires_at=datetime.now() + timedelta(hours=1),
    )
    db_session.add(session)
    db_session.commit()
    session_id = str(session.id)

    provider = SimpleOAuthProvider()
    result = await provider.load_access_token(session_id)

    assert result is not None
    assert result.client_id == "downscoped-client"
    assert result.scopes == ["read"]
    assert "organizer" not in result.scopes
    assert "people" not in result.scopes


# --- Connection pool / leak regression tests (issue #72) ---


@pytest.mark.asyncio
async def test_verify_token_does_not_leak_connections_under_load(db_session):
    """Repeated verify_token() must not leak connections from the pool.

    Regression for issue #72: chris-api saw 15 connections stuck
    `idle in transaction` with `SELECT users` as the last query, traced to
    handle_api_key_use committing inside `make_session` while user attrs
    were read after the commit. With expire_on_commit=True (the old
    default), that read auto-began a new transaction that could leak.
    """
    from memory.common.db.connection import get_engine

    user = create_test_user(db_session, scopes=["read", "write"])

    api_key = APIKey.create(
        user_id=user.id,
        key_type=APIKeyType.INTERNAL,
        name="Load Key",
    )
    db_session.add(api_key)
    db_session.commit()
    key_value = api_key.key

    provider = SimpleOAuthProvider()
    engine = get_engine()
    pool = cast(QueuePool, engine.pool)
    baseline_checked_out = pool.checkedout()

    # Hammer the auth path harder than the pool size (5 + 10 overflow = 15).
    # If any iteration leaks a connection, we'd be stuck at >15 checkouts.
    for _ in range(50):
        result = await provider.verify_token(key_value)
        assert result is not None

    assert pool.checkedout() == baseline_checked_out, (
        f"verify_token leaked connections: "
        f"checked out went from {baseline_checked_out} to {pool.checkedout()}"
    )


@pytest.mark.asyncio
async def test_verify_token_session_path_does_not_leak(db_session):
    """Session-token path must not leak connections either.

    Same regression as above, but for the OAuth session token flow rather
    than the API key flow.
    """
    from memory.common.db.connection import get_engine

    user = create_test_user(db_session, scopes=["read"])
    session = UserSession(
        user_id=user.id,
        expires_at=datetime.now() + timedelta(hours=1),
    )
    db_session.add(session)
    db_session.commit()
    session_id = str(session.id)

    provider = SimpleOAuthProvider()
    engine = get_engine()
    pool = cast(QueuePool, engine.pool)
    baseline_checked_out = pool.checkedout()

    for _ in range(50):
        result = await provider.verify_token(session_id)
        assert result is not None

    assert pool.checkedout() == baseline_checked_out


def test_session_factory_uses_expire_on_commit_false():
    """Sessions must NOT expire attributes on commit().

    With expire_on_commit=True (SQLAlchemy default) any attribute read
    after a mid-block commit triggers a fresh SELECT that auto-begins a
    new transaction, which can leak as `idle in transaction` if the
    surrounding code returns/raises before the session's own
    commit/close. See issue #72.
    """
    from memory.common.db.connection import get_session_factory

    factory = get_session_factory()
    # sessionmaker stashes constructor kwargs on .kw
    assert factory.kw.get("expire_on_commit") is False


# ====== register_client squatting protection (no DB) ======


@pytest.mark.asyncio
async def test_register_client_rejects_duplicate_client_id():
    """RFC 7591 / RFC 7592: an unauthenticated DCR call must not be able to
    overwrite an existing client's secret, scope, or redirect_uris by
    submitting a colliding client_id. Reject with ValueError instead.
    """
    from memory.api.MCP.oauth_provider import SimpleOAuthProvider
    from mcp.shared.auth import OAuthClientInformationFull

    provider = SimpleOAuthProvider()

    payload = OAuthClientInformationFull(
        client_id="public-client-id",
        client_secret="attacker_secret",
        redirect_uris=cast(list, ["http://localhost/cb"]),
        scope="read write admin",
    )

    fake_existing = MagicMock()  # any non-None marks "already exists"
    fake_session = MagicMock()
    fake_session.__enter__.return_value = fake_session
    fake_session.__exit__.return_value = False
    fake_session.get.return_value = fake_existing

    with patch("memory.api.MCP.oauth_provider.make_session", return_value=fake_session):
        with pytest.raises(ValueError, match="already registered"):
            await provider.register_client(payload)

    # And critically: no commit happened — the existing row is untouched.
    fake_session.commit.assert_not_called()
    fake_session.add.assert_not_called()


@pytest.mark.asyncio
async def test_register_client_inserts_when_client_id_is_new():
    """Happy path: a fresh client_id passes through to a normal insert."""
    from memory.api.MCP.oauth_provider import SimpleOAuthProvider
    from mcp.shared.auth import OAuthClientInformationFull

    provider = SimpleOAuthProvider()

    payload = OAuthClientInformationFull(
        client_id="brand-new-client",
        client_secret="legit_secret",
        redirect_uris=cast(list, ["http://localhost/cb"]),
        scope="read",
    )

    fake_session = MagicMock()
    fake_session.__enter__.return_value = fake_session
    fake_session.__exit__.return_value = False
    fake_session.get.return_value = None  # no existing row

    with patch("memory.api.MCP.oauth_provider.make_session", return_value=fake_session):
        await provider.register_client(payload)

    fake_session.add.assert_called_once()
    fake_session.commit.assert_called_once()


# ====== register_client redirect_uri allowlist (no DB) ======


def _fake_session_no_existing_client():
    """Build a make_session() mock that pretends no row exists for any client_id."""
    fake_session = MagicMock()
    fake_session.__enter__.return_value = fake_session
    fake_session.__exit__.return_value = False
    fake_session.get.return_value = None
    return fake_session


@pytest.mark.parametrize(
    "redirect_uri",
    [
        # RFC 8252 §7.3 native-app loopback: OS-assigned ephemeral port.
        # Claude Code, Claude Desktop, mcp-inspector all do this — the
        # port changes every connection, so it can't be in any static
        # allowlist.
        "http://localhost:57573/callback",
        "http://127.0.0.1:49152/callback",
        "http://[::1]:65000/callback",
        # Bare host (no port) must also still work.
        "http://localhost/callback",
        "http://127.0.0.1/cb",
    ],
)
@pytest.mark.asyncio
async def test_register_client_accepts_loopback_with_any_port(redirect_uri):
    """Loopback hosts get port-agnostic matching against the allowlist.

    The default allowlist is ``http://localhost,http://127.0.0.1`` (no ports).
    Native-app OAuth clients per RFC 8252 §7.3 must spawn an ephemeral
    listener and use it as the redirect URI; the port is unknowable at
    allowlist-config time. Treat loopback as port-agnostic to support them.
    """
    from memory.api.MCP.oauth_provider import SimpleOAuthProvider
    from mcp.shared.auth import OAuthClientInformationFull
    from memory.common import settings as common_settings

    provider = SimpleOAuthProvider()
    payload = OAuthClientInformationFull(
        client_id="loopback-client",
        client_secret="s",
        redirect_uris=cast(list, [redirect_uri]),
        scope="read",
    )

    with patch.object(
        common_settings,
        "OAUTH_REDIRECT_URI_ALLOWLIST",
        ["http://localhost", "http://127.0.0.1", "http://[::1]"],
    ), patch(
        "memory.api.MCP.oauth_provider.make_session",
        return_value=_fake_session_no_existing_client(),
    ):
        # Must NOT raise — loopback ephemeral ports are part of the
        # native-app OAuth contract.
        await provider.register_client(payload)


@pytest.mark.parametrize(
    "redirect_uri",
    [
        # The original attack the strict-tuple allowlist was designed to
        # defeat: a hostname that *looks* like localhost but isn't.
        "http://localhost.evil.com/cb",
        "http://127.0.0.1.evil.com/cb",
        # Non-loopback must still require exact port: an attacker who
        # registered app.example.com:443 must not be able to redirect to
        # an attacker-controlled port on the same host.
        "http://app.example.com:8080/cb",
        # Non-loopback HTTP scheme on an unrelated host.
        "https://evil.com/cb",
    ],
)
@pytest.mark.asyncio
async def test_register_client_rejects_non_loopback_or_lookalikes(redirect_uri):
    """The loopback exception must NOT loosen non-loopback enforcement.

    Allowlist contains: ``http://localhost``, ``http://127.0.0.1``,
    ``http://app.example.com:443``. Any URI whose origin doesn't exactly
    match (post-loopback-relaxation) must still be rejected.
    """
    from memory.api.MCP.oauth_provider import SimpleOAuthProvider
    from mcp.shared.auth import OAuthClientInformationFull
    from mcp.server.auth.provider import RegistrationError
    from memory.common import settings as common_settings

    provider = SimpleOAuthProvider()
    payload = OAuthClientInformationFull(
        client_id="bad-client",
        client_secret="s",
        redirect_uris=cast(list, [redirect_uri]),
        scope="read",
    )

    with patch.object(
        common_settings,
        "OAUTH_REDIRECT_URI_ALLOWLIST",
        ["http://localhost", "http://127.0.0.1", "http://app.example.com:443"],
    ), patch(
        "memory.api.MCP.oauth_provider.make_session",
        return_value=_fake_session_no_existing_client(),
    ):
        with pytest.raises(RegistrationError) as exc_info:
            await provider.register_client(payload)

    # RFC 7591 §3.2.2: invalid_redirect_uri is the right error code, and
    # the SDK's RegistrationError gets translated to a 400 with the
    # standard OAuth error-response body. Raising plain ValueError became
    # an unhandled 500.
    assert exc_info.value.error == "invalid_redirect_uri"


# ====== Authorization-code expiry — hermetic (no DB) ======


@pytest.mark.asyncio
async def test_load_authorization_code_rejects_expired_hermetic():
    """Expiry-on-load enforcement, exercised without a real Postgres."""
    from memory.api.MCP.oauth_provider import (
        SimpleOAuthProvider,
        now_naive_utc,
    )
    from mcp.shared.auth import OAuthClientInformationFull

    provider = SimpleOAuthProvider()
    client = OAuthClientInformationFull(
        client_id="c",
        client_secret="s",
        redirect_uris=cast(list, ["http://localhost/cb"]),
    )

    expired_row = MagicMock()
    expired_row.client_id = "c"
    expired_row.expires_at = now_naive_utc() - timedelta(minutes=5)
    expired_row.serialize.return_value = {}

    fake_session = MagicMock()
    fake_session.__enter__.return_value = fake_session
    fake_session.__exit__.return_value = False
    fake_session.query.return_value.filter.return_value.first.return_value = expired_row

    with patch("memory.api.MCP.oauth_provider.make_session", return_value=fake_session):
        with pytest.raises(ValueError, match="expired"):
            await provider.load_authorization_code(client, "code_xyz")


@pytest.mark.asyncio
async def test_exchange_authorization_code_rejects_expired_hermetic():
    """Expiry-on-exchange enforcement, exercised without a real Postgres."""
    from memory.api.MCP.oauth_provider import (
        SimpleOAuthProvider,
        now_naive_utc,
    )
    from mcp.shared.auth import OAuthClientInformationFull
    from mcp.server.auth.provider import AuthorizationCode
    from pydantic import AnyUrl

    provider = SimpleOAuthProvider()
    client = OAuthClientInformationFull(
        client_id="c",
        client_secret="s",
        redirect_uris=cast(list, ["http://localhost/cb"]),
    )
    expired_code = AuthorizationCode(
        code="code_xyz",
        client_id="c",
        redirect_uri=AnyUrl("http://localhost/cb"),
        redirect_uri_provided_explicitly=True,
        scopes=["read"],
        code_challenge="",
        expires_at=(now_naive_utc() - timedelta(minutes=5)).timestamp(),
    )

    expired_row = MagicMock()
    expired_row.id = 1
    expired_row.client_id = "c"
    expired_row.user_id = 42
    expired_row.user = MagicMock()
    expired_row.expires_at = now_naive_utc() - timedelta(minutes=5)

    fake_session = MagicMock()
    fake_session.__enter__.return_value = fake_session
    fake_session.__exit__.return_value = False
    fake_session.query.return_value.filter.return_value.first.return_value = expired_row

    with patch("memory.api.MCP.oauth_provider.make_session", return_value=fake_session):
        with pytest.raises(ValueError, match="expired"):
            await provider.exchange_authorization_code(client, expired_code)


@pytest.mark.asyncio
async def test_exchange_authorization_code_race_loser_rejected_hermetic():
    """Atomic UPDATE … RETURNING — race-loser sees no row and is rejected."""
    from memory.api.MCP.oauth_provider import (
        SimpleOAuthProvider,
        now_naive_utc,
    )
    from mcp.shared.auth import OAuthClientInformationFull
    from mcp.server.auth.provider import AuthorizationCode
    from pydantic import AnyUrl

    provider = SimpleOAuthProvider()
    client = OAuthClientInformationFull(
        client_id="c",
        client_secret="s",
        redirect_uris=cast(list, ["http://localhost/cb"]),
    )
    valid_code = AuthorizationCode(
        code="code_race",
        client_id="c",
        redirect_uri=AnyUrl("http://localhost/cb"),
        redirect_uri_provided_explicitly=True,
        scopes=["read"],
        code_challenge="",
        expires_at=(now_naive_utc() + timedelta(minutes=5)).timestamp(),
    )

    valid_row = MagicMock()
    valid_row.id = 1
    valid_row.client_id = "c"
    valid_row.user_id = 42
    valid_row.user = MagicMock()
    valid_row.expires_at = now_naive_utc() + timedelta(minutes=5)

    fake_session = MagicMock()
    fake_session.__enter__.return_value = fake_session
    fake_session.__exit__.return_value = False
    fake_session.query.return_value.filter.return_value.first.return_value = valid_row

    # The atomic UPDATE … RETURNING returns no rows because another
    # concurrent exchange already cleared the code.
    update_result = MagicMock()
    update_result.scalar_one_or_none.return_value = None
    fake_session.execute.return_value = update_result

    with patch("memory.api.MCP.oauth_provider.make_session", return_value=fake_session):
        with pytest.raises(ValueError, match="already used"):
            await provider.exchange_authorization_code(client, valid_code)


# ====== Authorization-code expiry + single-use enforcement ======


@pytest.mark.asyncio
async def test_load_authorization_code_rejects_expired_code(db_session):
    """RFC 6749 §4.1.2: auth codes must not outlive their expires_at."""
    from memory.api.MCP.oauth_provider import SimpleOAuthProvider, now_naive_utc
    from mcp.shared.auth import OAuthClientInformationFull

    user = create_test_user(db_session)
    create_oauth_client(db_session)

    # Insert an OAuthState whose code is set but the row is already past expiry
    expired = OAuthState(
        state="expired-state",
        client_id="test-client",
        redirect_uri="http://localhost/callback",
        redirect_uri_provided_explicitly=True,
        code_challenge="",
        scopes=["read"],
        expires_at=now_naive_utc() - timedelta(minutes=5),
        code="code_expired_xyz",
        user_id=user.id,
    )
    db_session.add(expired)
    db_session.commit()

    provider = SimpleOAuthProvider()
    client = OAuthClientInformationFull(
        client_id="test-client",
        client_secret="test-secret",
        redirect_uris=cast(list, ["http://localhost/callback"]),
    )

    with pytest.raises(ValueError, match="expired"):
        await provider.load_authorization_code(client, "code_expired_xyz")


@pytest.mark.asyncio
async def test_exchange_authorization_code_rejects_expired_code(db_session):
    """Exchange path also enforces RFC 6749 §4.1.2."""
    from memory.api.MCP.oauth_provider import SimpleOAuthProvider, now_naive_utc
    from mcp.shared.auth import OAuthClientInformationFull
    from mcp.server.auth.provider import AuthorizationCode

    user = create_test_user(db_session)
    create_oauth_client(db_session)

    expired = OAuthState(
        state="expired-state-2",
        client_id="test-client",
        redirect_uri="http://localhost/callback",
        redirect_uri_provided_explicitly=True,
        code_challenge="",
        scopes=["read"],
        expires_at=now_naive_utc() - timedelta(minutes=5),
        code="code_expired_abc",
        user_id=user.id,
    )
    db_session.add(expired)
    db_session.commit()

    provider = SimpleOAuthProvider()
    client = OAuthClientInformationFull(
        client_id="test-client",
        client_secret="test-secret",
        redirect_uris=cast(list, ["http://localhost/callback"]),
    )
    from pydantic import AnyUrl
    auth_code = AuthorizationCode(
        code="code_expired_abc",
        client_id="test-client",
        redirect_uri=AnyUrl("http://localhost/callback"),
        redirect_uri_provided_explicitly=True,
        scopes=["read"],
        code_challenge="",
        expires_at=(now_naive_utc() - timedelta(minutes=5)).timestamp(),
    )

    with pytest.raises(ValueError, match="expired"):
        await provider.exchange_authorization_code(client, auth_code)


@pytest.mark.asyncio
async def test_exchange_authorization_code_atomic_single_use(db_session):
    """Two concurrent exchanges of the same code: only one wins (CWE-367)."""
    from memory.api.MCP.oauth_provider import SimpleOAuthProvider, now_naive_utc
    from mcp.shared.auth import OAuthClientInformationFull
    from mcp.server.auth.provider import AuthorizationCode

    user = create_test_user(db_session)
    create_oauth_client(db_session)

    state = OAuthState(
        state="single-use",
        client_id="test-client",
        redirect_uri="http://localhost/callback",
        redirect_uri_provided_explicitly=True,
        code_challenge="",
        scopes=["read"],
        expires_at=now_naive_utc() + timedelta(minutes=5),
        code="code_single_use_42",
        user_id=user.id,
    )
    db_session.add(state)
    db_session.commit()

    provider = SimpleOAuthProvider()
    client = OAuthClientInformationFull(
        client_id="test-client",
        client_secret="test-secret",
        redirect_uris=cast(list, ["http://localhost/callback"]),
    )
    from pydantic import AnyUrl
    auth_code = AuthorizationCode(
        code="code_single_use_42",
        client_id="test-client",
        redirect_uri=AnyUrl("http://localhost/callback"),
        redirect_uri_provided_explicitly=True,
        scopes=["read"],
        code_challenge="",
        expires_at=(now_naive_utc() + timedelta(minutes=5)).timestamp(),
    )

    # First exchange wins
    await provider.exchange_authorization_code(client, auth_code)

    # Second exchange (same code) must now fail — either as already-used
    # (atomic UPDATE saw NULL) or as invalid (the code field was cleared by
    # the first exchange). Either way: not a successful token.
    with pytest.raises(ValueError):
        await provider.exchange_authorization_code(client, auth_code)


@pytest.mark.transactional_db
def test_attribute_read_after_commit_does_not_autobegin(db_session):
    """Reading an attribute after commit() must not auto-begin a transaction.

    This is the specific shape of the leak in issue #72. With the old
    expire_on_commit=True default the read below would issue a SELECT
    and leave the session `in transaction`; that transaction would then
    be committed by make_session's exit but only after returning the
    object — which is enough time to leak under load/cancellation.
    """
    from memory.common.db.connection import make_session

    user = create_test_user(db_session)
    user_id = user.id

    with make_session() as session:
        loaded = session.get(User, user_id)
        assert loaded is not None
        # Mid-block commit (mirrors handle_api_key_use)
        loaded.name = "Updated Name"
        session.commit()
        # Post-commit attribute read — the hot spot. With expire_on_commit
        # =True this issued a SELECT users; with False it must not.
        _ = loaded.name
        _ = loaded.email
        # Session should be idle (no transaction) after the reads
        assert not session.in_transaction(), (
            "session auto-began a transaction on attribute read after commit "
            "— expire_on_commit is not False"
        )
