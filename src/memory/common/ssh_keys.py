"""Key validation shared by the API and workers."""

import base64
import binascii
import hashlib
import re
import struct

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

from memory.common.access_control import UserLike, has_admin_scope
from memory.common.db.models.ssh_keys import SSHKey
from memory.common.encryption import encrypt_value

# Longest key text accepted: an 8192-bit RSA key takes about 6.5 KB.
MAX_KEY_CHARS = 16384
MAX_RSA_BITS = 8192
# Decrypting a key runs the KDF with the cost written in the key file itself,
# so these bound what one import can make the server compute.
# ssh-keygen's default is 16 rounds; guides suggest up to 100.
MAX_BCRYPT_ROUNDS = 128
MAX_PBKDF_ITERATIONS = 600_000
# scrypt's memory and work, 128 * N * r * p bytes.
MAX_SCRYPT_COST = 64 * 1024 * 1024

PEM_BLOCK = re.compile(rb"-----BEGIN ([A-Z0-9 ]+)-----(.*?)-----END \1-----", re.S)
OPENSSH_MAGIC = b"openssh-key-v1\0"
PBES2 = "1.2.840.113549.1.5.13"
PBKDF2 = "1.2.840.113549.1.5.12"
SCRYPT = "1.3.6.1.4.1.11591.4.11"
DER_SEQUENCE, DER_INTEGER, DER_OID = 0x30, 0x02, 0x06
TOO_COSTLY = "Key encryption is too costly to import: re-encrypt it with ssh-keygen -p -a 16, or import it unencrypted"


def pem_blocks(data: bytes) -> list[tuple[str, bytes]]:
    """(label, decoded body) of each PEM block, skipping RFC 1421 headers."""
    blocks = []
    for label, body in PEM_BLOCK.findall(data):
        lines = [line.strip() for line in body.strip().splitlines() if b":" not in line]
        try:
            blocks.append((label.decode(), base64.b64decode(b"".join(lines), validate=True)))
        except binascii.Error as exc:
            raise ValueError("Malformed key") from exc
    return blocks


def ssh_string(data: bytes, offset: int) -> tuple[bytes, int]:
    if offset + 4 > len(data):
        raise ValueError("Malformed key")
    (length,) = struct.unpack_from(">I", data, offset)
    end = offset + 4 + length
    if end > len(data):
        raise ValueError("Malformed key")
    return data[offset + 4 : end], end


def check_openssh_kdf(blob: bytes) -> None:
    if not blob.startswith(OPENSSH_MAGIC):
        raise ValueError("Malformed key")
    _cipher, offset = ssh_string(blob, len(OPENSSH_MAGIC))
    kdf, offset = ssh_string(blob, offset)
    options, _ = ssh_string(blob, offset)
    if kdf != b"bcrypt":
        return
    _salt, offset = ssh_string(options, 0)
    if offset + 4 > len(options):
        raise ValueError("Malformed key")
    if struct.unpack_from(">I", options, offset)[0] > MAX_BCRYPT_ROUNDS:
        raise ValueError(TOO_COSTLY)


def der_items(data: bytes) -> list[tuple[int, bytes]]:
    """The (tag, content) of each DER element in ``data``, one level deep."""
    items, offset = [], 0
    while offset < len(data):
        if offset + 2 > len(data):
            raise ValueError("Malformed key")
        tag, length = data[offset], data[offset + 1]
        offset += 2
        if length & 0x80:
            size = length & 0x7F
            if not 0 < size <= 4 or offset + size > len(data):
                raise ValueError("Malformed key")
            length = int.from_bytes(data[offset : offset + size], "big")
            offset += size
        if offset + length > len(data):
            raise ValueError("Malformed key")
        items.append((tag, data[offset : offset + length]))
        offset += length
    return items


def der_sequence(content: bytes, min_items: int) -> list[tuple[int, bytes]]:
    items = der_items(content)
    if len(items) < min_items:
        raise ValueError("Malformed key")
    return items


def der_int(item: tuple[int, bytes]) -> int:
    tag, content = item
    if tag != DER_INTEGER:
        raise ValueError("Malformed key")
    return int.from_bytes(content, "big", signed=True)


