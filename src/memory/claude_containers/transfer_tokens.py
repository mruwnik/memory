"""Short-lived HMAC-signed tokens for cloud-claude file transfer URLs.

Tokens are stateless (no DB row per token) and bound to a specific transfer
operation: a (user, session, path, action) tuple plus an expiry. Used in
URLs that bypass MCP transport so curl can stream arbitrary file/folder
sizes.

Format:  v1.<base64url(payload_json)>.<base64url(hmac_sha256(payload, secret))>
"""

from dataclasses import dataclass, asdict
from typing import Any, Literal

from memory.api import signed_tokens
from memory.api.signed_tokens import (
    SignedTokenError,
    SignedTokenExpiredError,
)
from memory.common import settings

VERSION = signed_tokens.VERSION
DEFAULT_TTL_SECONDS = 60

# Domain tag mixed into the HMAC message. Transfer tokens share
# TRANSFER_TOKEN_SECRET and the v1.<seg>.<sig> wire format with ingest tokens,
# so a domain tag makes the two families cryptographically non-interchangeable.
_SIGN_DOMAIN = "transfer.v1"


class TransferTokenError(SignedTokenError):
    """Raised when a transfer token is malformed, tampered, or expired."""


class TransferTokenExpiredError(TransferTokenError, SignedTokenExpiredError):
    """Raised specifically when the token's ``exp`` is in the past.

    Callers (notably ``cloud_claude.verify_transfer_token``) distinguish the
    expired branch via ``isinstance`` rather than string-matching the message,
    so a malformed-token error whose text happens to mention "expired" is not
    misclassified.
    """


@dataclass
class TransferTokenPayload:
    user_id: int
    session_id: str
    path: str
    action: Literal["read", "write"]
    exp: int | None  # unix timestamp; None means use default TTL on mint


