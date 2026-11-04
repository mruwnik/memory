import json
import time
from unittest.mock import patch

import pytest

from memory.api import signed_tokens

SECRET = "test-secret-key-for-signed-tokens-only"
DOMAIN = "test.v1"


@pytest.fixture(autouse=True)
def patch_secret():
    with patch("memory.common.settings.TRANSFER_TOKEN_SECRET", SECRET):
        yield


def token_from_segment(segment: str) -> str:
    sig = signed_tokens.sign_segment(segment, domain=DOMAIN, secret=SECRET)
    return f"{signed_tokens.VERSION}.{segment}.{sig}"


def test_round_trip_sets_default_exp():
    before = int(time.time())
    data = signed_tokens.verify(
        signed_tokens.sign({"a": 1}, domain=DOMAIN, ttl_seconds=120), domain=DOMAIN
    )
    assert data["a"] == 1
    assert before + 110 < data["exp"] <= before + 121


def test_other_domain_rejected():
    token = signed_tokens.sign({"a": 1}, domain="other.v1", ttl_seconds=60)
    with pytest.raises(signed_tokens.SignedTokenError, match="signature"):
        signed_tokens.verify(token, domain=DOMAIN)


def test_wrong_version_rejected():
    _, segment, sig = signed_tokens.sign({"a": 1}, domain=DOMAIN, ttl_seconds=60).split(".")
    with pytest.raises(signed_tokens.SignedTokenError, match="version"):
        signed_tokens.verify(f"v2.{segment}.{sig}", domain=DOMAIN)


def test_expired_raises_typed_subclass():
    token = signed_tokens.sign(
        {"exp": int(time.time()) - 1}, domain=DOMAIN, ttl_seconds=60
    )
    with pytest.raises(signed_tokens.SignedTokenExpiredError):
        signed_tokens.verify(token, domain=DOMAIN)


def test_secret_rotation_invalidates_existing_tokens():
    token = signed_tokens.sign({"a": 1}, domain=DOMAIN, ttl_seconds=60)
    with patch("memory.common.settings.TRANSFER_TOKEN_SECRET", "rotated-secret"):
        with pytest.raises(signed_tokens.SignedTokenError, match="signature"):
            signed_tokens.verify(token, domain=DOMAIN)


def test_empty_secret_refuses_to_sign():
    with patch("memory.common.settings.TRANSFER_TOKEN_SECRET", ""):
        with pytest.raises(signed_tokens.SignedTokenError, match="signing secret"):
            signed_tokens.sign({"a": 1}, domain=DOMAIN, ttl_seconds=60)


def test_empty_secret_refuses_to_verify():
    token = signed_tokens.sign({"a": 1}, domain=DOMAIN, ttl_seconds=60)
    with patch("memory.common.settings.TRANSFER_TOKEN_SECRET", ""):
        with pytest.raises(signed_tokens.SignedTokenError, match="signing secret"):
            signed_tokens.verify(token, domain=DOMAIN)


@pytest.mark.parametrize(
    "token",
    ["", "not-a-token", "v1.only-two", "v1.πayload.signature"],
)
def test_malformed_token_rejected(token):
    with pytest.raises(signed_tokens.SignedTokenError, match="malformed"):
        signed_tokens.verify(token, domain=DOMAIN)


@pytest.mark.parametrize(
    "segment",
    [
        "!@#$",
        signed_tokens.b64u_encode(b"\xff\xfe"),
        signed_tokens.b64u_encode(b"{not json"),
        signed_tokens.b64u_encode(json.dumps([1, 2, 3]).encode()),
    ],
    ids=["non-base64", "non-utf8", "non-json", "non-dict"],
)
def test_correctly_signed_garbage_payload_is_malformed(segment):
    with pytest.raises(signed_tokens.SignedTokenError, match="malformed"):
        signed_tokens.verify(token_from_segment(segment), domain=DOMAIN)


@pytest.mark.parametrize(
    "payload",
    [{}, {"exp": None}, {"exp": "not-an-int"}, {"exp": 1.5}, {"exp": [1]}, {"exp": True}],
)
def test_missing_or_non_int_exp_is_malformed_not_expired(payload):
    segment = signed_tokens.b64u_encode(json.dumps(payload).encode())
    with pytest.raises(signed_tokens.SignedTokenError, match="malformed") as exc_info:
        signed_tokens.verify(token_from_segment(segment), domain=DOMAIN)
    assert not isinstance(exc_info.value, signed_tokens.SignedTokenExpiredError)
