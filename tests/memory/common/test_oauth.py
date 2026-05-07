"""Tests for OAuth 2.0 flow handling."""

import logging
import pytest
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

import aiohttp

from memory.common.oauth import (
    OAuthEndpoints,
    generate_pkce_pair,
    discover_oauth_metadata,
    get_endpoints,
    register_oauth_client,
    issue_challenge,
    complete_oauth_flow,
    token_id,
)
from memory.common.db.models import MCPServer


class TestGeneratePkcePair:
    """Tests for generate_pkce_pair function."""

    def test_generates_valid_verifier_and_challenge(self):
        """Test that PKCE pair is generated correctly."""
        verifier, challenge = generate_pkce_pair()

        # Verifier should be base64url encoded (no padding)
        assert len(verifier) > 0
        assert "=" not in verifier
        assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in verifier)

        # Challenge should be base64url encoded (no padding)
        assert len(challenge) > 0
        assert "=" not in challenge
        assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in challenge)

        # They should be different
        assert verifier != challenge

    def test_generates_unique_pairs(self):
        """Test that each call generates a unique pair."""
        verifier1, challenge1 = generate_pkce_pair()
        verifier2, challenge2 = generate_pkce_pair()

        assert verifier1 != verifier2
        assert challenge1 != challenge2


class TestDiscoverOauthMetadata:
    """Tests for discover_oauth_metadata function."""

    @pytest.mark.asyncio
    async def test_discover_metadata_success(self):
        """Test successful OAuth metadata discovery."""
        metadata = {
            "authorization_endpoint": "https://example.com/auth",
            "registration_endpoint": "https://example.com/register",
            "token_endpoint": "https://example.com/token",
        }

        mock_response = Mock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=metadata)

        mock_get = AsyncMock()
        mock_get.__aenter__.return_value = mock_response
        mock_get.__aexit__.return_value = None

        mock_session = Mock()
        mock_session.get = Mock(return_value=mock_get)

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__.return_value = mock_session
        mock_session_ctx.__aexit__.return_value = None

        with patch("aiohttp.ClientSession", return_value=mock_session_ctx):
            result = await discover_oauth_metadata("https://example.com")

        assert result == metadata
        assert result is not None
        assert result["authorization_endpoint"] == "https://example.com/auth"

    @pytest.mark.asyncio
    async def test_discover_metadata_not_found(self):
        """Test OAuth metadata discovery when endpoint not found."""
        mock_response = Mock()
        mock_response.status = 404

        mock_get = AsyncMock()
        mock_get.__aenter__.return_value = mock_response
        mock_get.__aexit__.return_value = None

        mock_session = Mock()
        mock_session.get = Mock(return_value=mock_get)

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__.return_value = mock_session
        mock_session_ctx.__aexit__.return_value = None

        with patch("aiohttp.ClientSession", return_value=mock_session_ctx):
            result = await discover_oauth_metadata("https://example.com")

        assert result is None

    @pytest.mark.asyncio
    async def test_discover_metadata_connection_error(self):
        """Test OAuth metadata discovery with connection error."""
        mock_get = AsyncMock()
        mock_get.__aenter__.side_effect = aiohttp.ClientError("Connection failed")

        mock_session = Mock()
        mock_session.get = Mock(return_value=mock_get)

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__.return_value = mock_session
        mock_session_ctx.__aexit__.return_value = None

        with patch("aiohttp.ClientSession", return_value=mock_session_ctx):
            result = await discover_oauth_metadata("https://example.com")

        assert result is None


