import logging
import re
from datetime import datetime, timedelta, timezone
from typing import TypeVar, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from memory.common import settings
from memory.common.db.connection import DBSession, get_session, make_session
from memory.common.db.models import (
    APIKey,
    BotUser,
    MCPServer,
    HumanUser,
    User,
    UserSession,
)
from memory.common.db.models.base import Base
from memory.common.mcp import mcp_tools_list
from memory.common.oauth import complete_oauth_flow

T = TypeVar("T", bound=Base)

logger = logging.getLogger(__name__)

# Flag to log DISABLE_AUTH warning only once (not per-request)
_auth_disabled_warning_logged = False

# Create router
router = APIRouter(prefix="/auth", tags=["auth"])

# Endpoints that don't require authentication (prefix matching)
WHITELIST = {
    "/health",
    "/register",
    "/authorize",
    "/token",
    "/mcp",
    "/oauth/",
    "/.well-known/",
    "/ui",
    "/admin/statics/",  # SQLAdmin static resources
    "/google-drive/callback",  # Google OAuth callback
    "/polls/respond",  # Public poll response endpoints
}

# Claude WebSocket session path pattern - more specific than prefix matching
# Session IDs are format "u{user_id}-{hex}" so paths look like /claude/u123-abc...
# Note: Uses case-insensitive hex match to handle any UUID generation method
_CLAUDE_SESSION_PATTERN = re.compile(r"^/claude/u\d+-[a-fA-F0-9]+", re.IGNORECASE)

# Prefixes that identify a token as an API key (vs a session token)
API_KEY_PREFIXES = (
    "user_",  # Legacy prefix for migrated user keys
    "internal_", "discord_", "google_", "github_", "mcp_", "ot_",  # Key type prefixes
)


def get_bearer_token(request: Request) -> str | None:
    """Get bearer token from request"""
    bearer_token = request.headers.get("Authorization", "").split(" ")
    if len(bearer_token) != 2:
        return None
    return bearer_token[1]


def get_token(request: Request) -> str | None:
    """Get token from request"""
    return get_bearer_token(request) or request.cookies.get(
        settings.SESSION_COOKIE_NAME
    )


def create_user_session(
    user_id: int, db: DBSession, valid_for: int = settings.SESSION_VALID_FOR
) -> str:
    """Create a new session for a user"""
    expires_at = datetime.now(timezone.utc) + timedelta(days=valid_for)

    session = UserSession(user_id=user_id, expires_at=expires_at)
    db.add(session)
    db.commit()

    return str(session.id)


def get_user_session(
    request: Request, db: DBSession
) -> UserSession | None:
    """Get session ID from request"""
    session_id = get_token(request)

    if not session_id:
        return None

    session = db.get(UserSession, session_id)
    if not session:
        return None

    now = datetime.now(timezone.utc)
    # Normalize expires_at to UTC for comparison
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        # Assume naive datetimes are UTC (PostgreSQL stores as UTC)
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    else:
        # Convert to UTC if it has a different timezone
        expires_at = expires_at.astimezone(timezone.utc)

    if expires_at < now:
        return None
    return session


def lookup_api_key(api_key: str, db: DBSession) -> APIKey | None:
    """Look up an API key in the database using indexed lookup.

    Uses direct database query with unique index on APIKey.key for O(1) lookup
    instead of loading all keys. This prevents memory exhaustion with many keys.

    Note: This has a minor timing side-channel (attacker could learn if
    a key exists by response time), but the DoS risk from loading all
    keys is more significant for this use case.

    Args:
        api_key: The API key string to look up.
        db: Database session.

    Returns:
        The matching APIKey record, or None if not found.
    """
    return db.query(APIKey).filter(APIKey.key == api_key).first()


def authenticate_bot(api_key: str, db: DBSession) -> BotUser | None:
    """Authenticate a bot by API key.

    This is a convenience wrapper around authenticate_by_api_key() for bot-only auth.

    Args:
        api_key: The API key to authenticate.
        db: Database session.

    Returns:
        The BotUser if authenticated and is a bot, None otherwise.
    """
    user, _ = authenticate_by_api_key(api_key, db)
    if user is not None and user.user_type == "bot":
        return cast(BotUser, user)
    return None


def authenticate_by_api_key(
    api_key: str, db: DBSession, allowed_key_types: list[str] | None = None
) -> tuple[User | None, APIKey | None]:
    """Authenticate any user by API key.

    Uses constant-time comparison to prevent timing attacks.

    Args:
        api_key: The API key to authenticate.
        db: Database session.
        allowed_key_types: Optional list of allowed key types. If None, all types allowed.

    Returns:
        Tuple of (user, api_key_record).
    """
    matched_key = lookup_api_key(api_key, db)
    if matched_key is None or not matched_key.is_valid():
        return None, None

    # Check key type restriction if specified
    if allowed_key_types and matched_key.key_type not in allowed_key_types:
        return None, None

    handle_api_key_use(matched_key, db)
    return matched_key.user, matched_key


