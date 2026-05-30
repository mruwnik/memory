"""Generic stateless HMAC token core shared by transfer_tokens and
ingest_tokens. A token signs a JSON dict payload under a caller-supplied
domain tag; the domain is mixed into the HMAC message so tokens minted for
one domain cannot validate under another (cryptographic domain separation).

Format: v1.<b64u(payload_json)>.<b64u(hmac_sha256("<domain>.<seg>", secret))>
"""

import base64
import binascii
import hmac
import json
import time
from hashlib import sha256

from memory.common import settings

VERSION = "v1"


class SignedTokenError(Exception):
    """Malformed, tampered, or otherwise invalid signed token."""


class SignedTokenExpiredError(SignedTokenError):
    """Token's ``exp`` claim is an int in the past."""


def b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def require_secret() -> str:
    secret = settings.TRANSFER_TOKEN_SECRET
    if not secret:
        raise SignedTokenError(
            "signed tokens require a signing secret "
            "(set TRANSFER_TOKEN_SECRET or SECRETS_ENCRYPTION_KEY)"
        )
    return secret


def sign_segment(segment: str, *, domain: str, secret: str) -> str:
    """HMAC the domain-tagged segment and return the base64url signature.

    The domain is prefixed into the signed message so a signature minted for
    one domain cannot validate under another, even though both families share
    the secret and wire format.
    """
    message = f"{domain}.{segment}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), message, sha256).digest()
    return b64u_encode(sig)


def sign(
    payload: dict,
    *,
    domain: str,
    ttl_seconds: int,
    secret: str | None = None,
) -> str:
    """Create a signed token for the given dict payload under ``domain``.

    If ``payload`` has no ``exp`` (or it is None), ``exp`` is set to
    ``now + ttl_seconds`` on a copy of the payload (the caller's dict is not
    mutated).
    """
    secret = secret if secret is not None else require_secret()

    data = dict(payload)
    if data.get("exp") is None:
        data["exp"] = int(time.time()) + ttl_seconds

    payload_json = json.dumps(data, separators=(",", ":"), sort_keys=True)
    segment = b64u_encode(payload_json.encode("utf-8"))
    return f"{VERSION}.{segment}.{sign_segment(segment, domain=domain, secret=secret)}"


def verify(token: str, *, domain: str, secret: str | None = None) -> dict:
    """Validate a token under ``domain`` and return its decoded dict payload.

    Raises ``SignedTokenExpiredError`` if the payload carries an int ``exp``
    in the past. Raises ``SignedTokenError`` on any other validation failure
    (malformed shape, bad version, bad signature, undecodable payload, or a
    non-dict payload). Type-checking of individual payload fields beyond
    "is a dict" is left to the caller.
    """
    secret = secret if secret is not None else require_secret()

    if not token or token.count(".") != 2:
        raise SignedTokenError("malformed token")

    version, segment, signature_segment = token.split(".")

    if version != VERSION:
        raise SignedTokenError(f"unsupported token version: {version}")

    # A malformed segment (e.g. a non-ASCII char that survives a junk token)
    # must map to a malformed-token failure, not a 500.
    try:
        expected = sign_segment(segment, domain=domain, secret=secret)
    except (UnicodeEncodeError, ValueError):
        raise SignedTokenError("malformed token")
    if not hmac.compare_digest(expected, signature_segment):
        raise SignedTokenError("invalid signature")

    # `b64u_decode` can raise `binascii.Error` for non-base64 garbage and
    # `UnicodeDecodeError` if the bytes don't decode as utf-8. Pin the
    # contract explicitly so a future Python release that re-classifies
    # binascii.Error doesn't surprise us with a 500.
    try:
        data = json.loads(b64u_decode(segment))
    except (binascii.Error, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise SignedTokenError("malformed payload")

    if not isinstance(data, dict):
        raise SignedTokenError("malformed token payload")

    exp = data.get("exp")
    if isinstance(exp, int) and exp < int(time.time()):
        raise SignedTokenExpiredError("token expired")

    return data
