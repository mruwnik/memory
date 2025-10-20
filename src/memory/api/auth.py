from datetime import datetime, timedelta, timezone
import logging

from fastapi import HTTPException, Depends, Request, Response, APIRouter
from starlette.middleware.base import BaseHTTPMiddleware
from memory.common import settings
from sqlalchemy.orm import Session as DBSession, scoped_session

from memory.common.db.connection import get_session, make_session
from memory.common.db.models.users import User, HumanUser, BotUser, UserSession

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

    session = db.query(UserSession).get(session_id)
    if not session:
        return None

    now = datetime.now(timezone.utc)
    if session.expires_at.replace(tzinfo=timezone.utc) < now:
        return None
    return session


def get_session_user(request: Request, db: DBSession | scoped_session) -> User | None:
    """Get user from session ID if session is valid"""
    if session := get_user_session(request, db):
        return session.user
    return None


def get_current_user(request: Request, db: DBSession = Depends(get_session)) -> User:
    """FastAPI dependency to get current authenticated user"""
    user = get_session_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    return user


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
    """Authenticate a human user by email and password"""
    user = db.query(HumanUser).filter(HumanUser.email == email).first()
    if user and user.is_valid_password(password):
        return user
    return None


def authenticate_bot(api_key: str, db: DBSession) -> BotUser | None:
    """Authenticate a bot by API key"""
    return db.query(BotUser).filter(BotUser.api_key == api_key).first()


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

            logger.debug(f"Authenticated request from user {user.email} to {path}")

        return await call_next(request)