def handle_api_key_use(key_record: APIKey, db: DBSession) -> None:
    """Handle API key usage: update last_used_at and delete one-time keys.

    Warning: This function commits the database session. This is intentional
    to ensure one-time keys are deleted before request processing begins,
    preventing replay attacks. For regular keys, this updates the last_used_at
    timestamp immediately.

    For one-time keys specifically, the key is deleted and committed before
    the request completes. If the subsequent request fails, the key is still
    gone - this is by design for security (single use).
    """
    key_record.last_used_at = datetime.now(timezone.utc)
    if key_record.is_one_time:
        db.delete(key_record)
    db.commit()


def get_session_user(
    request: Request, db: DBSession, allowed_key_types: list[str] | None = None
) -> User | None:
    """Get user from session ID or API key if valid.

    Supports two authentication methods:
    - Session tokens (UUIDs from UserSession table)
    - API keys (from api_keys table)

    Args:
        request: The HTTP request.
        db: Database session.
        allowed_key_types: Optional list of allowed API key types. If None, all types allowed.
    """
    token = get_token(request)
    if not token:
        return None

    # Check if this looks like an API key (various prefixes)
    if any(token.startswith(prefix) for prefix in API_KEY_PREFIXES):
        user, _ = authenticate_by_api_key(token, db, allowed_key_types)
        return user

    # Otherwise treat as session token
    if session := get_user_session(request, db):
        return session.user
    return None


def get_current_user(request: Request, db: DBSession = Depends(get_session)) -> User:
    """FastAPI dependency to get current authenticated user"""
    user = get_session_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    return user


def get_user_from_token(
    token: str, db: DBSession, allowed_key_types: list[str] | None = None
) -> User | None:
    """Authenticate user from a token string.

    Supports both session tokens (UUIDs) and API keys.
    Useful for WebSocket authentication where tokens are passed via query params.

    Args:
        token: The token to authenticate.
        db: Database session.
        allowed_key_types: Optional list of allowed API key types. If None, all types allowed.
    """
    if not token:
        return None

    # Check if this looks like an API key
    if any(token.startswith(prefix) for prefix in API_KEY_PREFIXES):
        user, _ = authenticate_by_api_key(token, db, allowed_key_types)
        return user

    # Otherwise treat as session token
    session = db.get(UserSession, token)
    if not session:
        return None

    now = datetime.now(timezone.utc)
    if session.expires_at.replace(tzinfo=timezone.utc) < now:
        return None
    return session.user


def require_scope(scope: str):
    """Dependency that checks if user has required scope.

    Usage:
        @router.get("/admin")
        def admin_endpoint(user: User = require_scope("admin:users")):
            ...
    """

    def checker(user: User = Depends(get_current_user)) -> User:
        user_scopes = user.scopes or []
        if "*" in user_scopes or scope in user_scopes:
            return user
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    return Depends(checker)


def require_key_types(*allowed_types: str):
    """Dependency that restricts authentication to specific API key types.

    By default, all key types are allowed. Use this to restrict endpoints
    to only accept certain key types.

    Usage:
        @router.get("/discord-only")
        def discord_endpoint(user: User = require_key_types("discord", "internal")):
            # Only accepts discord or internal API keys (and session tokens)
            ...

    Args:
        allowed_types: Key types to allow (e.g., "discord", "internal", "mcp").
                      Session tokens (non-API-key auth) are always allowed.
    """
    allowed_list = list(allowed_types)

    def checker(
        request: Request, db: DBSession = Depends(get_session)
    ) -> User:
        user = get_session_user(request, db, allowed_key_types=allowed_list)
        if not user:
            raise HTTPException(
                status_code=401,
                detail=f"Authentication required with key type: {', '.join(allowed_list)}",
            )
        return user

    return Depends(checker)


def get_user_account(db: DBSession, model: type[T], account_id: int, user: User) -> T:
    """Get an account by ID, ensuring it belongs to the user or user is admin.

    Generic helper for verifying ownership of user-scoped resources.
    Returns 404 for both "not found" and "not yours" to avoid leaking info.
    Admins can access any account.

    Args:
        db: Database session
        model: SQLAlchemy model class (must have user_id column)
        account_id: ID of the account to retrieve
        user: Current authenticated user

    Returns:
        The account if found and owned by user (or user is admin)

    Raises:
        HTTPException: 404 if account not found or not owned by user (and not admin)
    """
    account = db.get(model, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.user_id != user.id and not has_admin_scope(user):  # type: ignore[attr-defined]
        raise HTTPException(status_code=404, detail="Account not found")
    return account


def has_admin_scope(user: User) -> bool:
    """Check if user has admin scope (can view all users' data).

    Admin users have either '*' (full access) or 'admin' scope.
    """
    user_scopes = user.scopes or []
    return "*" in user_scopes or "admin" in user_scopes


def resolve_user_filter(
    user_id: int | None, current_user: User, db: DBSession
) -> int | None:
    """Resolve user_id filter for admin queries.

    Used by endpoints that support admin viewing of other users' data.
    Non-admin users always see only their own data regardless of the
    user_id parameter.

    Args:
        user_id: Requested user ID filter (None for all users)
        current_user: The authenticated user making the request
        db: Database session for user validation

    Returns:
        - None if admin requests all users (user_id omitted)
        - Specific user_id if admin requests specific user
        - current_user.id if non-admin (ignores user_id param)

    Raises:
        HTTPException 404 if requested user doesn't exist
    """
    if not has_admin_scope(current_user):
        # Non-admins can only see their own data
        return current_user.id

    if user_id is None:
        # Admin with no filter - return all users
        return None

    # Admin requesting specific user - verify they exist
    target_user = db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    return user_id


def create_user(email: str, password: str, name: str, db: DBSession) -> HumanUser:
    """Create a new human user"""
    # Check if user already exists
    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="User already exists")

    user = HumanUser.create_with_password(email, name, password)
    db.add(user)
    db.commit()
    db.refresh(user)

    return user


