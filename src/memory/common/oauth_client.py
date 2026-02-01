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

from sqlalchemy.orm import Session

from memory.common import settings
from memory.common.db.models import OAuthClientState

logger = logging.getLogger(__name__)


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
        f"state={state[:16]}..., expires_at={expires_at.isoformat()}"
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

    Args:
        db: Database session
        signed_state: State in "state.signature" format from callback
        provider: Expected OAuth provider

    Returns:
        User ID if state is valid and not expired, None otherwise
    """
    logger.info(
        f"Validating OAuth state: provider={provider}, "
        f"signed_state={signed_state[:20]}... (len={len(signed_state)})"
    )

    if "." not in signed_state:
        logger.warning(f"Invalid state format - no dot separator: {signed_state[:30]}...")
        return None

    original_state = signed_state.rsplit(".", 1)[0]
    logger.info(f"Extracted original state: {original_state[:16]}...")

    # Look up state in database
    oauth_state = db.query(OAuthClientState).filter(
        OAuthClientState.state == original_state,
        OAuthClientState.provider == provider,
    ).first()

    if not oauth_state:
        # Log all states for this provider to help debug
        all_states = db.query(OAuthClientState).filter(
            OAuthClientState.provider == provider
        ).all()
        logger.warning(
            f"State not found in database: state={original_state[:16]}..., provider={provider}. "
            f"Existing states for provider: {[s.state[:16] + '...' for s in all_states]}"
        )
        return None

    user_id = oauth_state.user_id
    now = datetime.now(timezone.utc)
    logger.info(
        f"Found state in database: id={oauth_state.id}, user_id={user_id}, "
        f"expires_at={oauth_state.expires_at.isoformat()}, now={now.isoformat()}"
    )

    # Check expiration
    if oauth_state.expires_at < now:
        logger.warning(
            f"State expired: expires_at={oauth_state.expires_at.isoformat()}, "
            f"now={now.isoformat()}, diff={(now - oauth_state.expires_at).total_seconds()}s"
        )
        db.delete(oauth_state)
        db.commit()
        return None

    # Verify signature matches this user
    verified_state = verify_state_signature(signed_state, user_id)
    if verified_state != original_state:
        logger.warning(
            f"Signature verification failed: user_id={user_id}, "
            f"verified_state={verified_state[:16] + '...' if verified_state else 'None'}, "
            f"expected={original_state[:16]}..."
        )
        db.delete(oauth_state)
        db.commit()
        return None

    # Delete state (one-time use)
    db.delete(oauth_state)
    db.commit()
    logger.info(f"State validated and consumed successfully: user_id={user_id}")

    return user_id
