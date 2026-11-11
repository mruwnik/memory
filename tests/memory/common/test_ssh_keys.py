"""SSH key import bounds the work a key file can ask the server to do."""

import base64
import struct
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

from memory.common.ssh_keys import (
    MAX_BCRYPT_ROUNDS,
    MAX_KEY_CHARS,
    MAX_PBKDF_ITERATIONS,
    import_key,
    load_private_key,
)


def der(tag: int, content: bytes) -> bytes:
    length = len(content)
    if length < 0x80:
        return bytes([tag, length]) + content
    size = (length.bit_length() + 7) // 8
    return bytes([tag, 0x80 | size]) + length.to_bytes(size, "big") + content


def seq(*items: bytes) -> bytes:
    return der(0x30, b"".join(items))


def integer(value: int) -> bytes:
    return der(0x02, value.to_bytes(value.bit_length() // 8 + 1, "big"))


def octets(value: bytes) -> bytes:
    return der(0x04, value)


def oid(dotted: str) -> bytes:
    first, second, *rest = (int(arc) for arc in dotted.split("."))
    body = bytearray([40 * first + second])
    for arc in rest:
        chunk = [arc & 0x7F]
        arc >>= 7
        while arc:
            chunk.append(0x80 | (arc & 0x7F))
            arc >>= 7
        body.extend(reversed(chunk))
    return der(0x06, bytes(body))


def pem(label: str, body: bytes) -> bytes:
    lines = base64.encodebytes(body).decode()
    return f"-----BEGIN {label}-----\n{lines}-----END {label}-----\n".encode()


AES_CBC = seq(oid("2.16.840.1.101.3.4.1.42"), octets(b"\0" * 16))


def pbkdf2_key(iterations: int) -> bytes:
    kdf = seq(oid("1.2.840.113549.1.5.12"), seq(octets(b"salt" * 4), integer(iterations)))
    scheme = seq(oid("1.2.840.113549.1.5.13"), seq(kdf, AES_CBC))
    return pem("ENCRYPTED PRIVATE KEY", seq(scheme, octets(b"\0" * 64)))


def scrypt_key(n: int, r: int, p: int) -> bytes:
    params = seq(octets(b"salt" * 4), integer(n), integer(r), integer(p))
    kdf = seq(oid("1.3.6.1.4.1.11591.4.11"), params)
    scheme = seq(oid("1.2.840.113549.1.5.13"), seq(kdf, AES_CBC))
    return pem("ENCRYPTED PRIVATE KEY", seq(scheme, octets(b"\0" * 64)))


def pkcs12_pbe_key(iterations: int) -> bytes:
    scheme = seq(oid("1.2.840.113549.1.12.1.3"), seq(octets(b"salt" * 2), integer(iterations)))
    return pem("ENCRYPTED PRIVATE KEY", seq(scheme, octets(b"\0" * 64)))


def openssh_key(rounds: int) -> bytes:
    encryption = (
        serialization.PrivateFormat.OpenSSH.encryption_builder().kdf_rounds(rounds).build(b"pw")
    )
    return ed25519.Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, encryption
    )


def ssh_str(value: bytes) -> bytes:
    return struct.pack(">I", len(value)) + value


def smuggled_openssh_key(rounds: int, separator: bytes) -> bytes:
    """A bcrypt key whose first line ends in `separator`.

    Read without that line, the body is a decoy header with kdf "none"; the
    OpenSSH loader decodes leniently and reads the bcrypt header instead.
    """
    magic = b"openssh-key-v1\0"
    public = ed25519.Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    pubkey = ssh_str(b"ssh-ed25519") + ssh_str(public)
    decoy = magic + ssh_str(b"none") + ssh_str(b"none") + ssh_str(b"")
    edata = decoy + b"\0" * (-len(decoy) % 48)

    def head(salt_len: int) -> bytes:
        options = ssh_str(b"S" * salt_len) + struct.pack(">I", rounds)
        return (
            magic + ssh_str(b"aes256-ctr") + ssh_str(b"bcrypt") + ssh_str(options)
            + struct.pack(">I", 1) + ssh_str(pubkey) + struct.pack(">I", len(edata))
        )

    real = next(head(n) for n in range(16, 19) if len(head(n)) % 3 == 0)
    return (
        b"-----BEGIN OPENSSH PRIVATE KEY-----\n" + base64.b64encode(real) + separator + b"\n"
        + base64.b64encode(edata) + b"\n-----END OPENSSH PRIVATE KEY-----\n"
    )


def fake_rsa_key(bits: int) -> bytes:
    """An unencrypted PKCS#8 RSA key of `bits` whose factors aren't prime."""
    p = (1 << (bits // 2 - 1)) | 1
    q = (1 << (bits - bits // 2 - 1)) | 3
    numbers = rsa.RSAPrivateNumbers(
        p=p, q=q, d=3, dmp1=3, dmq1=3, iqmp=1,
        public_numbers=rsa.RSAPublicNumbers(e=65537, n=p * q),
    )
    return numbers.private_key(unsafe_skip_rsa_key_validation=True).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


@pytest.mark.parametrize(
    "data",
    [
        openssh_key(MAX_BCRYPT_ROUNDS + 1),
        pbkdf2_key(MAX_PBKDF_ITERATIONS + 1),
        pbkdf2_key(2**62),
        scrypt_key(2**14, 8, 5),
        scrypt_key(2**20, 8, 1),
        pkcs12_pbe_key(MAX_PBKDF_ITERATIONS + 1),
    ],
    ids=["bcrypt rounds", "pbkdf2 iterations", "pbkdf2 huge", "scrypt p", "scrypt N", "pkcs12 iterations"],
)
def test_a_costly_key_encryption_is_refused_before_decrypting(data):
    with pytest.raises(ValueError, match="too costly"):
        load_private_key(data, b"pw")


def test_an_rsa_key_over_the_size_cap_is_refused_before_validation():
    with pytest.raises(ValueError, match="at most 8192 bits"):
        load_private_key(fake_rsa_key(8200), None)


@pytest.mark.parametrize(
    "data, match",
    [
        (b"x" * (MAX_KEY_CHARS + 1), "too large"),
        (openssh_key(16) + openssh_key(16), "exactly one"),
        (b"no key here", "exactly one"),
        (pem("OPENSSH PRIVATE KEY", b"openssh-key-v1\0\0\0\0\xff"), "Malformed"),
        (pem("OPENSSH PRIVATE KEY", b"not openssh"), "Malformed"),
        (pem("ENCRYPTED PRIVATE KEY", b"\x30\x84\xff\xff\xff\xff"), "Malformed"),
        (pem("ENCRYPTED PRIVATE KEY", seq(seq(der(0x06, b"\x2a\x86")), octets(b""))), "Malformed"),
        (pem("ENCRYPTED PRIVATE KEY", seq(seq(oid("1.2.840.113549.1.5.13"), seq(
            seq(oid("1.2.3.4")), AES_CBC)), octets(b""))), "Unsupported"),
    ],
    ids=["oversized", "two keys", "no key", "truncated openssh", "bad magic", "bad length", "bad oid", "unknown kdf"],
)
def test_malformed_key_text_is_a_value_error(data, match):
    with pytest.raises(ValueError, match=match):
        load_private_key(data, b"pw")


def encrypted(private, fmt) -> str:
    return private.private_bytes(
        serialization.Encoding.PEM, fmt, serialization.BestAvailableEncryption(b"pw")
    ).decode()


@pytest.mark.parametrize(
    "text",
    [
        openssh_key(16).decode(),
        openssh_key(MAX_BCRYPT_ROUNDS).decode(),
        encrypted(ed25519.Ed25519PrivateKey.generate(), serialization.PrivateFormat.PKCS8),
        encrypted(ec.generate_private_key(ec.SECP256R1()), serialization.PrivateFormat.TraditionalOpenSSL),
        encrypted(rsa.generate_private_key(65537, 2048), serialization.PrivateFormat.TraditionalOpenSSL),
    ],
    ids=["openssh default", "openssh at cap", "pkcs8", "traditional ec", "traditional rsa"],
)
def test_ordinary_encrypted_keys_still_import(text):
    _encrypted, public, fingerprint = import_key(text, "pw")
    assert (public.split()[0] in {"ssh-ed25519", "ecdsa-sha2-nistp256", "ssh-rsa"}, fingerprint[:7]) == (True, "SHA256:")


def test_an_invalid_rsa_key_under_the_cap_is_still_refused():
    with pytest.raises(ValueError):
        load_private_key(fake_rsa_key(2048), None)


@pytest.mark.parametrize("separator", [b":", b": x", b"*", b"!"])
def test_a_key_line_the_loader_would_read_differently_is_refused(separator):
    start = time.monotonic()
    with pytest.raises(ValueError, match="Malformed"):
        load_private_key(smuggled_openssh_key(MAX_BCRYPT_ROUNDS * 4, separator), b"pw")
    assert time.monotonic() - start < 1
