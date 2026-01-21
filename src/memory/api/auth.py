import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import TypeVar, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from memory.common import settings
from memory.common.db.connection import DBSession, get_session, make_session
from memory.common.db.models import (
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


# Create router
router = APIRouter(prefix="/auth", tags=["auth"])

# Endpoints that don't require authentication
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
    # Claude WebSocket log streaming - auth via token query param
    # NOTE: This pattern depends on session_id format from cloud_claude.make_session_id()
    # which generates IDs like "u{user_id}-{hex}". If that format changes, update this.
    "/claude/u",
}


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
    if session.expires_at.replace(tzinfo=timezone.utc) < now:
        return None
    return session


def authenticate_bot(api_key: str, db: DBSession) -> BotUser | None:
    """Authenticate a bot by API key.

    Uses constant-time comparison to prevent timing attacks.
    """
    # Get all bot users and compare with constant-time function
    # This prevents timing attacks on API key discovery
    bots = db.query(BotUser).all()
    for bot in bots:
        if bot.api_key and secrets.compare_digest(bot.api_key, api_key):
            return bot
    return None


def authenticate_by_api_key(api_key: str, db: DBSession) -> User | None:
    """Authenticate any user by API key.

    Supports both bot users (bot_* keys) and human users (user_* keys).
    Uses constant-time comparison to prevent timing attacks.
    """
    # Query all users with API keys and compare with constant-time function
    users = db.query(User).filter(User.api_key.isnot(None)).all()
    for user in users:
        if user.api_key and secrets.compare_digest(user.api_key, api_key):
            return user
    return None


def get_session_user(request: Request, db: DBSession) -> User | None:
    """Get user from session ID or API key if valid.

    Supports two authentication methods:
    - Session tokens (UUIDs from UserSession table)
    - API keys (prefixed with 'bot_' for BotUser accounts)
    """
    token = get_token(request)
    if not token:
        return None

    # Check if this is an API key (for bot or human users with API keys)
    if token.startswith("bot_") or token.startswith("user_"):
        return authenticate_by_api_key(token, db)

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


def get_user_from_token(token: str, db: DBSession) -> User | None:
    """Authenticate user from a token string.

    Supports both session tokens (UUIDs) and API keys.
    Useful for WebSocket authentication where tokens are passed via query params.
    """
    if not token:
        return None

    # Check if this is an API key
    if token.startswith("bot_") or token.startswith("user_"):
        return authenticate_by_api_key(token, db)

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


def get_user_account(db: DBSession, model: type[T], account_id: int, user: User) -> T:
    """Get an account by ID, ensuring it belongs to the user.

    Generic helper for verifying ownership of user-scoped resources.
    Returns 404 for both "not found" and "not yours" to avoid leaking info.

    Args:
        db: Database session
        model: SQLAlchemy model class (must have user_id column)
        account_id: ID of the account to retrieve
        user: Current authenticated user

    Returns:
        The account if found and owned by user

    Raises:
        HTTPException: 404 if account not found or not owned by user
    """
    account = db.get(model, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.user_id != user.id:  # type: ignore[attr-defined]
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

    return Response(
        content=f"""
        <html>
            <body>
                <h1>{title}</h1>
                <p>{message}</p>
                <p>{close}</p>
            </body>
        </html>
        """,
        status_code=status_code,
    )


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """Middleware to require authentication for all endpoints except whitelisted ones."""

    async def dispatch(self, request: Request, call_next):
        if settings.DISABLE_AUTH:
            return await call_next(request)

        path = request.url.path

        # Skip authentication for whitelisted endpoints
        if (
            any(path.startswith(whitelist_path) for whitelist_path in WHITELIST)
            or path == "/"
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