class TestGetEndpoints:
    """Tests for get_endpoints function."""

    @pytest.mark.asyncio
    async def test_get_endpoints_success(self):
        """Test successful endpoint retrieval."""
        metadata = {
            "authorization_endpoint": "https://example.com/auth",
            "registration_endpoint": "https://example.com/register",
            "token_endpoint": "https://example.com/token",
        }

        with patch("memory.common.oauth.discover_oauth_metadata", return_value=metadata):
            result = await get_endpoints("https://example.com")

        assert isinstance(result, OAuthEndpoints)
        assert result.authorization_endpoint == "https://example.com/auth"
        assert result.registration_endpoint == "https://example.com/register"
        assert result.token_endpoint == "https://example.com/token"
        assert "/auth/callback/discord" in result.redirect_uri

    @pytest.mark.asyncio
    async def test_get_endpoints_no_metadata(self):
        """Test when OAuth metadata cannot be discovered."""
        with patch("memory.common.oauth.discover_oauth_metadata", return_value=None):
            with pytest.raises(ValueError, match="Failed to connect to MCP server"):
                await get_endpoints("https://example.com")

    @pytest.mark.asyncio
    async def test_get_endpoints_missing_authorization(self):
        """Test when authorization endpoint is missing."""
        metadata = {
            "registration_endpoint": "https://example.com/register",
            "token_endpoint": "https://example.com/token",
        }

        with patch("memory.common.oauth.discover_oauth_metadata", return_value=metadata):
            with pytest.raises(ValueError, match="authorization endpoint"):
                await get_endpoints("https://example.com")

    @pytest.mark.asyncio
    async def test_get_endpoints_missing_registration(self):
        """Test when registration endpoint is missing."""
        metadata = {
            "authorization_endpoint": "https://example.com/auth",
            "token_endpoint": "https://example.com/token",
        }

        with patch("memory.common.oauth.discover_oauth_metadata", return_value=metadata):
            with pytest.raises(ValueError, match="dynamic client registration"):
                await get_endpoints("https://example.com")

    @pytest.mark.asyncio
    async def test_get_endpoints_missing_token(self):
        """Test when token endpoint is missing."""
        metadata = {
            "authorization_endpoint": "https://example.com/auth",
            "registration_endpoint": "https://example.com/register",
        }

        with patch("memory.common.oauth.discover_oauth_metadata", return_value=metadata):
            with pytest.raises(ValueError, match="token endpoint"):
                await get_endpoints("https://example.com")


class TestRegisterOauthClient:
    """Tests for register_oauth_client function."""

    @pytest.mark.asyncio
    async def test_register_client_success(self):
        """Test successful OAuth client registration."""
        endpoints = OAuthEndpoints(
            authorization_endpoint="https://example.com/auth",
            registration_endpoint="https://example.com/register",
            token_endpoint="https://example.com/token",
            redirect_uri="https://myapp.com/callback",
        )

        client_info = {"client_id": "test-client-123"}

        mock_response = Mock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value="Success")
        mock_response.json = AsyncMock(return_value=client_info)
        mock_response.raise_for_status = Mock()

        mock_post = AsyncMock()
        mock_post.__aenter__.return_value = mock_response
        mock_post.__aexit__.return_value = None

        mock_session = Mock()
        mock_session.post = Mock(return_value=mock_post)

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__.return_value = mock_session
        mock_session_ctx.__aexit__.return_value = None

        with patch("aiohttp.ClientSession", return_value=mock_session_ctx):
            client_id = await register_oauth_client(
                endpoints,
                "https://example.com",
                "Test Client",
            )

        assert client_id == "test-client-123"

    @pytest.mark.asyncio
    async def test_register_client_http_error(self):
        """Test OAuth client registration with HTTP error."""
        endpoints = OAuthEndpoints(
            authorization_endpoint="https://example.com/auth",
            registration_endpoint="https://example.com/register",
            token_endpoint="https://example.com/token",
            redirect_uri="https://myapp.com/callback",
        )

        mock_response = Mock()
        mock_response.raise_for_status = Mock(side_effect=aiohttp.ClientResponseError(
            request_info=Mock(),
            history=(),
            status=400,
            message="Bad Request",
        ))

        mock_post = AsyncMock()
        mock_post.__aenter__.return_value = mock_response
        mock_post.__aexit__.return_value = None

        mock_session = Mock()
        mock_session.post = Mock(return_value=mock_post)

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__.return_value = mock_session
        mock_session_ctx.__aexit__.return_value = None

        with patch("aiohttp.ClientSession", return_value=mock_session_ctx):
            with pytest.raises(ValueError, match="Failed to register OAuth client"):
                await register_oauth_client(
                    endpoints,
                    "https://example.com",
                    "Test Client",
                )

    @pytest.mark.asyncio
    async def test_register_client_missing_client_id(self):
        """Test OAuth client registration when response lacks client_id."""
        endpoints = OAuthEndpoints(
            authorization_endpoint="https://example.com/auth",
            registration_endpoint="https://example.com/register",
            token_endpoint="https://example.com/token",
            redirect_uri="https://myapp.com/callback",
        )

        client_info = {}  # Missing client_id

        mock_response = Mock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=client_info)
        mock_response.raise_for_status = Mock()

        mock_post = AsyncMock()
        mock_post.__aenter__.return_value = mock_response
        mock_post.__aexit__.return_value = None

        mock_session = Mock()
        mock_session.post = Mock(return_value=mock_post)

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__.return_value = mock_session
        mock_session_ctx.__aexit__.return_value = None

        with patch("aiohttp.ClientSession", return_value=mock_session_ctx):
            with pytest.raises(ValueError, match="Failed to register OAuth client"):
                await register_oauth_client(
                    endpoints,
                    "https://example.com",
                    "Test Client",
                )


