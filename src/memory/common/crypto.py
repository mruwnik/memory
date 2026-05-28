"""Shared crypto primitives.

Kept as a leaf module that depends on nothing in this codebase — both
``settings`` and ``db.models.secrets`` can import from it without
circularity.
"""

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# Domain-separating ``info`` includes ``v1`` so that rotating the
# derivation scheme (e.g. switching to a different hash) cleanly
# invalidates all existing tokens by bumping the version tag.
TRANSFER_TOKEN_SECRET_HKDF_INFO = b"memory:transfer-token-secret:v1"


def derive_transfer_token_secret(master_key: str, salt: bytes) -> str:
    """HKDF-SHA256-derive a 32-byte (256-bit) HMAC key from ``master_key``.

    Returns hex (callers ``encode("utf-8")`` the secret, so any printable
    string works; hex keeps the value greppable in process listings if
    it ever leaks). The derivation is deterministic — same input → same
    output — which is required so all API instances in a deployment
    compute the same transfer secret without coordination.

    The HKDF ``info`` (:data:`TRANSFER_TOKEN_SECRET_HKDF_INFO`) provides
    domain separation: the same ``master_key`` and ``salt`` used with a
    different ``info`` would produce a different output, so a leaked
    transfer secret cannot be brute-forced in tandem with another HKDF
    output derived from the same key.
    """
    derived_bytes = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=TRANSFER_TOKEN_SECRET_HKDF_INFO,
    ).derive(master_key.encode("utf-8"))
    return derived_bytes.hex()
