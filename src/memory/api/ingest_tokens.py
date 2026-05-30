"""Stateless HMAC tokens carrying a pending add_content ingestion intent.

Shares the wire format and signing core (``signed_tokens``) with cloud-claude
transfer tokens, but the payload is the ingest intent
(type/filename/tags/metadata/project) so the /ingest/upload endpoint can land
bytes and dispatch the right task with no server-side pending row.

Format: v1.<b64u(payload_json)>.<b64u(hmac_sha256(domain-tagged payload, secret))>
"""

from dataclasses import dataclass, asdict, field
from typing import Any

from memory.api import signed_tokens
from memory.api.signed_tokens import (
    SignedTokenError,
    SignedTokenExpiredError,
)
from memory.common import settings

VERSION = signed_tokens.VERSION

# Domain separation tag mixed into the signed message. Ingest tokens share
# TRANSFER_TOKEN_SECRET and the v1.<seg>.<sig> wire format with cloud-claude
# transfer tokens, so without a context tag a signature minted for one family
# would validate against the other's verifier. Signing over a tagged message
# makes the two families cryptographically non-interchangeable.
_SIGN_DOMAIN = "ingest.v1"


class IngestTokenError(SignedTokenError):
    """Malformed, tampered, or otherwise invalid ingest token."""


class IngestTokenExpiredError(IngestTokenError, SignedTokenExpiredError):
    """Token's exp is in the past."""


@dataclass
class IngestTokenPayload:
    user_id: int | None
    type: str
    filename: str
    tags: list[str] = field(default_factory=list)
    doc_metadata: dict[str, Any] = field(default_factory=dict)
    project_id: int | None = None
    exp: int | None = None  # unix ts; None -> default TTL at mint


def mint_token(payload: IngestTokenPayload, ttl_seconds: int | None = None) -> str:
    """Create a signed token for the given ingest payload.

    If ``payload.exp`` is None, set it to ``now + ttl_seconds`` (or
    ``settings.INGEST_TOKEN_TTL_SECONDS`` if ttl_seconds is also None).
    """
    ttl = ttl_seconds if ttl_seconds is not None else settings.INGEST_TOKEN_TTL_SECONDS
    try:
        return signed_tokens.sign(asdict(payload), domain=_SIGN_DOMAIN, ttl_seconds=ttl)
    except SignedTokenExpiredError as exc:
        raise IngestTokenExpiredError(str(exc)) from exc
    except SignedTokenError as exc:
        raise IngestTokenError(str(exc)) from exc


def verify_token(token: str) -> IngestTokenPayload:
    """Validate a token and return its payload.

    Raises ``IngestTokenExpiredError`` if the token's ``exp`` is in the past.
    Raises ``IngestTokenError`` on any other validation failure.
    """
    try:
        data = signed_tokens.verify(token, domain=_SIGN_DOMAIN)
    except SignedTokenExpiredError as exc:
        raise IngestTokenExpiredError(str(exc)) from exc
    except SignedTokenError as exc:
        raise IngestTokenError(str(exc)) from exc

    # A correctly-signed payload whose shape doesn't match the ingest schema
    # (missing required fields, or unexpected keys from another token family)
    # must surface as an auth failure, not an unhandled TypeError → 500.
    try:
        return IngestTokenPayload(**data)
    except TypeError:
        raise IngestTokenError("token payload does not match the ingest schema")