class TestIssueChallenge:
    """Tests for issue_challenge function."""

    @pytest.mark.asyncio
    async def test_issue_challenge_success(self, db_session):
        """Test successful OAuth challenge issuance."""
        mcp_server = MCPServer(
            name="Test Server",
            mcp_server_url="https://example.com",
            client_id="test-client-123",
        )
        db_session.add(mcp_server)
        db_session.commit()

        endpoints = OAuthEndpoints(
            authorization_endpoint="https://example.com/auth",
            registration_endpoint="https://example.com/register",
            token_endpoint="https://example.com/token",
            redirect_uri="https://myapp.com/callback",
        )

        with patch("memory.common.oauth.generate_pkce_pair", return_value=("verifier123", "challenge123")):
            auth_url = await issue_challenge(mcp_server, endpoints)

        # Verify the auth URL contains expected parameters
        assert "https://example.com/auth?" in auth_url
        assert "client_id=test-client-123" in auth_url
        # redirect_uri will be URL encoded
        assert "redirect_uri=" in auth_url
        assert "myapp.com" in auth_url
        assert "callback" in auth_url
        assert "response_type=code" in auth_url
        assert "code_challenge=challenge123" in auth_url
        assert "code_challenge_method=S256" in auth_url
        assert "state=" in auth_url

        # Verify state and code_verifier were stored
        assert mcp_server.state is not None
        assert mcp_server.code_verifier == "verifier123"


class TestCompleteOauthFlow:
    """Tests for complete_oauth_flow function."""

    @pytest.mark.asyncio
    async def test_complete_oauth_flow_success(self, db_session):
        """Test successful OAuth flow completion."""
        mcp_server = MCPServer(
            name="Test Server",
            mcp_server_url="https://example.com",
            client_id="test-client-123",
            state="test-state",
            code_verifier="test-verifier",
        )
        db_session.add(mcp_server)
        db_session.commit()

        metadata = {
            "authorization_endpoint": "https://example.com/auth",
            "registration_endpoint": "https://example.com/register",
            "token_endpoint": "https://example.com/token",
        }

        token_response = {
            "access_token": "access-token-123",
            "refresh_token": "refresh-token-123",
            "expires_in": 3600,
        }

        mock_token_response = Mock()
        mock_token_response.status = 200
        mock_token_response.json = AsyncMock(return_value=token_response)

        mock_post = AsyncMock()
        mock_post.__aenter__.return_value = mock_token_response
        mock_post.__aexit__.return_value = None

        mock_session = Mock()
        mock_session.post = Mock(return_value=mock_post)

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__.return_value = mock_session
        mock_session_ctx.__aexit__.return_value = None

        with (
            patch("memory.common.oauth.discover_oauth_metadata", return_value=metadata),
            patch("aiohttp.ClientSession", return_value=mock_session_ctx),
        ):
            status, message = await complete_oauth_flow(
                mcp_server,
                "auth-code-123",
                "test-state",
            )

        assert status == 200
        assert "successful" in message

        # Verify tokens were stored
        assert mcp_server.access_token == "access-token-123"
        assert mcp_server.refresh_token == "refresh-token-123"
        assert mcp_server.token_expires_at is not None

        # Verify temporary state was cleared
        assert mcp_server.state is None
        assert mcp_server.code_verifier is None

    @pytest.mark.asyncio
    async def test_complete_oauth_flow_invalid_state(self):
        """Test OAuth flow completion with invalid state."""
        status, message = await complete_oauth_flow(
            cast(Any, None),
            "auth-code-123",
            "invalid-state",
        )

        assert status == 400
        assert "Invalid or expired" in message

    @pytest.mark.asyncio
    async def test_complete_oauth_flow_token_error(self, db_session):
        """Test OAuth flow completion when token exchange fails."""
        mcp_server = MCPServer(
            name="Test Server",
            mcp_server_url="https://example.com",
            client_id="test-client-123",
            state="test-state",
            code_verifier="test-verifier",
        )
        db_session.add(mcp_server)
        db_session.commit()

        metadata = {
            "authorization_endpoint": "https://example.com/auth",
            "registration_endpoint": "https://example.com/register",
            "token_endpoint": "https://example.com/token",
        }

        mock_token_response = Mock()
        mock_token_response.status = 400
        mock_token_response.text = AsyncMock(return_value="Invalid grant")

        mock_post = AsyncMock()
        mock_post.__aenter__.return_value = mock_token_response
        mock_post.__aexit__.return_value = None

        mock_session = Mock()
        mock_session.post = Mock(return_value=mock_post)

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__.return_value = mock_session
        mock_session_ctx.__aexit__.return_value = None

        with (
            patch("memory.common.oauth.discover_oauth_metadata", return_value=metadata),
            patch("aiohttp.ClientSession", return_value=mock_session_ctx),
        ):
            status, message = await complete_oauth_flow(
                mcp_server,
                "invalid-code",
                "test-state",
            )

        assert status == 500
        assert "Token exchange failed" in message

    @pytest.mark.asyncio
    async def test_complete_oauth_flow_missing_access_token(self, db_session):
        """Test OAuth flow completion when access token is missing from response."""
        mcp_server = MCPServer(
            name="Test Server",
            mcp_server_url="https://example.com",
            client_id="test-client-123",
            state="test-state",
            code_verifier="test-verifier",
        )
        db_session.add(mcp_server)
        db_session.commit()

        metadata = {
            "authorization_endpoint": "https://example.com/auth",
            "registration_endpoint": "https://example.com/register",
            "token_endpoint": "https://example.com/token",
        }

        token_response = {}  # Missing access_token

        mock_token_response = Mock()
        mock_token_response.status = 200
        mock_token_response.json = AsyncMock(return_value=token_response)

        mock_post = AsyncMock()
        mock_post.__aenter__.return_value = mock_token_response
        mock_post.__aexit__.return_value = None

        mock_session = Mock()
        mock_session.post = Mock(return_value=mock_post)

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__.return_value = mock_session
        mock_session_ctx.__aexit__.return_value = None

        with (
            patch("memory.common.oauth.discover_oauth_metadata", return_value=metadata),
            patch("aiohttp.ClientSession", return_value=mock_session_ctx),
        ):
            status, message = await complete_oauth_flow(
                mcp_server,
                "auth-code-123",
                "test-state",
            )

        assert status == 500
        assert "did not include access_token" in message

    @pytest.mark.asyncio
    async def test_complete_oauth_flow_get_endpoints_error(self, db_session):
        """Test OAuth flow completion when getting endpoints fails."""
        mcp_server = MCPServer(
            name="Test Server",
            mcp_server_url="https://example.com",
            client_id="test-client-123",
            state="test-state",
            code_verifier="test-verifier",
        )
        db_session.add(mcp_server)
        db_session.commit()

        with patch("memory.common.oauth.discover_oauth_metadata", return_value=None):
            status, message = await complete_oauth_flow(
                mcp_server,
                "auth-code-123",
                "test-state",
            )

        assert status == 500
        assert "Failed to get OAuth endpoints" in message


