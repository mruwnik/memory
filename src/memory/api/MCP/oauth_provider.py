import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Optional, cast
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from memory.common import settings
from memory.common.db.connection import make_session, scoped_session
from memory.common.db.models.users import (
    OAuthClientInformation,
    OAuthRefreshToken,
    OAuthState,
    User,
    BotUser,
    UserSession,
)
from memory.common.db.models.users import (
    OAuthToken as TokenBase,
)

logger = logging.getLogger(__name__)

ALLOWED_SCOPES = ["read", "write", "claudeai"]
BASE_SCOPES = ["read"]
RW_SCOPES = ["read", "write"]


# Token configuration constants
ACCESS_TOKEN_LIFETIME = 3600 * 30 * 24  # 30 days
REFRESH_TOKEN_LIFETIME = 30 * 24 * 3600  # 30 days


def create_expiration(lifetime_seconds: int) -> datetime:
    """Create expiration datetime from lifetime in seconds."""
    return datetime.fromtimestamp(time.time() + lifetime_seconds)


def generate_refresh_token() -> str:
    """Generate a new refresh token."""
    return f"rt_{secrets.token_hex(32)}"


def create_access_token_session(
    user_id: int, oauth_state_id: str | None = None
) -> UserSession:
    """Create a new access token session."""
    return UserSession(
        user_id=user_id,
        oauth_state_id=oauth_state_id,
        expires_at=create_expiration(ACCESS_TOKEN_LIFETIME),
    )


def create_refresh_token_record(
    client_id: str,
    user_id: int,
    scopes: list[str],
    access_token_session_id: Optional[str] = None,
) -> OAuthRefreshToken:
    """Create a new refresh token record."""
    return OAuthRefreshToken(
        token=generate_refresh_token(),
        client_id=client_id,
        user_id=user_id,
        scopes=scopes,
        expires_at=create_expiration(REFRESH_TOKEN_LIFETIME),
        access_token_session_id=access_token_session_id,
    )


def validate_refresh_token(db_refresh_token: OAuthRefreshToken) -> None:
    """Validate a refresh token, raising ValueError if invalid."""
    now = datetime.now()
    if db_refresh_token.expires_at < now:  # type: ignore
        logger.error(f"Refresh token expired: {db_refresh_token.token[:20]}...")
        db_refresh_token.revoked = True  # type: ignore
        raise ValueError("Refresh token expired")


def create_oauth_token(
    access_token: str, scopes: list[str], refresh_token: Optional[str] = None
) -> OAuthToken:
    """Create an OAuth token response."""
    return OAuthToken(
        access_token=access_token,
        token_type="Bearer",
        expires_in=ACCESS_TOKEN_LIFETIME,
        refresh_token=refresh_token,
        scope=" ".join(scopes),
    )


