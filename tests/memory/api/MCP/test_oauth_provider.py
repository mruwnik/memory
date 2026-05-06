"""Tests for MCP OAuth provider."""

import pytest
from datetime import datetime, timedelta

from memory.api.MCP.oauth_provider import (
    SimpleOAuthProvider,
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
async def test_verify_token_returns_none_for_expired_session(db_session):
    """Test that verify_token returns None for expired sessions."""
    user = create_test_user(db_session)

    session = UserSession(
        user_id=user.id,
        expires_at=datetime.now() - timedelta(hours=1),
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
    pool = engine.pool
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
    pool = engine.pool
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
