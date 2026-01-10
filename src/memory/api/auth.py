import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import scoped_session
from starlette.middleware.base import BaseHTTPMiddleware

from memory.common import settings
from memory.common.db.connection import get_session, make_session
from memory.common.db.models import (
    BotUser,
    MCPServer,
    HumanUser,
    User,
    UserSession,
)
from memory.common.mcp import mcp_tools_list
from memory.common.oauth import complete_oauth_flow

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
    request: Request, db: DBSession | scoped_session
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


def authenticate_bot(api_key: str, db: DBSession | scoped_session) -> BotUser | None:
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


def get_session_user(request: Request, db: DBSession | scoped_session) -> User | None:
    """Get user from session ID or API key if valid.

    Supports two authentication methods:
    - Session tokens (UUIDs from UserSession table)
    - API keys (prefixed with 'bot_' for BotUser accounts)
    """
    token = get_token(request)
    if not token:
        return None

    # Check if this is an API key (for bot users)
    if token.startswith("bot_"):
        return authenticate_bot(token, db)

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


from typing import TypeVar
from memory.common.db.models.base import Base

T = TypeVar("T", bound=Base)


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
