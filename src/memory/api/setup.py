"""First-run setup: claiming a fresh instance, and what the setup wizard checks."""

import hmac
import logging
import secrets
import threading
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from memory.api.auth import authenticate_request, create_user, create_user_session
from memory.api.password_reset import require_rate_limit
from memory.api.users import link_person_to_user, validate_password_strength
from memory.common import settings
from memory.common.db.connection import get_session, make_session
from memory.common.db.counts import count_rows
from memory.common.db.models import (
    ArticleFeed,
    CalendarAccount,
    EmailAccount,
    GithubAccount,
    GoogleAccount,
    Note,
    OAuthClientInformation,
    OAuthRefreshToken,
    SlackUserCredentials,
    TranscriptAccount,
    User,
)
from memory.common.db.models.discord import DiscordBot
from memory.common.rate_limit import rate_limit_key
from memory.common.scopes import SCOPE_ADMIN

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/setup", tags=["setup"])

# Serializes concurrent claims, so two valid requests can't both create a first account.
SETUP_LOCK_KEY = 0x5E70001

# Held only in this process: a restart, or the account being created, retires it.
_token_lock = threading.Lock()
_setup_token: str | None = None


class AccountCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    # No min_length: validate_password_strength owns the rule and its message.
    password: str = Field(..., max_length=128)
    token: str = Field(..., min_length=1, max_length=256)


def needs_account(db: Session) -> bool:
    return db.query(User.id).filter(User.user_type == "human").first() is None


def setup_token() -> str:
    """The token that claims this instance, logged when first made."""
    global _setup_token
    with _token_lock:
        if _setup_token is None:
            _setup_token = secrets.token_urlsafe(24)
            # The operator reads the token from this line, so it carries the value on purpose.
            logger.warning(
                "No account exists yet. Create it at %s/ui/setup with setup token: %s",
                settings.SERVER_URL,
                _setup_token,
            )
        return _setup_token


def retire_setup_token() -> None:
    global _setup_token
    with _token_lock:
        _setup_token = None


def announce_setup_token() -> None:
    """Log the setup token at startup if the instance is still unclaimed."""
    try:
        with make_session() as db:
            unclaimed = needs_account(db)
    except SQLAlchemyError:
        # The database may not be ready yet; /setup/status logs the token on first visit.
        logger.warning("Could not check for an account at startup")
        return
    if unclaimed:
        setup_token()


def source_counts(db: Session, user_id: int) -> dict[str, int]:
    return {
        "email": count_rows(db, EmailAccount, EmailAccount.user_id == user_id),
        "google": count_rows(db, GoogleAccount, GoogleAccount.user_id == user_id)
        + count_rows(db, CalendarAccount, CalendarAccount.user_id == user_id),
        "github": count_rows(db, GithubAccount, GithubAccount.user_id == user_id),
        "slack": count_rows(db, SlackUserCredentials, SlackUserCredentials.user_id == user_id),
        "discord": count_rows(db, DiscordBot, DiscordBot.created_by_user_id == user_id),
        "feeds": count_rows(db, ArticleFeed, ArticleFeed.user_id == user_id),
        "notes": count_rows(db, Note, Note.creator_id == user_id),
        "transcripts": count_rows(db, TranscriptAccount, TranscriptAccount.user_id == user_id),
    }


def is_web_ui(redirect_uris: list[str]) -> bool:
    # The web UI signs in through the same OAuth flow, redirecting back to /ui.
    return any(urlsplit(uri).path.rstrip("/") == "/ui" for uri in redirect_uris)


def mcp_clients(db: Session, user_id: int) -> list[str]:
    """Names of the OAuth clients holding a live grant for this user, besides the web UI."""
    rows = (
        db.query(OAuthClientInformation.client_name, OAuthClientInformation.redirect_uris)
        .join(OAuthRefreshToken, OAuthRefreshToken.client_id == OAuthClientInformation.client_id)
        .filter(
            OAuthRefreshToken.user_id == user_id,
            OAuthRefreshToken.revoked.is_(False),
            # Stored as naive UTC.
            OAuthRefreshToken.expires_at > datetime.now(timezone.utc).replace(tzinfo=None),
        )
        .distinct()
        .order_by(OAuthClientInformation.client_name)
        .all()
    )
    return [name for name, uris in rows if not is_web_ui(uris)]


@router.get("/status")
def status(request: Request, db: Session = Depends(get_session)) -> dict:
    """Callers without a login session (strangers, API keys) learn only whether
    the instance still needs its first account."""
    unclaimed = needs_account(db)
    if unclaimed:
        setup_token()
    user, key = authenticate_request(request, db)
    if user is None or key is not None:
        return {"needs_account": unclaimed}
    return {
        "needs_account": unclaimed,
        "sources": source_counts(db, user.id),
        "mcp_clients": mcp_clients(db, user.id),
        "mcp_url": f"{settings.SERVER_URL}/mcp",
    }


@router.post("/account")
def create_account(data: AccountCreate, request: Request, db: Session = Depends(get_session)) -> dict:
    """Create the first account, as an admin, and sign it in."""
    require_rate_limit(f"setup_account:ip:{rate_limit_key(request)}", settings.API_RATE_LIMIT_AUTH)

    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SETUP_LOCK_KEY})
    if not needs_account(db):
        raise HTTPException(status_code=404, detail="Not found")
    if not hmac.compare_digest(data.token.encode(), setup_token().encode()):
        raise HTTPException(status_code=403, detail="That setup token is not the one in the server log")
    validate_password_strength(data.password)

    # Admin from its first commit, so a failure after it can't leave a sole non-admin human.
    user = create_user(data.email, data.password, data.name.strip(), db, scopes=[SCOPE_ADMIN])
    link_person_to_user(db, user)
    retire_setup_token()
    logger.warning("Setup created the first account, user_id=%s", user.id)
    return {"session_id": create_user_session(user.id, db)}