def mint_token(
    payload: TransferTokenPayload,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str:
    """Create a signed token for the given transfer payload.

    If ``payload.exp`` is None, set it to ``now + ttl_seconds``.
    """
    try:
        return signed_tokens.sign(
            asdict(payload), domain=_SIGN_DOMAIN, ttl_seconds=ttl_seconds
        )
    except SignedTokenExpiredError as exc:
        raise TransferTokenExpiredError(str(exc)) from exc
    except SignedTokenError as exc:
        raise TransferTokenError(str(exc)) from exc


def verify_token(token: str) -> TransferTokenPayload:
    """Validate a token and return its payload.

    Raises ``TransferTokenExpiredError`` if the token's ``exp`` is in the
    past (callers should distinguish this — it's a normal end-of-life
    case, not tampering). Raises ``TransferTokenError`` on any other
    validation failure.
    """
    try:
        data = signed_tokens.verify(token, domain=_SIGN_DOMAIN)
    except SignedTokenExpiredError as exc:
        raise TransferTokenExpiredError(str(exc)) from exc
    except SignedTokenError as exc:
        raise TransferTokenError(str(exc)) from exc

    # Distinguish "no/garbage exp claim" (malformed payload, possibly
    # tampering) from "exp is an int in the past" (genuinely expired, already
    # surfaced as TransferTokenExpiredError above). Conflating them defeats the
    # purpose of the dedicated subclass and gives an attacker probing token
    # shape the same response as a benign expiry.
    exp = data.get("exp")
    if not isinstance(exp, int):
        raise TransferTokenError("malformed payload")

    try:
        user_id = int(data["user_id"])
        session_id = str(data["session_id"])
        path = str(data["path"])
        action = data["action"]
    except (KeyError, TypeError, ValueError):
        raise TransferTokenError("malformed payload")

    if action not in ("read", "write"):
        raise TransferTokenError("malformed payload")

    return TransferTokenPayload(
        user_id=user_id,
        session_id=session_id,
        path=path,
        action=action,
        exp=exp,
    )


# ---------------------------------------------------------------------------
# Path validation + URL mint helpers (shared between API and MCP layers)
# ---------------------------------------------------------------------------

# Reserved path components that would let `path` segments escape the
# `/containers/{sid}/files/` orchestrator route or wedge unparseable URLs.
_DISALLOWED_PATH_COMPONENTS = frozenset({"", ".", ".."})

# Characters rejected outright at mint time. The path is interpolated into
# the orchestrator URL via ``urllib.parse.quote`` (in ``container_files_url``),
# but defense-in-depth requires rejecting any character that has URL meaning
# OR could survive percent-decoding to bypass segment validation:
#
#   ``\x00 \r \n``  HTTP framing / header splitting
#   ``"``           Breaks ``Content-Disposition`` filename quoting
#   ``? # ;``       URL query / fragment / matrix params — would re-route the
#                   request away from ``/files/{path}`` even after quoting if
#                   any future code path skips quoting
#   ``%``           Could survive percent-decoding (e.g. ``%2e%2e`` -> ``..``)
#                   if a downstream parser RFC-3986-normalizes; the validator
#                   here only sees literal ``..``
#   ``&``           Query separator; reserved for the same reason as ``?``
#   ``\``           Backslash has no role in container POSIX paths and would
#                   confuse Windows-style normalizers if any sit in the chain
#   `` `` (space)   Real container paths can contain spaces, but the
#                   orchestrator URL would need them quoted; reject rather
#                   than risk inconsistent quoting between code paths
_FORBIDDEN_PATH_CHARS = (
    "\x00", "\r", "\n", '"', "?", "#", ";", "%", "&", "\\", " ",
)


def validate_transfer_path(path: str) -> str:
    """Reject paths that could traverse the orchestrator URL space.

    The path eventually gets concatenated into
    ``http://orchestrator/containers/{sid}/files/{path}`` (with the path
    segment percent-encoded by ``container_files_url``). httpx does not
    auto-normalize ``..`` segments per RFC 3986, but reverse proxies in front
    of the orchestrator might. Either way, ``..``/``.``/empty-segment paths
    are not legitimate user input — reject early.

    The path is interpolated into the orchestrator URL via ``urlencode``,
    but defense-in-depth requires rejecting any character that has URL
    meaning OR could survive percent-decoding to bypass segment validation
    (see ``_FORBIDDEN_PATH_CHARS`` for the full list and rationale).
    """
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")

    for ch in _FORBIDDEN_PATH_CHARS:
        if ch in path:
            raise ValueError("path contains forbidden characters")

    # Tolerate a leading slash; split on '/' and check each segment.
    parts = path.strip("/").split("/")
    for part in parts:
        if part in _DISALLOWED_PATH_COMPONENTS:
            raise ValueError(f"invalid path segment: {part!r}")

    return path


def normalize_abs_path(path: str) -> str:
    """Routes pass `path:path` without a leading slash; restore it."""
    return "/" + path.lstrip("/")


def mint_transfer_url(
    *,
    base_url: str,
    user_id: int,
    session_id: str,
    path: str,
    action: Literal["read", "write"],
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    """Build a presigned URL for a transfer operation.

    ``base_url`` is the public origin for the Memory API (e.g. derived from
    the request's ``X-Forwarded-Host`` on the API side, or
    ``settings.SERVER_URL`` on the MCP side). Centralizing the URL shape
    here keeps the two call sites from drifting.

    The path is validated and normalized to an absolute path before being
    embedded in the token.
    """
    validate_transfer_path(path)
    abs_path = normalize_abs_path(path)
    ttl = ttl_seconds if ttl_seconds is not None else settings.TRANSFER_TOKEN_TTL_SECONDS

    payload = TransferTokenPayload(
        user_id=user_id,
        session_id=session_id,
        path=abs_path,
        action=action,
        exp=None,
    )
    token = mint_token(payload, ttl_seconds=ttl)
    base = base_url.rstrip("/")
    if action == "read":
        return {
            "url": f"{base}/claude/transfer/pull?token={token}",
            "expires_in": ttl,
        }
    return {
        "url": f"{base}/claude/transfer/push",
        "token": token,
        "expires_in": ttl,
    }
