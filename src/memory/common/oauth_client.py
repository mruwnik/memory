"""Generic OAuth2 client utilities for CSRF state management.

This module provides reusable functions for OAuth2 client flows:
- State generation with HMAC signatures for CSRF protection
- Database-backed state storage and validation

Provider-specific logic (authorization URLs, token exchange, response parsing)
remains in the respective API modules (slack.py, google_drive.py, etc.).
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from memory.common import settings
from memory.common.db.models import OAuthClientState


def generate_state() -> str:
    """Generate a cryptographically secure random state string."""
    return secrets.token_urlsafe(32)


def sign_state(state: str, user_id: int) -> str:
    """Sign a state string with HMAC using user_id for binding.

    Returns: "state.signature" format
    """
    secret = settings.SECRETS_ENCRYPTION_KEY.encode() if settings.SECRETS_ENCRYPTION_KEY else b"default-secret"
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
    oauth_state = OAuthClientState(
        state=state,
        provider=provider,
        user_id=user_id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=expires_minutes),
    )
    db.add(oauth_state)
    db.commit()
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
    if "." not in signed_state:
        return None

    original_state = signed_state.rsplit(".", 1)[0]

    # Look up state in database
    oauth_state = db.query(OAuthClientState).filter(
        OAuthClientState.state == original_state,
        OAuthClientState.provider == provider,
    ).first()

    if not oauth_state:
        return None

    user_id = oauth_state.user_id

    # Check expiration
    if oauth_state.expires_at < datetime.now(timezone.utc):
        db.delete(oauth_state)
        db.commit()
        return None

    # Verify signature matches this user
    if verify_state_signature(signed_state, user_id) != original_state:
        db.delete(oauth_state)
        db.commit()
        return None

    # Delete state (one-time use)
    db.delete(oauth_state)
    db.commit()

    return user_id
