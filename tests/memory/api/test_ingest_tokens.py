import json
import time
from unittest.mock import patch

import pytest

from memory.api import ingest_tokens as it
from memory.api import signed_tokens
from memory.api import transfer_tokens as tt


def _ingest_token_from_segment(seg: str) -> str:
    sig = signed_tokens.sign_segment(seg, domain="ingest.v1", secret=SECRET)
    return f"{signed_tokens.VERSION}.{seg}.{sig}"


SECRET = "test-secret-key-for-ingest-tokens-only"


@pytest.fixture(autouse=True)
def patch_secret():
    with patch("memory.common.settings.TRANSFER_TOKEN_SECRET", SECRET):
        yield


def _payload():
    return it.IngestTokenPayload(
        user_id=1, type="application/pdf", filename="x.pdf",
        tags=["a"], doc_metadata={"k": "v"}, project_id=None, exp=None,
    )


def test_roundtrip():
    token = it.mint_token(_payload(), ttl_seconds=60)
    got = it.verify_token(token)
    assert got.user_id == 1
    assert got.type == "application/pdf"
    assert got.tags == ["a"]
    assert got.doc_metadata == {"k": "v"}


def test_tampered_rejected():
    token = it.mint_token(_payload(), ttl_seconds=60)
    head, mid, sig = token.split(".")
    with pytest.raises(it.IngestTokenError):
        it.verify_token(f"{head}.{mid}.{sig[:-2]}xx")


def test_expired_rejected():
    p = _payload()
    p.exp = int(time.time()) - 1
    token = it.mint_token(p)
    with pytest.raises(it.IngestTokenExpiredError):
        it.verify_token(token)


def test_malformed_rejected():
    with pytest.raises(it.IngestTokenError):
        it.verify_token("not-a-token")
    with pytest.raises(it.IngestTokenError):
        it.verify_token("v1.only-two")


def test_transfer_token_rejected_cross_protocol():
    """A cloud-claude transfer token (same secret, same wire format) must not
    validate as an ingest token — domain separation makes the signatures
    non-interchangeable, and a wrong-shape payload is an auth failure not a 500."""
    transfer = tt.mint_token(
        tt.TransferTokenPayload(
            user_id=1, session_id="s", path="/p", action="write", exp=None
        ),
        ttl_seconds=60,
    )
    with pytest.raises(it.IngestTokenError):
        it.verify_token(transfer)


def test_wrong_shape_payload_rejected_not_typeerror():
    """A correctly-signed-by-ingest token whose JSON shape doesn't match the
    schema raises IngestTokenError (→ 403), never an unhandled TypeError."""
    seg = signed_tokens.b64u_encode(json.dumps({"unexpected": "field"}).encode())
    token = _ingest_token_from_segment(seg)
    with pytest.raises(it.IngestTokenError):
        it.verify_token(token)


def test_non_dict_payload_rejected():
    seg = signed_tokens.b64u_encode(json.dumps([1, 2, 3]).encode())
    token = _ingest_token_from_segment(seg)
    with pytest.raises(it.IngestTokenError):
        it.verify_token(token)
