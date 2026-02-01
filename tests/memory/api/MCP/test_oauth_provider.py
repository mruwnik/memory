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
