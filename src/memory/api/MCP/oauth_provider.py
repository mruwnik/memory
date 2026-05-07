import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, cast
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from fastmcp.server.auth import OAuthProvider
from fastmcp.server.auth.auth import AccessToken as FastMCPAccessToken  # type: ignore[reportPrivateImportUsage]
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
)
from mcp.server.auth.settings import ClientRegistrationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from memory.common import settings
from sqlalchemy import update
from sqlalchemy.orm import Session

from memory.common.db.connection import make_session
from memory.common.db.models.users import (
    OAuthClientInformation,
    OAuthRefreshToken,
    OAuthState,
    User,
    UserSession,
)
from memory.common.db.models.users import (
    OAuthToken as TokenBase,
)
from memory.api.auth import lookup_api_key, handle_api_key_use
from memory.common.scopes import SCOPE_CLAUDE_AI, SCOPE_READ, SCOPE_WRITE

logger = logging.getLogger(__name__)


def token_id(token: str) -> str:
    """Create a safe identifier for logging tokens without exposing them.

    Returns first 8 chars of SHA256 hash - enough for correlation, not enough for brute-force.
    """
    return hashlib.sha256(token.encode()).hexdigest()[:8]


def redirect_uri_origin(uri: str) -> tuple[str, str, int | None]:
    """Return the (scheme, host, port) origin of a redirect URI.

    Used to compare allowlist entries to client-supplied redirect_uris
    by *origin*, not by string prefix.  String-prefix matching is unsafe:
    ``http://localhost.evil.com`` starts with ``http://localhost``, so a
    naive ``startswith`` allowlist would let an attacker register an
    arbitrary host.
    """
    parsed = urlparse(uri)
    return (parsed.scheme, parsed.hostname or "", parsed.port)

ALLOWED_SCOPES = [SCOPE_READ, SCOPE_WRITE, SCOPE_CLAUDE_AI]
BASE_SCOPES = [SCOPE_READ]
RW_SCOPES = [SCOPE_READ, SCOPE_WRITE]


# Token configuration constants
ACCESS_TOKEN_LIFETIME = 3600 * 30 * 24  # 30 days
REFRESH_TOKEN_LIFETIME = 30 * 24 * 3600  # 30 days