def der_oid(item: tuple[int, bytes]) -> str:
    tag, content = item
    if tag != DER_OID or not content or content[-1] & 0x80:
        raise ValueError("Malformed key")
    arcs, value = [], 0
    for byte in content:
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            arcs.append(value)
            value = 0
    first = min(arcs[0] // 40, 2)
    return ".".join(map(str, [first, arcs[0] - 40 * first, *arcs[1:]]))


def algorithm(item: tuple[int, bytes]) -> tuple[str, list[tuple[int, bytes]]]:
    """An AlgorithmIdentifier's OID and its parameters' elements (a SEQUENCE's)."""
    tag, content = item
    if tag != DER_SEQUENCE:
        raise ValueError("Malformed key")
    items = der_sequence(content, 1)
    params = items[1:] if len(items) > 1 else []
    if params and params[0][0] == DER_SEQUENCE:
        return der_oid(items[0]), der_items(params[0][1])
    return der_oid(items[0]), []


def check_pkcs8_kdf(der: bytes) -> None:
    """Refuse an EncryptedPrivateKeyInfo whose KDF cost exceeds the caps."""
    items = der_items(der)
    if len(items) != 1 or items[0][0] != DER_SEQUENCE:
        raise ValueError("Malformed key")
    oid, params = algorithm(der_sequence(items[0][1], 2)[0])
    if oid != PBES2:
        # PKCS#5 v1 and PKCS#12 schemes: PBEParameter {salt, iterationCount}.
        if len(params) < 2 or der_int(params[1]) > MAX_PBKDF_ITERATIONS:
            raise ValueError(TOO_COSTLY)
        return
    if not params:
        raise ValueError("Malformed key")
    kdf, kdf_params = algorithm(params[0])
    if kdf == PBKDF2 and len(kdf_params) >= 2:
        if der_int(kdf_params[1]) > MAX_PBKDF_ITERATIONS:
            raise ValueError(TOO_COSTLY)
        return
    if kdf == SCRYPT and len(kdf_params) >= 4:
        n, r, p = (der_int(item) for item in kdf_params[1:4])
        if min(n, r, p) < 1 or 128 * n * r * p > MAX_SCRYPT_COST:
            raise ValueError(TOO_COSTLY)
        return
    raise ValueError("Unsupported key encryption")


def check_kdf_cost(data: bytes) -> None:
    """Refuse key text whose decryption would cost more than the caps, before decrypting it."""
    blocks = pem_blocks(data)
    if len(blocks) != 1:
        raise ValueError("Provide exactly one PEM or OpenSSH private key")
    label, body = blocks[0]
    if label == "OPENSSH PRIVATE KEY":
        check_openssh_kdf(body)
    elif label == "ENCRYPTED PRIVATE KEY":
        check_pkcs8_kdf(body)


def load_private_key(data: bytes, password: bytes | None):
    """Load key text, refusing costly KDF settings and oversized RSA keys before the expensive work."""
    if len(data) > MAX_KEY_CHARS:
        raise ValueError("Key is too large")
    check_kdf_cost(data)
    # RSA validation tests p and q for primality, which takes seconds at
    # 8192 bits and grows with the cube of the size: check the size first.
    if data.startswith(b"-----BEGIN OPENSSH"):
        private = serialization.load_ssh_private_key(
            data, password=password, unsafe_skip_rsa_key_validation=True
        )
    else:
        private = serialization.load_pem_private_key(
            data, password=password, unsafe_skip_rsa_key_validation=True
        )
    if not isinstance(private, rsa.RSAPrivateKey):
        return private
    if private.key_size > MAX_RSA_BITS:
        raise ValueError(f"RSA keys must be at most {MAX_RSA_BITS} bits")
    plain = private.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return serialization.load_der_private_key(plain, password=None)


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
    return encode_key(load_private_key(data, password))


def can_use_key(key: SSHKey, user: UserLike) -> bool:
    return key.user_id == user.id or (key.user_id is None and has_admin_scope(user))