# ----- Log redaction regression -----
#
# Previously several call sites in oauth.py / oauth_provider.py logged
# raw authorization codes and access-token prefixes, which made replay
# from log aggregators trivial. Pin the redaction here.


def test_token_id_is_short_non_reversible_and_stable():
    """token_id should hash, not preview — and round-trip the same input."""
    code = "ac_3pq_secret_DO_NOT_LOG"

    digest = token_id(code)

    assert len(digest) == 8
    # Must not be a prefix of the original (the historical "first 20 chars" log).
    assert digest not in code
    # Stable across calls (correlation id, not random).
    assert token_id(code) == digest


@pytest.mark.asyncio
async def test_issue_challenge_does_not_log_raw_state(caplog):
    """issue_challenge must log a SHA-prefix id, not the raw state value."""
    mcp_server = Mock()
    mcp_server.mcp_server_url = "https://example.com"
    mcp_server.client_id = "client-id"
    mcp_server.id = 1

    endpoints = OAuthEndpoints(
        authorization_endpoint="https://example.com/auth",
        registration_endpoint="https://example.com/register",
        token_endpoint="https://example.com/token",
        redirect_uri="https://myapp.com/callback",
    )

    with caplog.at_level(logging.INFO, logger="memory.common.oauth"):
        await issue_challenge(mcp_server, endpoints)

    state = mcp_server.state
    code_verifier = mcp_server.code_verifier
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    # Raw state and verifier must never appear in the log line.
    assert state not in log_text
    assert code_verifier not in log_text
    # Their SHA-prefix correlation ids should.
    assert token_id(state) in log_text
    assert token_id(code_verifier) in log_text


@pytest.mark.asyncio
async def test_complete_oauth_flow_invalid_state_does_not_log_raw_state(caplog):
    """The error path for an invalid state must not echo the raw state value."""
    state = "extremely-secret-state-value-please-no"

    with caplog.at_level(logging.ERROR, logger="memory.common.oauth"):
        status, _message = await complete_oauth_flow(
            cast(Any, None),  # simulates the "no MCP server for this state" branch
            "code-irrelevant",
            state,
        )

    assert status == 400
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert state not in log_text
    # The truncated 20-char prefix that used to be logged must also be gone.
    assert state[:20] not in log_text
    # But a correlation id must still be present so logs are useful.
    assert token_id(state) in log_text


