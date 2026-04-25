"""Short-lived HMAC-signed tokens for cloud-claude file transfer URLs.

Tokens are stateless (no DB row per token) and bound to a specific transfer
operation: a (user, session, path, action) tuple plus an expiry. Used in
URLs that bypass MCP transport so curl can stream arbitrary file/folder
sizes.

Format:  v1.<base64url(payload_json)>.<base64url(hmac_sha256(payload, secret))>
"""

import base64
import hmac
import json
import time
from dataclasses import dataclass, asdict
from hashlib import sha256
from typing import Any, Literal

from memory.common import settings

VERSION = "v1"
DEFAULT_TTL_SECONDS = 60


class TransferTokenError(Exception):
    """Raised when a transfer token is malformed, tampered, or expired."""


@dataclass
class TransferTokenPayload:
    user_id: int
    session_id: str
    path: str
    action: Literal["read", "write"]
    exp: int | None  # unix timestamp; None means use default TTL on mint


def _b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sign(payload_segment: str, secret: str) -> str:
    sig = hmac.new(
        secret.encode("utf-8"),
        payload_segment.encode("ascii"),
        sha256,
    ).digest()
    return _b64u_encode(sig)


def _require_secret() -> str:
    secret = settings.TRANSFER_TOKEN_SECRET
    if not secret:
        raise TransferTokenError(
            "Transfer token secret is not configured "
            "(set TRANSFER_TOKEN_SECRET or SECRETS_ENCRYPTION_KEY)"
        )
    return secret


def mint_token(
    payload: TransferTokenPayload,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str:
    """Create a signed token for the given transfer payload.

    If ``payload.exp`` is None, set it to ``now + ttl_seconds``.
    """
    secret = _require_secret()

    data = asdict(payload)
    if data["exp"] is None:
        data["exp"] = int(time.time()) + ttl_seconds

    payload_json = json.dumps(data, separators=(",", ":"), sort_keys=True)
    payload_segment = _b64u_encode(payload_json.encode("utf-8"))
    signature_segment = _sign(payload_segment, secret)
    return f"{VERSION}.{payload_segment}.{signature_segment}"


def verify_token(token: str) -> TransferTokenPayload:
    """Validate a token and return its payload.

    Raises TransferTokenError on any validation failure.
    """
    secret = _require_secret()

    if not token or token.count(".") != 2:
        raise TransferTokenError("malformed token")

    version, payload_segment, signature_segment = token.split(".")

    if version != VERSION:
        raise TransferTokenError(f"unsupported token version: {version}")

    expected = _sign(payload_segment, secret)
    if not hmac.compare_digest(expected, signature_segment):
        raise TransferTokenError("invalid signature")

    try:
        data = json.loads(_b64u_decode(payload_segment))
    except (ValueError, json.JSONDecodeError):
        raise TransferTokenError("malformed payload")

    exp = data.get("exp")
    if not isinstance(exp, int) or exp < int(time.time()):
        raise TransferTokenError("token expired")

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
