import secrets
import time
from typing import Optional, Any, cast
from urllib.parse import urlencode
import logging
from datetime import datetime, timezone

from memory.common.db.models.users import (
    User,
    UserSession,
    OAuthClientInformation,
    OAuthState,
)
from memory.common.db.connection import make_session
from memory.common import settings
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationParams,
    AuthorizationCode,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

logger = logging.getLogger(__name__)


class SimpleOAuthProvider(OAuthAuthorizationServerProvider):
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        """Get OAuth client information."""
        with make_session() as session:
            client = session.query(OAuthClientInformation).get(client_id)
            return client and OAuthClientInformationFull(**client.serialize())

    async def register_client(self, client_info: OAuthClientInformationFull):
        """Register a new OAuth client."""
        with make_session() as session:
            client = session.query(OAuthClientInformation).get(client_info.client_id)
            if not client:
                client = OAuthClientInformation(client_id=client_info.client_id)

            for key, value in client_info.model_dump().items():
                if key == "redirect_uris":
                    value = [str(uri) for uri in value]
                elif value and key in [
                    "client_uri",
                    "logo_uri",
                    "tos_uri",
                    "policy_uri",
                    "jwks_uri",
                ]:
                    value = str(value)
                setattr(client, key, value)
            session.add(client)
            session.commit()

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Redirect to login page for user authentication."""
        redirect_uri_str = str(params.redirect_uri)
        registered_uris = [str(uri) for uri in getattr(client, "redirect_uris", [])]
        if redirect_uri_str not in registered_uris:
            logger.error(
                f"Redirect URI {redirect_uri_str} not in registered URIs: {registered_uris}"
            )
            raise ValueError(f"Invalid redirect_uri: {redirect_uri_str}")

        # Store the authorization parameters in database
        with make_session() as session:
            oauth_state = OAuthState(
                state=params.state or secrets.token_hex(16),
                client_id=client.client_id,
                redirect_uri=str(params.redirect_uri),
                redirect_uri_provided_explicitly=str(
                    params.redirect_uri_provided_explicitly
                ).lower()
                == "true",
                code_challenge=params.code_challenge or "",
                scopes=["read", "write"],  # Default scopes
                expires_at=datetime.fromtimestamp(time.time() + 600),  # 10 min expiry
            )
            session.add(oauth_state)
            session.commit()

            return f"{settings.SERVER_URL}/oauth/login?" + urlencode(
                {
                    "state": oauth_state.state,
                    "client_id": client.client_id,
                    "redirect_uri": oauth_state.redirect_uri,
                    "redirect_uri_provided_explicitly": oauth_state.redirect_uri_provided_explicitly,
                    "code_challenge": cast(str, oauth_state.code_challenge),
                }
            )

    async def complete_authorization(self, oauth_params: dict, user: User) -> str:
        """Complete authorization after successful login."""
        if not (state := oauth_params.get("state")):
            logger.error("No state parameter provided")
            raise ValueError("Missing state parameter")

        with make_session() as session:
            # Load OAuth state from database
            oauth_state = session.query(OAuthState).get(state)
            if not oauth_state:
                logger.error(f"State {state} not found in database")
                raise ValueError("Invalid state parameter")

            # Check if state has expired
            now = datetime.fromtimestamp(time.time())
            if oauth_state.expires_at < now:
                logger.error(f"State {state} has expired")
                oauth_state.stale = True
                session.commit()
                raise ValueError("State has expired")

            oauth_state.code = f"code_{secrets.token_hex(16)}"
            oauth_state.stale = False
            oauth_state.user_id = user.id

            session.add(oauth_state)
            session.commit()

            return construct_redirect_uri(
                oauth_state.redirect_uri, code=oauth_state.code, state=state
            )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> Optional[AuthorizationCode]:
        """Load an authorization code."""
        with make_session() as session:
            auth_code = (
                session.query(OAuthState)
                .filter(OAuthState.code == authorization_code)
                .first()
            )
            if not auth_code:
                logger.error(f"Invalid authorization code: {authorization_code}")
                raise ValueError("Invalid authorization code")
            return AuthorizationCode(**auth_code.serialize(code=True))

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        """Exchange authorization code for tokens."""
        with make_session() as session:
            auth_code = (
                session.query(OAuthState)
                .filter(OAuthState.code == authorization_code.code)
                .first()
            )

            if not auth_code:
                logger.error(f"Invalid authorization code: {authorization_code.code}")
                raise ValueError("Invalid authorization code")

            # Get the user associated with this auth code
            if not auth_code.user:
                logger.error(f"No user found for auth code: {authorization_code.code}")
                raise ValueError("Invalid authorization code")

            # Create a UserSession to serve as access token
            expires_at = datetime.fromtimestamp(time.time() + 3600)

            auth_code.session = UserSession(
                user_id=auth_code.user_id,
                oauth_state_id=auth_code.state,
                expires_at=expires_at,
            )
            auth_code.stale = True  # type: ignore
            session.commit()
            access_token = str(auth_code.session.id)

        return OAuthToken(
            access_token=access_token,
            token_type="bearer",
            expires_in=3600,
            scope=" ".join(authorization_code.scopes),
        )

    async def load_access_token(self, token: str) -> Optional[AccessToken]:
        """Load and validate an access token."""
        with make_session() as session:
            now = datetime.now(timezone.utc).replace(
                tzinfo=None
            )  # Make naive for DB comparison

            # Query for active (non-expired) session
            user_session = session.query(UserSession).get(token)
            if not user_session or user_session.expires_at < now:
                return None

            return AccessToken(
                token=token,
                client_id=user_session.oauth_state.client_id,
                scopes=user_session.oauth_state.scopes,
                expires_at=int(user_session.expires_at.timestamp()),
            )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> Optional[RefreshToken]:
        """Load a refresh token - not supported in this simple implementation."""
        return None

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        """Exchange refresh token - not supported in this simple implementation."""
        raise NotImplementedError("Refresh tokens not supported")

    async def revoke_token(
        self, token: str, token_type_hint: Optional[str] = None
    ) -> None:
        """Revoke a token."""
        with make_session() as session:
            user_session = session.query(UserSession).get(token)
            if user_session:
                session.delete(user_session)
                session.commit()

    def get_protected_resource_metadata(self) -> dict[str, Any]:
        """Return metadata about the protected resource."""
        return {
            "resource_server": settings.SERVER_URL,
            "scopes_supported": ["read", "write"],
            "bearer_methods_supported": ["header"],
            "resource_documentation": f"{settings.SERVER_URL}/docs",
            "protected_resources": [
                {
                    "resource_uri": f"{settings.SERVER_URL}/mcp",
                    "scopes": ["read", "write"],
                    "http_methods": ["POST", "GET"],
                },
                {
                    "resource_uri": f"{settings.SERVER_URL}/mcp/",
                    "scopes": ["read", "write"],
                    "http_methods": ["POST", "GET"],
                },
            ],
        }