# ----- token_expires_at: tz-aware UTC -----


@pytest.mark.parametrize("expires_in", [3600, "3600", 7200, "7200"])
@pytest.mark.asyncio
async def test_complete_oauth_flow_token_expires_at_is_tz_aware_utc(
    monkeypatch, expires_in
):
    """token_expires_at must be tz-aware UTC, regardless of expires_in's type.

    The fix: switch from the naive `datetime.now()` (which uses local time)
    to `datetime.now(timezone.utc)`, and coerce expires_in to int so a
    string-typed value from a quirky upstream provider doesn't blow up
    inside `timedelta`. Without this, the column either compares-incorrectly
    against tz-aware comparisons elsewhere or raises TypeError.
    """
    from datetime import timezone as tz

    # Stand-in for the MCPServer ORM object — only the attributes the
    # success path mutates need to exist.
    class FakeMcp:
        id = 1
        mcp_server_url = "https://example.com"
        client_id = "client-xyz"
        code_verifier = "verifier"
        state = "state"
        access_token = None
        refresh_token = None
        token_expires_at = None

    mcp_server = FakeMcp()

    metadata = {
        "authorization_endpoint": "https://example.com/auth",
        "registration_endpoint": "https://example.com/register",
        "token_endpoint": "https://example.com/token",
    }
    token_response = {
        "access_token": "access-tok",
        "refresh_token": "refresh-tok",
        "expires_in": expires_in,
    }
    mock_token_response = Mock()
    mock_token_response.status = 200
    mock_token_response.json = AsyncMock(return_value=token_response)

    mock_post = AsyncMock()
    mock_post.__aenter__.return_value = mock_token_response
    mock_post.__aexit__.return_value = None

    mock_session = Mock()
    mock_session.post = Mock(return_value=mock_post)

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__.return_value = mock_session
    mock_session_ctx.__aexit__.return_value = None

    with (
        patch("memory.common.oauth.discover_oauth_metadata", return_value=metadata),
        patch("aiohttp.ClientSession", return_value=mock_session_ctx),
    ):
        status, _ = await complete_oauth_flow(
            cast(Any, mcp_server), "auth-code-xyz", "state"
        )

    assert status == 200
    assert mcp_server.token_expires_at is not None
    # The big invariant the original task is about: tz-aware UTC.
    assert mcp_server.token_expires_at.tzinfo is not None
    assert mcp_server.token_expires_at.utcoffset() == tz.utc.utcoffset(None)


@pytest.mark.asyncio
async def test_complete_oauth_flow_garbage_expires_in_falls_back_to_default(caplog):
    """A non-integer expires_in must default to 3600 with a warning, not crash."""
    from datetime import timezone as tz

    class FakeMcp:
        id = 1
        mcp_server_url = "https://example.com"
        client_id = "client-xyz"
        code_verifier = "verifier"
        state = "state"
        access_token = None
        refresh_token = None
        token_expires_at = None

    mcp_server = FakeMcp()

    metadata = {
        "authorization_endpoint": "https://example.com/auth",
        "registration_endpoint": "https://example.com/register",
        "token_endpoint": "https://example.com/token",
    }
    token_response = {
        "access_token": "access-tok",
        "refresh_token": "refresh-tok",
        "expires_in": "not-a-number",
    }
    mock_token_response = Mock()
    mock_token_response.status = 200
    mock_token_response.json = AsyncMock(return_value=token_response)

    mock_post = AsyncMock()
    mock_post.__aenter__.return_value = mock_token_response
    mock_post.__aexit__.return_value = None

    mock_session = Mock()
    mock_session.post = Mock(return_value=mock_post)

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__.return_value = mock_session
    mock_session_ctx.__aexit__.return_value = None

    with (
        patch("memory.common.oauth.discover_oauth_metadata", return_value=metadata),
        patch("aiohttp.ClientSession", return_value=mock_session_ctx),
        caplog.at_level(logging.WARNING, logger="memory.common.oauth"),
    ):
        status, _ = await complete_oauth_flow(
            cast(Any, mcp_server), "auth-code-xyz", "state"
        )

    assert status == 200
    assert mcp_server.token_expires_at is not None
    assert mcp_server.token_expires_at.tzinfo is not None
    # The warning should mention the bad value so operators can find the offender.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "not-a-number" in log_text
