"""Key validation shared by the API and workers."""

import base64
import hashlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

from memory.common.access_control import UserLike, has_admin_scope
from memory.common.db.models.ssh_keys import SSHKey
from memory.common.encryption import encrypt_value


def encode_key(private) -> tuple[bytes, str, str]:
    if not isinstance(
        private,
        (ed25519.Ed25519PrivateKey, rsa.RSAPrivateKey, ec.EllipticCurvePrivateKey),
    ):
        raise ValueError("Use an Ed25519, RSA or ECDSA key")
    if isinstance(private, rsa.RSAPrivateKey) and private.key_size < 2048:
        raise ValueError("RSA keys must be at least 2048 bits")
    public = (
        private.public_key()
        .public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
        )
        .decode()
    )
    fingerprint = "SHA256:" + base64.b64encode(
        hashlib.sha256(base64.b64decode(public.split()[1])).digest()
    ).decode().rstrip("=")
    plain = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    ).decode()
    return encrypt_value(plain), public, fingerprint


def import_key(value: str, passphrase: str | None = None) -> tuple[bytes, str, str]:
    password = passphrase.encode() if passphrase else None
    data = value.strip().encode() + b"\n"
    loader = (
        serialization.load_ssh_private_key
        if data.startswith(b"-----BEGIN OPENSSH")
        else serialization.load_pem_private_key
    )
    return encode_key(loader(data, password=password))


def can_use_key(key: SSHKey, user: UserLike) -> bool:
    return key.user_id == user.id or (key.user_id is None and has_admin_scope(user))
