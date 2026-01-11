"""Tests for MCP OAuth provider."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock

from memory.api.MCP.oauth_provider import (
    SimpleOAuthProvider,
    make_token,
    create_expiration,
    ACCESS_TOKEN_LIFETIME,
)
from memory.common.db.models.users import (
    OAuthClientInformation,
    OAuthState,
    OAuthRefreshToken,
    User,
    UserSession,
)


class TestMakeToken:
    """Tests for make_token function."""

    def test_make_token_from_oauth_state_sets_oauth_state_id(self, db_session):
        """Test that make_token sets oauth_state_id when given an OAuthState."""
        # Create a user
        user = User(email="test@example.com", name="Test User", scopes=["read"])
        db_session.add(user)
        db_session.commit()

        # Create an OAuth client (required for FK constraint)
        client = OAuthClientInformation(
            client_id="test-client",
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

        # Create an OAuth state
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

        # Call make_token
        token = make_token(db_session, oauth_state, ["read"])

        # Verify the session was created with oauth_state_id
        session = db_session.query(UserSession).filter(
            UserSession.id == token.access_token
        ).first()
        assert session is not None
        assert session.oauth_state_id == oauth_state.id
        assert session.user_id == user.id

    def test_make_token_from_refresh_token_does_not_set_oauth_state_id(self, db_session):
        """Test that make_token sets oauth_state_id to None when given a refresh token."""
        # Create a user
        user = User(email="test@example.com", name="Test User", scopes=["read"])
        db_session.add(user)
        db_session.commit()

        # Create an OAuth client (required for FK constraint)
        client = OAuthClientInformation(
            client_id="test-client",
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

        # Create a refresh token (not an OAuthState)
        refresh_token = OAuthRefreshToken(
            token="rt_test_token",
            client_id="test-client",
            user_id=user.id,
            scopes=["read"],
            expires_at=datetime.now() + timedelta(days=30),
        )
        db_session.add(refresh_token)
        db_session.commit()

        # Call make_token with refresh token
        token = make_token(db_session, refresh_token, ["read"])

        # Verify the session was created WITHOUT oauth_state_id
        session = db_session.query(UserSession).filter(
            UserSession.id == token.access_token
        ).first()
        assert session is not None
        assert session.oauth_state_id is None  # Key assertion!
        assert session.user_id == user.id


class TestVerifyToken:
    """Tests for SimpleOAuthProvider.verify_token method."""

    @pytest.mark.asyncio
    async def test_verify_token_expands_wildcard_scopes(self, db_session):
        """Test that verify_token includes read/write for users with * scope."""
        # Create a user with wildcard scope
        user = User(email="test@example.com", name="Test User", scopes=["*"])
        db_session.add(user)
        db_session.commit()

        # Create a session
        session = UserSession(
            user_id=user.id,
            expires_at=datetime.now() + timedelta(hours=1),
        )
        db_session.add(session)
        db_session.commit()
        session_id = str(session.id)

        # Mock make_session to return our test session
        with patch("memory.api.MCP.oauth_provider.make_session") as mock_make_session:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = Mock(return_value=db_session)
            mock_ctx.__exit__ = Mock(return_value=None)
            mock_make_session.return_value = mock_ctx

            provider = SimpleOAuthProvider()
            result = await provider.verify_token(session_id)

        assert result is not None
        assert "*" in result.scopes
        assert "read" in result.scopes
        assert "write" in result.scopes

    @pytest.mark.asyncio
    async def test_verify_token_combines_user_scopes_with_oauth_scopes(self, db_session):
        """Test that verify_token combines user's MCP scopes with OAuth scopes."""
        # Create a user with specific scopes
        user = User(
            email="test@example.com",
            name="Test User",
            scopes=["organizer", "github", "people"],
        )
        db_session.add(user)
        db_session.commit()

        # Create a session
        session = UserSession(
            user_id=user.id,
            expires_at=datetime.now() + timedelta(hours=1),
        )
        db_session.add(session)
        db_session.commit()
        session_id = str(session.id)

        with patch("memory.api.MCP.oauth_provider.make_session") as mock_make_session:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = Mock(return_value=db_session)
            mock_ctx.__exit__ = Mock(return_value=None)
            mock_make_session.return_value = mock_ctx

            provider = SimpleOAuthProvider()
            result = await provider.verify_token(session_id)

        assert result is not None
        # User's MCP scopes
        assert "organizer" in result.scopes
        assert "github" in result.scopes
        assert "people" in result.scopes
        # FastMCP OAuth scopes (always included)
        assert "read" in result.scopes
        assert "write" in result.scopes

    @pytest.mark.asyncio
    async def test_verify_token_returns_none_for_expired_session(self, db_session):
        """Test that verify_token returns None for expired sessions."""
        # Create a user
        user = User(email="test@example.com", name="Test User", scopes=["read"])
        db_session.add(user)
        db_session.commit()

        # Create an expired session
        session = UserSession(
            user_id=user.id,
            expires_at=datetime.now() - timedelta(hours=1),  # Expired
        )
        db_session.add(session)
        db_session.commit()
        session_id = str(session.id)

        with patch("memory.api.MCP.oauth_provider.make_session") as mock_make_session:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = Mock(return_value=db_session)
            mock_ctx.__exit__ = Mock(return_value=None)
            mock_make_session.return_value = mock_ctx

            provider = SimpleOAuthProvider()
            result = await provider.verify_token(session_id)

        assert result is None

    @pytest.mark.asyncio
    async def test_verify_token_returns_none_for_invalid_token(self, db_session):
        """Test that verify_token returns None for non-existent tokens."""
        with patch("memory.api.MCP.oauth_provider.make_session") as mock_make_session:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = Mock(return_value=db_session)
            mock_ctx.__exit__ = Mock(return_value=None)
            mock_make_session.return_value = mock_ctx

            provider = SimpleOAuthProvider()
            result = await provider.verify_token("non-existent-token")

        assert result is None

    @pytest.mark.asyncio
    async def test_verify_token_uses_frontend_client_id_without_oauth_state(self, db_session):
        """Test that sessions without oauth_state use 'frontend' as client_id."""
        # Create a user
        user = User(email="test@example.com", name="Test User", scopes=["read"])
        db_session.add(user)
        db_session.commit()

        # Create a session without oauth_state (simulates refresh token path)
        session = UserSession(
            user_id=user.id,
            oauth_state_id=None,  # No OAuth state
            expires_at=datetime.now() + timedelta(hours=1),
        )
        db_session.add(session)
        db_session.commit()
        session_id = str(session.id)

        with patch("memory.api.MCP.oauth_provider.make_session") as mock_make_session:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = Mock(return_value=db_session)
            mock_ctx.__exit__ = Mock(return_value=None)
            mock_make_session.return_value = mock_ctx

            provider = SimpleOAuthProvider()
            result = await provider.verify_token(session_id)

        assert result is not None
        assert result.client_id == "frontend"

    @pytest.mark.asyncio
    async def test_verify_token_defaults_to_read_for_user_without_scopes(self, db_session):
        """Test that users without configured scopes get default read/write."""
        # Create a user without scopes
        user = User(email="test@example.com", name="Test User", scopes=None)
        db_session.add(user)
        db_session.commit()

        # Create a session
        session = UserSession(
            user_id=user.id,
            expires_at=datetime.now() + timedelta(hours=1),
        )
        db_session.add(session)
        db_session.commit()
        session_id = str(session.id)

        with patch("memory.api.MCP.oauth_provider.make_session") as mock_make_session:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = Mock(return_value=db_session)
            mock_ctx.__exit__ = Mock(return_value=None)
            mock_make_session.return_value = mock_ctx

            provider = SimpleOAuthProvider()
            result = await provider.verify_token(session_id)

        assert result is not None
        assert "read" in result.scopes
        assert "write" in result.scopes
