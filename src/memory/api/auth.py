from datetime import datetime, timedelta, timezone
import textwrap
from typing import cast
import logging

from fastapi import HTTPException, Depends, Request, Response, APIRouter, Form
from fastapi.responses import HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware
from memory.common import settings
from sqlalchemy.orm import Session as DBSession, scoped_session
from pydantic import BaseModel

from memory.common.db.connection import get_session, make_session
from memory.common.db.models.users import User, UserSession

logger = logging.getLogger(__name__)


# Pydantic models
class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str


class LoginResponse(BaseModel):
    session_id: str
    user_id: int
    email: str
    name: str


# Create router
router = APIRouter(prefix="/auth", tags=["auth"])


def create_user_session(
    user_id: int, db: DBSession, valid_for: int = settings.SESSION_VALID_FOR
) -> str:
    """Create a new session for a user"""
    expires_at = datetime.now(timezone.utc) + timedelta(days=valid_for)

    session = UserSession(user_id=user_id, expires_at=expires_at)
    db.add(session)
    db.commit()

    return str(session.id)


def get_session_user(session_id: str, db: DBSession | scoped_session) -> User | None:
    """Get user from session ID if session is valid"""
    session = db.query(UserSession).get(session_id)
    if not session:
        return None
    now = datetime.now(timezone.utc)
    if session.expires_at.replace(tzinfo=timezone.utc) > now:
        return session.user
    return None


def get_current_user(request: Request, db: DBSession = Depends(get_session)) -> User:
    """FastAPI dependency to get current authenticated user"""
    # Check for session ID in header or cookie
    session_id = request.headers.get(
        settings.SESSION_HEADER_NAME
    ) or request.cookies.get(settings.SESSION_COOKIE_NAME)

    if not session_id:
        raise HTTPException(status_code=401, detail="No session provided")

    user = get_session_user(session_id, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    return user


def create_user(email: str, password: str, name: str, db: DBSession) -> User:
    """Create a new user"""
    # Check if user already exists
    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="User already exists")

    user = User.create_with_password(email, name, password)
    db.add(user)
    db.commit()
    db.refresh(user)

    return user


def authenticate_user(email: str, password: str, db: DBSession) -> User | None:
    """Authenticate a user by email and password"""
    user = db.query(User).filter(User.email == email).first()
    if user and user.is_valid_password(password):
        return user
    return None


# Auth endpoints
@router.post("/register", response_model=LoginResponse)
def register(request: RegisterRequest, db: DBSession = Depends(get_session)):
    """Register a new user"""
    if not settings.REGISTER_ENABLED:
        raise HTTPException(status_code=403, detail="Registration is disabled")

    user = create_user(request.email, request.password, request.name, db)
    session_id = create_user_session(user.id, db)  # type: ignore

    return LoginResponse(session_id=session_id, **user.serialize())


@router.get("/login", response_model=LoginResponse)
def login_page():
    """Login page"""
    return HTMLResponse(
        content=textwrap.dedent("""
            <html>
                <body>
                    <h1>Login</h1>
                    <form method="post" action="/auth/login-form">
                        <input type="email" name="email" placeholder="Email" />
                        <input type="password" name="password" placeholder="Password" />
                        <button type="submit">Login</button>
                    </form>
                </body>
            </html>
    """),
    )


@router.post("/login", response_model=LoginResponse)
def login(
    request: LoginRequest, response: Response, db: DBSession = Depends(get_session)
):
    """Login and create a session"""
    return login_form(response, db, request.email, request.password)


@router.post("/login-form", response_model=LoginResponse)
def login_form(
    response: Response,
    db: DBSession = Depends(get_session),
    email: str = Form(),
    password: str = Form(),
):
    """Login with form data and create a session"""
    user = authenticate_user(email, password, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    session_id = create_user_session(cast(int, user.id), db)

    # Set session cookie
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        secure=settings.HTTPS,
        samesite="lax",
        max_age=settings.SESSION_COOKIE_MAX_AGE,
    )

    return LoginResponse(session_id=session_id, **user.serialize())


@router.post("/logout")
def logout(response: Response, user: User = Depends(get_current_user)):
    """Logout and clear session"""
    response.delete_cookie(settings.SESSION_COOKIE_NAME)
    return {"message": "Logged out successfully"}


@router.get("/me")
def get_me(user: User = Depends(get_current_user)):
    """Get current user info"""
    return user.serialize()


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """Middleware to require authentication for all endpoints except whitelisted ones."""

    # Endpoints that don't require authentication
    WHITELIST = {
        "/health",
        "/auth/login",
        "/auth/login-form",
        "/auth/register",
    }

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip authentication for whitelisted endpoints
        if any(path.startswith(whitelist_path) for whitelist_path in self.WHITELIST):
            return await call_next(request)

        # Check for session ID in header or cookie
        session_id = request.headers.get(
            settings.SESSION_HEADER_NAME
        ) or request.cookies.get(settings.SESSION_COOKIE_NAME)

        if not session_id:
            return Response(
                content="Authentication required",
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Validate session and get user
        with make_session() as session:
            user = get_session_user(session_id, session)
            if not user:
                return Response(
                    content="Invalid or expired session",
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )

            logger.debug(f"Authenticated request from user {user.email} to {path}")

        return await call_next(request)