def now_naive_utc() -> datetime:
    """Return ``datetime.now(timezone.utc)`` stripped of tzinfo.

    UserSession/OAuth* tables store ``expires_at`` as naive UTC; this helper
    keeps the conversion in one place so a future ``datetime.now()`` slip
    (which would silently use *local* time) can't drift the bookkeeping.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_expiration(lifetime_seconds: int) -> datetime:
    """Create a UTC-aware expiration datetime from a lifetime in seconds.

    Returns a naive UTC datetime (tzinfo=None) for consistency with how
    expires_at is stored in the database. Using UTC ensures correct behaviour
    on servers whose local timezone is not UTC.
    """
    return (datetime.now(timezone.utc) + timedelta(seconds=lifetime_seconds)).replace(
        tzinfo=None
    )


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


def resolve_session_scopes(user_session: UserSession) -> tuple[str, list[str]]:
    """Return ``(client_id, scopes)`` for a UserSession.

    When the session has no associated OAuthState (frontend logins, scheduled-
    task helper sessions, etc.), fall back to ``"frontend"`` as the client_id
    and the user's own scopes.  This is the *intentional* fallback for
    password-login sessions; if a future code path creates a UserSession with
    ``oauth_state_id=None`` that should NOT receive the user's full scopes,
    the right fix is to add an explicit per-session scope column rather than
    rely on this implicit grant.
    """
    oauth_state = user_session.oauth_state
    if oauth_state is not None:
        return cast(str, oauth_state.client_id), list(cast(list[str], oauth_state.scopes) or [])
    user_scopes = user_session.user.scopes if user_session.user else []
    return "frontend", list(user_scopes or [])


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
    """Validate a refresh token, raising ValueError if invalid.

    Reuses memory.api.auth.is_expired so a tz-aware non-UTC expires_at
    (which can happen with some Postgres drivers / pool configs) is
    properly converted instead of silently relabeled.
    """
    from memory.api.auth import is_expired

    if is_expired(db_refresh_token.expires_at):
        logger.error(f"Refresh token expired: id={token_id(db_refresh_token.token)}")
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


def make_token_from_data(
    db: Session,
    oauth_state_id: int | None,
    user_id: int,
    client_id: str,
    scopes: list[str],
) -> OAuthToken:
    """Create OAuth token from extracted data (after auth code deletion)."""
    new_session = UserSession(
        user_id=user_id,
        oauth_state_id=oauth_state_id,
        expires_at=create_expiration(ACCESS_TOKEN_LIFETIME),
    )

    # Create refresh token
    refresh_token = create_refresh_token_record(
        client_id,
        user_id,
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


def make_token(
    db: Session,
    auth_state: TokenBase,
    scopes: list[str],
) -> OAuthToken:
    """Create OAuth token from auth state object (for refresh token flow)."""
    # Only set oauth_state_id if this is an OAuthState (not a refresh token)
    oauth_state_id = auth_state.id if isinstance(auth_state, OAuthState) else None
    return make_token_from_data(
        db,
        oauth_state_id=oauth_state_id,
        user_id=cast(int, auth_state.user_id),
        client_id=cast(str, auth_state.client_id),
        scopes=scopes,
    )


class SimpleOAuthProvider(OAuthProvider):
    """OAuth provider that extends fastmcp's OAuthProvider with custom login flow."""

    def __init__(self):
        super().__init__(
            base_url=settings.SERVER_URL,
            issuer_url=settings.SERVER_URL,
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=ALLOWED_SCOPES,
                default_scopes=BASE_SCOPES,
            ),
            required_scopes=BASE_SCOPES,
        )

    async def verify_token(self, token: str) -> FastMCPAccessToken | None:
        """Verify an access token and return token info if valid."""
        with make_session() as session:
            # Try as OAuth access token first
            user_session = session.get(UserSession, token)
            if user_session:
                now = now_naive_utc()
                if user_session.expires_at < now:
                    return None

                client_id, base_scopes = resolve_session_scopes(user_session)
                # Always include SCOPE_READ/SCOPE_WRITE for FastMCP endpoint auth
                scopes: list[str] = sorted(set(base_scopes) | {SCOPE_READ, SCOPE_WRITE})

                # Tokens themselves stay out of the log; correlate via the
                # SHA-prefix id so a leaked log can't be replayed.
                logger.info(
                    f"verify_token: token_id={token_id(token)}, "
                    f"user={user_session.user_id}, scopes={scopes}, client={client_id}"
                )
                return FastMCPAccessToken(
                    token=token,
                    client_id=client_id,
                    scopes=scopes or [SCOPE_READ],
                )

            # Try as API key (bot or user)
            api_key_record = lookup_api_key(token, session)
            if api_key_record and api_key_record.is_valid():
                user = api_key_record.user
                logger.info(
                    f"User {user.name} (id={user.id}) authenticated via API key"
                )
                # Use API key scopes if set, otherwise fall back to user scopes
                scopes = api_key_record.scopes or list(user.scopes or []) or [SCOPE_READ]
                # Handle API key usage (update last_used_at, delete one-time keys)
                handle_api_key_use(api_key_record, session)
                return FastMCPAccessToken(
                    token=token,
                    client_id=cast(str, user.name or user.email),
                    scopes=scopes,
                )

            return None

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        """Get OAuth client information."""
        with make_session() as session:
            client = session.get(OAuthClientInformation, client_id)
            return client and OAuthClientInformationFull(**client.serialize())

    async def register_client(self, client_info: OAuthClientInformationFull):
        """Register a new OAuth client.

        Validates that every redirect_uri's *origin* (scheme + host + port)
        matches one in the allowlist exactly, to prevent authorization-code
        phishing via open dynamic client registration.  A previous version
        used ``str.startswith`` which let an attacker register
        ``http://localhost.evil.com/cb`` (it starts with ``http://localhost``)
        and harvest auth codes; comparing parsed origins closes that hole.

        Configure OAUTH_REDIRECT_URI_ALLOWLIST (comma-separated URIs) to
        expand the default localhost-only allowlist.  ``*`` disables the
        check entirely (logged at startup-time so leaks are visible).
        """
        allowlist = settings.OAUTH_REDIRECT_URI_ALLOWLIST
        if allowlist == ["*"]:
            logger.warning(
                "OAUTH_REDIRECT_URI_ALLOWLIST=['*'] — dynamic client "
                "registration accepts any redirect_uri. This is unsafe in "
                "production."
            )
        else:
            allowed_origins = {redirect_uri_origin(p) for p in allowlist}
            for uri in client_info.redirect_uris:
                uri_str = str(uri)
                if redirect_uri_origin(uri_str) not in allowed_origins:
                    logger.warning(
                        "Rejected OAuth client registration: redirect_uri %r "
                        "origin not in allowlist %r",
                        uri_str,
                        allowlist,
                    )
                    raise ValueError(
                        f"redirect_uri {uri_str!r} is not permitted. "
                        "Set OAUTH_REDIRECT_URI_ALLOWLIST to allow additional URIs."
                    )

        with make_session() as session:
            client = session.get(OAuthClientInformation, client_info.client_id)
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
                expires_at=create_expiration(600),  # 10 min expiry
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

            # Check if state has expired (compare naive UTC datetimes)
            now = now_naive_utc()
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
        # Log a SHA-prefix correlation id, never the raw code. Auth codes
        # are short-lived single-use credentials; whoever can grep the logs
        # can replay them otherwise.
        logger.info(f"Loading authorization code: id={token_id(authorization_code)}")
        with make_session() as session:
            auth_code = (
                session.query(OAuthState)
                .filter(OAuthState.code == authorization_code)
                .first()
            )
            if not auth_code:
                logger.error(
                    f"Invalid authorization code: id={token_id(authorization_code)}"
                )
                raise ValueError("Invalid authorization code")

            # RFC 6749 §4.1.3: verify the code was issued to THIS client
            if auth_code.client_id != client.client_id:
                logger.warning(
                    "Authorization code id=%s requested by client %r but issued to %r",
                    token_id(authorization_code),
                    client.client_id,
                    auth_code.client_id,
                )
                raise ValueError("Authorization code not issued to this client")

            # RFC 6749 §4.1.2: auth codes MUST be short-lived ("a maximum
            # authorization code lifetime of 10 minutes is RECOMMENDED").
            # We pin to the same expires_at the row was issued with so a code
            # someone scraped from referer/logs/etc. can't outlive the login
            # window indefinitely.
            if auth_code.expires_at < now_naive_utc():
                logger.warning(
                    "Authorization code id=%s expired", token_id(authorization_code)
                )
                raise ValueError("Authorization code expired")

            return AuthorizationCode(**auth_code.serialize(code=True))

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        """Exchange authorization code for tokens."""
        # Same redaction as load_authorization_code — never log the raw code.
        logger.info(
            f"Exchanging authorization code: id={token_id(authorization_code.code)}"
        )
        with make_session() as session:
            auth_code = (
                session.query(OAuthState)
                .filter(OAuthState.code == authorization_code.code)
                .first()
            )

            if not auth_code:
                logger.error(
                    f"Invalid authorization code: id={token_id(authorization_code.code)}"
                )
                raise ValueError("Invalid authorization code")

            # RFC 6749 §4.1.3: verify the code was issued to THIS client
            if auth_code.client_id != client.client_id:
                logger.warning(
                    "Authorization code id=%s exchange attempted by client %r but issued to %r",
                    token_id(authorization_code.code),
                    client.client_id,
                    auth_code.client_id,
                )
                raise ValueError("Authorization code not issued to this client")

            if not auth_code.user:
                logger.error(
                    f"No user found for auth code: id={token_id(authorization_code.code)}"
                )
                raise ValueError("Invalid authorization code")

            # RFC 6749 §4.1.2: enforce short lifetime on the exchange path
            # too — load_authorization_code already checks, but we re-check
            # here so callers that skip the load step still get the guarantee.
            if auth_code.expires_at < now_naive_utc():
                logger.warning(
                    "Authorization code id=%s expired",
                    token_id(authorization_code.code),
                )
                raise ValueError("Authorization code expired")

            # Extract data needed for token creation
            auth_code_id = auth_code.id
            user_id = auth_code.user_id
            client_id = auth_code.client_id
            scopes = authorization_code.scopes

            # Atomically invalidate: UPDATE … WHERE code=:code RETURNING id.
            # If two requests race, only one's WHERE clause matches (the other
            # already saw NULL). We keep the OAuthState row because
            # UserSession references it for client_id/scopes lookup in
            # load_access_token() — clearing the code is enough to prevent
            # replay. (CWE-367 single-use enforcement.)
            consumed = session.execute(
                update(OAuthState)
                .where(
                    OAuthState.id == auth_code_id,
                    OAuthState.code == authorization_code.code,
                )
                .values(code=None)
                .returning(OAuthState.id)
            ).scalar_one_or_none()
            if consumed is None:
                logger.warning(
                    "Authorization code id=%s already consumed (race)",
                    token_id(authorization_code.code),
                )
                raise ValueError("Authorization code already used")
            session.commit()

            # Create token linked to the (now code-less) OAuthState
            token = make_token_from_data(
                session,
                oauth_state_id=auth_code_id,
                user_id=user_id,  # type: ignore[arg-type]
                client_id=client_id,
                scopes=scopes,
            )
            logger.info(f"Exchanged authorization code for user {user_id}")

            return token

    async def load_access_token(self, token: str) -> Optional[AccessToken]:
        """Load and validate an access token (or bot API key)."""
        with make_session() as session:
            # Try as OAuth access token first
            user_session = session.get(UserSession, token)
            if user_session:
                now = now_naive_utc()  # Make naive for DB comparison

                if user_session.expires_at < now:
                    return None

                client_id, scopes = resolve_session_scopes(user_session)
                return AccessToken(
                    token=token,
                    client_id=client_id,
                    scopes=scopes,
                    expires_at=int(user_session.expires_at.timestamp()),
                )

            # Try as API key (bot or user)
            api_key_record = lookup_api_key(token, session)
            if api_key_record and api_key_record.is_valid():
                user = api_key_record.user
                logger.info(
                    f"User {user.name} (id={user.id}) authenticated via API key"
                )
                # Use API key scopes if set, otherwise fall back to user scopes
                scopes = api_key_record.scopes or list(user.scopes or []) or [SCOPE_READ]
                # Handle API key usage (update last_used_at, delete one-time keys)
                handle_api_key_use(api_key_record, session)
                return AccessToken(
                    token=token,
                    client_id=cast(str, user.name or user.email),
                    scopes=scopes,
                    expires_at=2147483647,  # Far future (2038)
                )

            return None

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> Optional[RefreshToken]:
        """Load and validate a refresh token."""
        with make_session() as session:
            now = now_naive_utc()

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
                    f"Invalid or expired refresh token: {token_id(refresh_token)}"
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
                logger.error(f"Refresh token not found: {token_id(refresh_token.token)}")
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

    async def revoke_token(  # type: ignore[override]
        self, token: str, token_type_hint: Optional[str] = None
    ) -> None:
        """Revoke a token (access token or refresh token)."""
        with make_session() as session:
            revoked = False

            # Try to revoke as access token (UserSession)
            if not token_type_hint or token_type_hint == "access_token":
                user_session = session.get(UserSession, token)
                if user_session:
                    session.delete(user_session)
                    revoked = True
                    logger.info(f"Revoked access token: id={token_id(token)}")

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
                    logger.info(f"Revoked refresh token: id={token_id(token)}")

            if revoked:
                session.commit()
            else:
                logger.warning(f"Token not found for revocation: id={token_id(token)}")

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
