"""Generic OAuth2 client utilities for CSRF state management.

This module provides reusable functions for OAuth2 client flows:
- State generation with HMAC signatures for CSRF protection
- Database-backed state storage and validation

Provider-specific logic (authorization URLs, token exchange, response parsing)
remains in the respective API modules (slack.py, google_drive.py, etc.).
"""

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from memory.common import settings
from memory.common.db.models import OAuthClientState

logger = logging.getLogger(__name__)


def log_corr_id(value: str | None) -> str:
    """Build a stable, non-reversible correlation id for logging secret values.

    Returns the first 8 hex chars of SHA256(value). Two log lines for the
    same secret produce the same correlation id (so traces can be joined
    across components), but the id is computationally infeasible to invert,
    so an operator with log access cannot recover the secret.

    Used for OAuth `state`, signed_state, OAuth `code`, and similar values
    that previously leaked into logs as raw prefixes — see SECURITY/MED
    7c02ac7c (CWE-532). For non-secret values pass them in directly.
    """
    if not value:
        return "<empty>"
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:8]


def generate_state() -> str:
    """Generate a cryptographically secure random state string."""
    return secrets.token_urlsafe(32)


def sign_state(state: str, user_id: int) -> str:
    """Sign a state string with HMAC using user_id for binding.

    Returns: "state.signature" format

    Raises:
        RuntimeError: If SECRETS_ENCRYPTION_KEY is not configured
    """
    if not settings.SECRETS_ENCRYPTION_KEY:
        raise RuntimeError(
            "SECRETS_ENCRYPTION_KEY must be set for OAuth state signing. "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
    secret = settings.SECRETS_ENCRYPTION_KEY.encode()
    message = f"{state}:{user_id}".encode()
    signature = hmac.new(secret, message, hashlib.sha256).hexdigest()[:16]
    return f"{state}.{signature}"


def verify_state_signature(signed_state: str, user_id: int) -> str | None:
    """Verify a signed state and return the original state if valid.

    Args:
        signed_state: State in "state.signature" format
        user_id: User ID that should match the signature

    Returns:
        Original state string if signature is valid, None otherwise
    """
    if "." not in signed_state:
        return None

    state, provided_sig = signed_state.rsplit(".", 1)
    expected_signed = sign_state(state, user_id)
    expected_sig = expected_signed.rsplit(".", 1)[1]

    if hmac.compare_digest(provided_sig, expected_sig):
        return state
    return None


def store_state(
    db: Session,
    state: str,
    provider: str,
    user_id: int,
    expires_minutes: int = 10,
) -> OAuthClientState:
    """Store OAuth state in database for CSRF validation.

    Args:
        db: Database session
        state: The state string (unsigned)
        provider: OAuth provider name (e.g., "slack", "google")
        user_id: User initiating the OAuth flow
        expires_minutes: State validity period (default 10 minutes)

    Returns:
        Created OAuthClientState record
    """
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    logger.info(
        f"Storing OAuth state: provider={provider}, user_id={user_id}, "
        f"state_corr={log_corr_id(state)}, expires_at={expires_at.isoformat()}"
    )
    oauth_state = OAuthClientState(
        state=state,
        provider=provider,
        user_id=user_id,
        expires_at=expires_at,
    )
    db.add(oauth_state)
    db.commit()
    logger.info(f"OAuth state stored successfully: id={oauth_state.id}")
    return oauth_state


def validate_and_consume_state(
    db: Session,
    signed_state: str,
    provider: str,
) -> int | None:
    """Validate OAuth state from callback and consume it (one-time use).

    The consume step is atomic: a single ``DELETE ... RETURNING`` claims the
    row, so two concurrent callbacks for the same ``signed_state`` cannot
    both succeed (one will see no row returned). The previous SELECT-then-
    DELETE form left a race window covering signature/expiry checks where
    both callers passed and both completed the OAuth flow.

    Args:
        db: Database session
        signed_state: State in "state.signature" format from callback
        provider: Expected OAuth provider

    Returns:
        User ID if state is valid and not expired, None otherwise
    """
    logger.info(
        f"Validating OAuth state: provider={provider}, "
        f"signed_state_corr={log_corr_id(signed_state)}, len={len(signed_state)}"
    )

    if "." not in signed_state:
        logger.warning(
            f"Invalid state format - no dot separator: "
            f"signed_state_corr={log_corr_id(signed_state)}"
        )
        return None

    original_state = signed_state.rsplit(".", 1)[0]

    # Atomically claim the row — only the race-winner gets a result.
    # The signature and expiry checks below run on the *returned* row, so
    # an invalid state still gets consumed (preventing brute-force attempts
    # against a single token, which mirrors the previous behaviour where
    # bad-signature / expired states were also deleted before returning).
    result = db.execute(
        delete(OAuthClientState)
        .where(OAuthClientState.state == original_state)
        .where(OAuthClientState.provider == provider)
        .returning(
            OAuthClientState.id,
            OAuthClientState.user_id,
            OAuthClientState.expires_at,
        )
    ).first()
    db.commit()

    if result is None:
        # Don't enumerate other in-flight state values — that's an oracle
        # for an attacker who can read logs (CWE-532). Log only the
        # correlation id of the missing value plus a count of pending
        # states for the provider, useful for ops without leaking secrets.
        # Note: the row may be missing because (a) it never existed, (b)
        # it expired and was reaped, or (c) a concurrent callback already
        # consumed it — all three are caller-indistinguishable by design.
        active_count = db.query(OAuthClientState).filter(
            OAuthClientState.provider == provider
        ).count()
        logger.warning(
            f"State not found in database: state_corr={log_corr_id(original_state)}, "
            f"provider={provider}, active_states_for_provider={active_count}"
        )
        return None

    state_id, user_id, expires_at = result
    now = datetime.now(timezone.utc)
    logger.info(
        f"Consumed state from database: id={state_id}, user_id={user_id}, "
        f"expires_at={expires_at.isoformat()}, now={now.isoformat()}"
    )

    # Check expiration
    if expires_at < now:
        logger.warning(
            f"State expired: expires_at={expires_at.isoformat()}, "
            f"now={now.isoformat()}, diff={(now - expires_at).total_seconds()}s"
        )
        return None

    # Verify signature matches this user
    verified_state = verify_state_signature(signed_state, user_id)
    if verified_state != original_state:
        logger.warning(
            f"Signature verification failed: user_id={user_id}, "
            f"verified_corr={log_corr_id(verified_state)}, "
            f"expected_corr={log_corr_id(original_state)}"
        )
        return None

    logger.info(f"State validated and consumed successfully: user_id={user_id}")

    return user_id