def authenticate_user(email: str, password: str, db: DBSession) -> HumanUser | None:
    """Authenticate a human user by email and password.

    Uses constant-time comparison to prevent timing-based user enumeration.
    """
    user = db.query(HumanUser).filter(HumanUser.email == email).first()

    # Always perform password check to prevent timing attacks
    # Even if user doesn't exist, we do a dummy check
    if user:
        if user.is_valid_password(password):
            return user
    else:
        # Dummy password check to prevent timing-based user enumeration
        # This ensures the function takes similar time whether user exists or not
        from memory.common.db.models.users import verify_password
        verify_password(password, "$2b$12$dummy.hash.for.timing.attack.prevention")

    return None


@router.api_route("/logout", methods=["GET", "POST"])
def logout(request: Request, db: DBSession = Depends(get_session)):
    """Logout and clear session"""
    session = get_user_session(request, db)
    if session:
        db.delete(session)
        db.commit()
    return {"message": "Logged out successfully"}


@router.get("/me")
def get_me(user: User = Depends(get_current_user)):
    """Get current user info"""
    return user.serialize()


@router.get("/callback/discord")
async def oauth_callback_discord(request: Request):
    """Get current user info"""
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")

    # Log OAuth callback without sensitive data (code/state could be intercepted)
    logger.info("Received OAuth callback")

    message, title, close, status_code = "", "", "", 200
    if error:
        logger.error(f"OAuth error: {error}")
        message = f"Error: {error}"
        title = "❌ Authorization Failed"
        status_code = 400
    elif not code or not state:
        message = "Missing authorization code or state parameter."
        title = "❌ Invalid Request"
        status_code = 400
    else:
        # Complete the OAuth flow (exchange code for token)
        with make_session() as session:
            mcp_server = (
                session.query(MCPServer).filter(MCPServer.state == state).first()
            )
            if not mcp_server:
                return Response(
                    content="MCP server not found",
                    status_code=404,
                )

            status_code, message = await complete_oauth_flow(mcp_server, code, state)
            session.commit()

            tools = await mcp_tools_list(
                cast(str, mcp_server.mcp_server_url), cast(str, mcp_server.access_token)
            )
            mcp_server.available_tools = [
                name for tool in tools if (name := tool.get("name"))
            ]
            session.commit()
            logger.info(f"MCP server tools: {tools}")

        if 200 <= status_code < 300:
            title = "✅ Authorization Successful!"
            close = "You can close this window and return to the MCP server."
        else:
            title = "❌ Authorization Failed"

    from html import escape
    return Response(
        content=f"""
        <html>
            <body>
                <h1>{escape(title)}</h1>
                <p>{escape(message)}</p>
                <p>{close}</p>
            </body>
        </html>
        """,
        status_code=status_code,
    )


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """Middleware to require authentication for all endpoints except whitelisted ones."""

    async def dispatch(self, request: Request, call_next):
        global _auth_disabled_warning_logged
        if settings.DISABLE_AUTH:
            # Log warning only once to avoid flooding logs during development
            if not _auth_disabled_warning_logged:
                logger.warning(
                    "DISABLE_AUTH is enabled - all endpoints are publicly accessible. "
                    "This should ONLY be used for local development."
                )
                _auth_disabled_warning_logged = True
            return await call_next(request)

        path = request.url.path

        # Skip authentication for whitelisted endpoints
        if (
            any(path.startswith(whitelist_path) for whitelist_path in WHITELIST)
            or path == "/"
            or _CLAUDE_SESSION_PATTERN.match(path)  # Claude WebSocket sessions
        ):
            return await call_next(request)

        # Check for session ID in header or cookie
        session_id = get_token(request)
        if not session_id:
            return Response(
                content="Authentication required",
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Validate session and get user
        with make_session() as session:
            user = get_session_user(request, session)
            if not user:
                return Response(
                    content="Invalid or expired session",
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )

            # Log user ID instead of email for privacy
            logger.debug(f"Authenticated request from user_id={user.id} to {path}")

        return await call_next(request)