def make_token(
    db: scoped_session,
    auth_state: TokenBase,
    scopes: list[str],
) -> OAuthToken:
    new_session = UserSession(
        user_id=auth_state.user_id,
        oauth_state_id=auth_state.id,
        expires_at=create_expiration(ACCESS_TOKEN_LIFETIME),
    )

    # Create refresh token
    refresh_token = create_refresh_token_record(
        cast(str, auth_state.client_id),
        cast(int, auth_state.user_id),
        scopes,
        cast(str, new_session.id),
    )

    db.add(new_session)
    db.add(refresh_token)
    db.commit()

    return create_oauth_token(
        str(new_session.id),
        scopes,
        cast(str, refresh_token.token),
    )


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

        # Determine which scopes to grant
        requested_scopes = getattr(params, "scopes", None) or []

        if not requested_scopes:
            # Use default scopes if none requested
            requested_scopes = BASE_SCOPES

        # Validate requested scopes are allowed
        requested_scopes_set = set(requested_scopes)
        allowed_scopes_set = set(ALLOWED_SCOPES)

        if not requested_scopes_set.issubset(allowed_scopes_set):
            invalid_scopes = requested_scopes_set - allowed_scopes_set
            raise ValueError(f"Invalid scopes: {', '.join(invalid_scopes)}")

        # Check if requested scopes are in client's registered scopes
        client_scopes = (
            getattr(client, "scope", "").split() if hasattr(client, "scope") else []
        )
        client_scopes_set = set(client_scopes)

        if client_scopes and not requested_scopes_set.issubset(client_scopes_set):
            invalid_scopes = requested_scopes_set - client_scopes_set
            logger.error(
                f"❌ Client was not registered with scope(s): {invalid_scopes}"
            )
            raise ValueError(
                f"Client was not registered with scope {', '.join(invalid_scopes)}"
            )

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
                scopes=requested_scopes,
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
        logger.info(f"Completing authorization with params: {oauth_params}")
        if not (state := oauth_params.get("state")):
            logger.error("No state parameter provided")
            raise ValueError("Missing state parameter")

        with make_session() as session:
            # Load OAuth state from database
            oauth_state = (
                session.query(OAuthState).filter(OAuthState.state == state).first()
            )
            if not oauth_state:
                logger.error(f"State {state} not found in database")
                raise ValueError("Invalid state parameter")

            # Check if state has expired
            now = datetime.fromtimestamp(time.time())
            if oauth_state.expires_at < now:
                logger.error(f"State {state} has expired")
                oauth_state.stale = True  # type: ignore
                session.commit()
                raise ValueError("State has expired")

            oauth_state.code = f"code_{secrets.token_hex(16)}"  # type: ignore
            oauth_state.stale = False  # type: ignore
            oauth_state.user_id = user.id

            session.add(oauth_state)
            session.commit()

            parsed_uri = urlparse(str(oauth_state.redirect_uri))
            query_params = {
                k: ",".join(v) for k, v in parse_qs(parsed_uri.query).items()
            }
            query_params |= {
                "code": oauth_state.code,
                "state": state,
            }
            return urlunparse(parsed_uri._replace(query=urlencode(query_params)))

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> Optional[AuthorizationCode]:
        """Load an authorization code."""
        logger.info(f"Loading authorization code: {authorization_code}")
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
        logger.info(f"Exchanging authorization code: {authorization_code}")
        with make_session() as session:
            auth_code = (
                session.query(OAuthState)
                .filter(OAuthState.code == authorization_code.code)
                .first()
            )

            if not auth_code:
                logger.error(f"Invalid authorization code: {authorization_code.code}")
                raise ValueError("Invalid authorization code")

            if not auth_code.user:
                logger.error(f"No user found for auth code: {authorization_code.code}")
                raise ValueError("Invalid authorization code")

            token = make_token(session, auth_code, authorization_code.scopes)
            logger.info(f"Exchanged authorization code: {token}")
            return token

    async def load_access_token(self, token: str) -> Optional[AccessToken]:
        """Load and validate an access token (or bot API key)."""
        with make_session() as session:
            # Try as OAuth access token first
            user_session = session.query(UserSession).get(token)
            if user_session:
                now = datetime.now(timezone.utc).replace(
                    tzinfo=None
                )  # Make naive for DB comparison

                if user_session.expires_at < now:
                    return None

                return AccessToken(
                    token=token,
                    client_id=user_session.oauth_state.client_id,
                    scopes=user_session.oauth_state.scopes,
                    expires_at=int(user_session.expires_at.timestamp()),
                )

            # Try as bot API key
            bot = session.query(User).filter(User.api_key == token).first()
            if bot:
                logger.info(f"Bot {bot.name} (id={bot.id}) authenticated via API key")
                return AccessToken(
                    token=token,
                    client_id=cast(str, bot.name or bot.email),
                    scopes=["read", "write"],  # Bots get full access
                    expires_at=2147483647,  # Far future (2038)
                )

            return None

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> Optional[RefreshToken]:
        """Load and validate a refresh token."""
        with make_session() as session:
            now = datetime.now()

            # Query for the refresh token
            db_refresh_token = (
                session.query(OAuthRefreshToken)
                .filter(
                    OAuthRefreshToken.token == refresh_token,
                    OAuthRefreshToken.client_id == client.client_id,
                    OAuthRefreshToken.revoked == False,  # noqa: E712
                    OAuthRefreshToken.expires_at > now,
                )
                .first()
            )

            if not db_refresh_token:
                logger.error(
                    f"Invalid or expired refresh token: {refresh_token[:20]}..."
                )
                return None

            return RefreshToken(
                token=refresh_token,
                client_id=client.client_id,
                scopes=cast(list[str], db_refresh_token.scopes),
                expires_at=int(db_refresh_token.expires_at.timestamp()),
            )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        """Exchange refresh token for new access token."""
        with make_session() as session:
            # Load the refresh token from database
            db_refresh_token = (
                session.query(OAuthRefreshToken)
                .filter(
                    OAuthRefreshToken.token == refresh_token.token,
                    OAuthRefreshToken.client_id == client.client_id,
                    OAuthRefreshToken.revoked == False,  # noqa: E712
                )
                .first()
            )

            if not db_refresh_token:
                logger.error(f"Refresh token not found: {refresh_token.token[:20]}...")
                raise ValueError("Invalid refresh token")

            # Validate refresh token
            validate_refresh_token(db_refresh_token)

            # Validate requested scopes are subset of original scopes
            original_scopes = set(cast(list[str], db_refresh_token.scopes))
            requested_scopes = set(scopes) if scopes else original_scopes

            if not requested_scopes.issubset(original_scopes):
                logger.error(
                    f"Requested scopes {requested_scopes} exceed original scopes {original_scopes}"
                )
                raise ValueError("Requested scopes exceed original authorization")

            return make_token(session, db_refresh_token, scopes)

    async def revoke_token(
        self, token: str, token_type_hint: Optional[str] = None
    ) -> None:
        """Revoke a token (access token or refresh token)."""
        with make_session() as session:
            revoked = False

            # Try to revoke as access token (UserSession)
            if not token_type_hint or token_type_hint == "access_token":
                user_session = session.query(UserSession).get(token)
                if user_session:
                    session.delete(user_session)
                    revoked = True
                    logger.info(f"Revoked access token: {token[:20]}...")

            # Try to revoke as refresh token
            if not revoked and (
                not token_type_hint or token_type_hint == "refresh_token"
            ):
                refresh_token = (
                    session.query(OAuthRefreshToken)
                    .filter(OAuthRefreshToken.token == token)
                    .first()
                )
                if refresh_token:
                    refresh_token.revoked = True  # type: ignore
                    revoked = True
                    logger.info(f"Revoked refresh token: {token[:20]}...")

            if revoked:
                session.commit()
            else:
                logger.warning(f"Token not found for revocation: {token[:20]}...")

    def get_protected_resource_metadata(self) -> dict[str, Any]:
        """Return metadata about the protected resource."""
        return {
            "resource_server": settings.SERVER_URL,
            "scopes_supported": ALLOWED_SCOPES,
            "bearer_methods_supported": ["header"],
            "resource_documentation": f"{settings.SERVER_URL}/docs",
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_basic"],
            "refresh_token_rotation_enabled": True,
            "protected_resources": [
                {
                    "resource_uri": f"{settings.SERVER_URL}/mcp",
                    "scopes": RW_SCOPES,
                    "http_methods": ["POST", "GET"],
                },
                {
                    "resource_uri": f"{settings.SERVER_URL}/mcp/",
                    "scopes": RW_SCOPES,
                    "http_methods": ["POST", "GET"],
                },
            ],
        }
