"""Self-service password reset by an emailed one-time link."""

import hmac
import logging
from functools import cache
from hashlib import sha256

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from memory.api import signed_tokens
from memory.api.users import revoke_user_credentials, validate_password_strength
from memory.common import settings
from memory.common.access_control import has_admin_scope
from memory.common.celery_app import SEND_NOTIFICATION
from memory.common.celery_app import app as celery_app
from memory.common.db.connection import get_session
from memory.common.db.models import EmailAccount, HumanUser
from memory.common.db.models.users import hash_password
from memory.common.email_sender import PASSWORD_RESET_SUBJECT
from memory.common.rate_limit import check_rate_limit_spec, rate_limit_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/password-reset", tags=["auth"])

TOKEN_DOMAIN = "password-reset"
TOKEN_TTL_SECONDS = 30 * 60
# Per-address, so nobody can flood a victim's inbox with reset mail.
EMAIL_RATE_LIMIT = "3/hour"


class ResetRequest(BaseModel):
    email: str = Field(..., min_length=1, max_length=255)


class ResetConfirm(BaseModel):
    token: str = Field(..., min_length=1, max_length=2048)
    # No min_length: validate_password_strength owns the rule and its message.
    new_password: str = Field(..., max_length=128)


@cache
def warn_non_admin_sender(account_id: int) -> None:
    logger.warning(
        "Password reset disabled: sender email account_id=%s is not owned by an admin", account_id
    )


def sender_account(db: Session) -> EmailAccount | None:
    """The account reset mail goes out from, or None when reset is unavailable."""
    if not settings.PASSWORD_RESET_FROM_EMAIL or not settings.TRANSFER_TOKEN_SECRET:
        return None
    account = (
        db.query(EmailAccount)
        .filter(
            EmailAccount.email_address == settings.PASSWORD_RESET_FROM_EMAIL,
            EmailAccount.send_enabled.is_(True),
        )
        .first()
    )
    if account is None:
        return None
    # Its owner controls the SMTP server and Sent folder, so sees every reset link.
    if not has_admin_scope(account.user):
        warn_non_admin_sender(account.id)
        return None
    return account


def password_fingerprint(user: HumanUser) -> str:
    # Binding the token to the current hash makes it single-use: any password
    # change (this reset, a self-change, an admin reset) re-salts the hash.
    return sha256((user.password_hash or "").encode()).hexdigest()[:16]


def make_reset_token(user: HumanUser) -> str:
    return signed_tokens.sign(
        {"uid": user.id, "pw": password_fingerprint(user)},
        domain=TOKEN_DOMAIN,
        ttl_seconds=TOKEN_TTL_SECONDS,
    )


def user_for_token(db: Session, token: str) -> HumanUser | None:
    try:
        data = signed_tokens.verify(token, domain=TOKEN_DOMAIN)
    except signed_tokens.SignedTokenError:
        return None
    uid, fingerprint = data.get("uid"), data.get("pw")
    if not isinstance(uid, int) or not isinstance(fingerprint, str):
        return None
    # Locked so two confirms racing on one token can't both pass the fingerprint check.
    user = db.query(HumanUser).filter_by(id=uid).with_for_update().first()
    if user is None or user.password_hash is None:
        return None
    if not hmac.compare_digest(password_fingerprint(user), fingerprint):
        return None
    return user


def reset_email_args(account: EmailAccount, user: HumanUser) -> list:
    # The fragment never reaches the server, keeping the token out of access logs and Referer.
    link = f"{settings.SERVER_URL}/ui/reset-password#token={make_reset_token(user)}"
    body = (
        f"Someone asked to reset the password for the Memory account {user.email}.\n\n"
        f"To choose a new password, open this link within {TOKEN_TTL_SECONDS // 60} minutes:\n\n"
        f"{link}\n\n"
        "If that wasn't you, ignore this email and your password stays the same."
    )
    return [
        "email",
        user.email,
        body,
        account.user_id,
        PASSWORD_RESET_SUBJECT,
        {
            "from_address": account.email_address,
            "subject": PASSWORD_RESET_SUBJECT,
            "password_reset": True,
        },
    ]


def require_rate_limit(key: str, spec: str) -> None:
    if not check_rate_limit_spec(key, spec):
        raise HTTPException(status_code=429, detail="Too many attempts. Please wait and try again.")


@router.get("")
def availability(db: Session = Depends(get_session)):
    return {"available": sender_account(db) is not None}


@router.post("/request")
def request_reset(
    data: ResetRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    """Email a reset link. Answers the same whether or not the address has an account."""
    account = sender_account(db)
    if account is None:
        raise HTTPException(status_code=404, detail="Password reset by email is not configured")

    email = data.email.strip()
    require_rate_limit(f"password_reset:ip:{rate_limit_key(request)}", settings.API_RATE_LIMIT_AUTH)
    require_rate_limit(f"password_reset:email:{email.lower()}", EMAIL_RATE_LIMIT)

    user = db.query(HumanUser).filter(HumanUser.email == email).first()
    # Passwordless (API-key-only) users have a constant fingerprint, so a link could be forged.
    if user and user.password_hash is not None:
        # Published after the response, so broker latency and failures don't reveal which
        # addresses exist. Positional args: tracked_task persists only kwargs, keeping the
        # link out of pending_jobs.
        background_tasks.add_task(
            celery_app.send_task, SEND_NOTIFICATION, args=reset_email_args(account, user)
        )
        logger.info("Password reset link queued for user_id=%s", user.id)
    return {"status": "ok"}


@router.post("/confirm")
def confirm_reset(data: ResetConfirm, request: Request, db: Session = Depends(get_session)):
    require_rate_limit(f"password_reset_confirm:ip:{rate_limit_key(request)}", settings.API_RATE_LIMIT_AUTH)

    user = user_for_token(db, data.token)
    if user is None:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired")

    validate_password_strength(data.new_password)
    user.password_hash = hash_password(data.new_password)
    # Whoever forgot the password may have lost a device: end every session, like the admin reset.
    revoke_user_credentials(db, user.id)
    db.commit()
    logger.info("Password reset by emailed link for user_id=%s", user.id)
    return {"status": "password_reset"}
