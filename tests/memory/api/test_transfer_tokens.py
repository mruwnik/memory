"""Tests for short-lived HMAC tokens used by cloud-claude file transfer URLs."""

import time
from unittest.mock import patch

import pytest

from memory.api.transfer_tokens import (
    TransferTokenError,
    TransferTokenPayload,
    mint_token,
    mint_transfer_url,
    validate_transfer_path,
    verify_token,
)


SECRET = "test-secret-key-for-transfer-tokens-only"


@pytest.fixture(autouse=True)
def patch_secret():
    with patch("memory.common.settings.TRANSFER_TOKEN_SECRET", SECRET):
        yield


def make_payload(**overrides) -> TransferTokenPayload:
    base = dict(
        user_id=42,
        session_id="u42-e6-deadbeef0000",
        path="/workspace/report.md",
        action="read",
    )
    base.update(overrides)
    return TransferTokenPayload(**base, exp=int(time.time()) + 60)


def test_round_trip_returns_same_payload():
    payload = make_payload()
    token = mint_token(payload)
    decoded = verify_token(token)
    assert decoded.user_id == payload.user_id
    assert decoded.session_id == payload.session_id
    assert decoded.path == payload.path
    assert decoded.action == payload.action
    assert decoded.exp == payload.exp


def test_token_has_three_dot_separated_parts():
    token = mint_token(make_payload())
    parts = token.split(".")
    assert len(parts) == 3
    assert parts[0] == "v1"


@pytest.mark.parametrize("action", ["read", "write"])
def test_round_trip_preserves_action(action):
    decoded = verify_token(mint_token(make_payload(action=action)))
    assert decoded.action == action


def test_expired_token_rejected():
    payload = TransferTokenPayload(
        user_id=42,
        session_id="u42-e6-aaaaaaaa0000",
        path="/workspace/x",
        action="read",
        exp=int(time.time()) - 1,
    )
    token = mint_token(payload)
    with pytest.raises(TransferTokenError, match="expired"):
        verify_token(token)


def test_tampered_payload_rejected():
    token = mint_token(make_payload())
    parts = token.split(".")
    # Flip a byte in the payload segment
    bad_payload = parts[1][:-1] + ("A" if parts[1][-1] != "A" else "B")
    bad = ".".join([parts[0], bad_payload, parts[2]])
    with pytest.raises(TransferTokenError, match="signature"):
        verify_token(bad)


def test_tampered_signature_rejected():
    token = mint_token(make_payload())
    parts = token.split(".")
    bad_sig = parts[2][:-1] + ("A" if parts[2][-1] != "A" else "B")
    bad = ".".join([parts[0], parts[1], bad_sig])
    with pytest.raises(TransferTokenError, match="signature"):
        verify_token(bad)


def test_wrong_version_rejected():
    token = mint_token(make_payload())
    parts = token.split(".")
    bad = ".".join(["v2", parts[1], parts[2]])
    with pytest.raises(TransferTokenError, match="version"):
        verify_token(bad)


def test_malformed_token_rejected():
    with pytest.raises(TransferTokenError, match="malformed"):
        verify_token("not-a-token")
    with pytest.raises(TransferTokenError, match="malformed"):
        verify_token("v1.only-two-parts")
    with pytest.raises(TransferTokenError, match="malformed"):
        verify_token("")


def test_mint_uses_default_ttl_when_exp_missing():
    payload = TransferTokenPayload(
        user_id=1,
        session_id="u1-e6-aaaa",
        path="/p",
        action="read",
        exp=None,
    )
    before = int(time.time())
    token = mint_token(payload, ttl_seconds=120)
    decoded = verify_token(token)
    assert before + 110 < decoded.exp <= before + 121


def test_different_secrets_produce_different_signatures():
    payload = make_payload()
    with patch("memory.common.settings.TRANSFER_TOKEN_SECRET", "secret-a"):
        token_a = mint_token(payload)
    with patch("memory.common.settings.TRANSFER_TOKEN_SECRET", "secret-b"):
        token_b = mint_token(payload)
    assert token_a.split(".")[1] == token_b.split(".")[1]  # payload identical
    assert token_a.split(".")[2] != token_b.split(".")[2]  # signature differs


def test_secret_change_invalidates_existing_tokens():
    payload = make_payload()
    with patch("memory.common.settings.TRANSFER_TOKEN_SECRET", "secret-a"):
        token = mint_token(payload)
    with patch("memory.common.settings.TRANSFER_TOKEN_SECRET", "secret-b"):
        with pytest.raises(TransferTokenError, match="signature"):
            verify_token(token)


def test_empty_secret_refuses_to_mint():
    with patch("memory.common.settings.TRANSFER_TOKEN_SECRET", ""):
        with pytest.raises(TransferTokenError, match="not configured"):
            mint_token(make_payload())


def test_empty_secret_refuses_to_verify():
    token = mint_token(make_payload())
    with patch("memory.common.settings.TRANSFER_TOKEN_SECRET", ""):
        with pytest.raises(TransferTokenError, match="not configured"):
            verify_token(token)


# -- validate_transfer_path -----------------------------------------------


@pytest.mark.parametrize(
    "bad_path",
    [
        "../../etc/passwd",
        "workspace/../../../etc/passwd",
        "workspace/./report.md",
        "workspace//report.md",
        "/../etc/passwd",
        "./report.md",
        "..",
        ".",
        "",
    ],
)
def test_validate_transfer_path_rejects_traversal(bad_path):
    with pytest.raises(ValueError):
        validate_transfer_path(bad_path)


@pytest.mark.parametrize(
    "bad_path",
    [
        "/workspace/foo\x00bar",
        "/workspace/foo\rbar",
        "/workspace/foo\nbar",
        '/workspace/foo"bar',
    ],
)
def test_validate_transfer_path_rejects_control_chars(bad_path):
    with pytest.raises(ValueError):
        validate_transfer_path(bad_path)


@pytest.mark.parametrize(
    "bad_path",
    [
        # `?` would re-route to the orchestrator's query-string-handling endpoints
        "/workspace/list?path=/etc/passwd",
        "/workspace/file?x",
        # `#` is a URL fragment
        "/workspace/file#frag",
        # `;` is matrix params
        "/workspace/file;param=x",
        # `%` would survive percent-decoding to e.g. `..` (`%2e%2e`)
        "/workspace/%2e%2e/etc",
        "/workspace/file%20with",
        # `&` is a query separator
        "/workspace/file&other",
        # `\` has no role in POSIX paths; reject for Windows-style normalizers
        "/workspace/foo\\bar",
        # space would need consistent URL quoting between code paths
        "/workspace/file with space.md",
    ],
)
def test_validate_transfer_path_rejects_url_meaningful_chars(bad_path):
    with pytest.raises(ValueError):
        validate_transfer_path(bad_path)


@pytest.mark.parametrize(
    "good_path",
    [
        "/workspace/report.md",
        "workspace/report.md",
        "/workspace/レポート.md",  # non-ASCII
        "/workspace/sub/dir/file.txt",
        "/a",
    ],
)
def test_validate_transfer_path_accepts_legit_paths(good_path):
    assert validate_transfer_path(good_path) == good_path


# -- mint_transfer_url ----------------------------------------------------


def test_mint_transfer_url_read_returns_url_with_token():
    out = mint_transfer_url(
        base_url="https://api.example.com/",
        user_id=42,
        session_id="u42-e6-aaaa",
        path="/workspace/x.md",
        action="read",
    )
    assert "url" in out and "expires_in" in out
    assert "/claude/transfer/pull?token=" in out["url"]
    assert "token" not in out  # read action doesn't expose separate token field
    # Trailing slash on base_url must be normalized (no `//` in result)
    assert "//claude" not in out["url"].replace("https://", "")
    payload = verify_token(out["url"].split("token=", 1)[1])
    assert payload.action == "read"
    assert payload.path == "/workspace/x.md"


def test_mint_transfer_url_write_returns_separate_token_field():
    out = mint_transfer_url(
        base_url="https://api.example.com",
        user_id=42,
        session_id="u42-e6-aaaa",
        path="/workspace",
        action="write",
    )
    assert "/claude/transfer/push" in out["url"]
    assert "token=" not in out["url"]  # write keeps token out of access logs
    assert "token" in out
    payload = verify_token(out["token"])
    assert payload.action == "write"


def test_mint_transfer_url_normalizes_path_to_absolute():
    """Path passed without leading slash should become absolute."""
    out = mint_transfer_url(
        base_url="https://api.example.com",
        user_id=42,
        session_id="u42-e6-aaaa",
        path="workspace/x.md",
        action="read",
    )
    payload = verify_token(out["url"].split("token=", 1)[1])
    assert payload.path == "/workspace/x.md"


def test_mint_transfer_url_rejects_traversal():
    with pytest.raises(ValueError):
        mint_transfer_url(
            base_url="https://api.example.com",
            user_id=42,
            session_id="u42-e6-aaaa",
            path="../../etc/passwd",
            action="read",
        )


# -- type coercion on verify ----------------------------------------------


def _mint_with_raw_payload(raw_data: dict) -> str:
    """Mint a token directly from a raw dict, bypassing the dataclass.

    Used to forge tokens with wrong types — exercises the verify-side
    type coercion that protects against future code paths writing junk.
    """
    import base64
    import hmac
    import json as _json
    from hashlib import sha256

    from memory.api.transfer_tokens import VERSION
    from memory.common import settings

    payload_json = _json.dumps(raw_data, separators=(",", ":"), sort_keys=True)
    seg = base64.urlsafe_b64encode(payload_json.encode()).rstrip(b"=").decode()
    sig = hmac.new(
        settings.TRANSFER_TOKEN_SECRET.encode(), seg.encode(), sha256
    ).digest()
    sig_seg = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
    return f"{VERSION}.{seg}.{sig_seg}"


def test_verify_rejects_non_int_user_id():
    """A signed-but-malformed payload (e.g. user_id is a string) must fail
    cleanly rather than being stored as-is and crashing some downstream
    consumer that does arithmetic on it."""
    token = _mint_with_raw_payload({
        "user_id": "not-an-int",
        "session_id": "u1-e6-aaaa",
        "path": "/workspace/x",
        "action": "read",
        "exp": int(time.time()) + 60,
    })
    with pytest.raises(TransferTokenError, match="malformed payload"):
        verify_token(token)


def test_verify_rejects_invalid_action():
    token = _mint_with_raw_payload({
        "user_id": 1,
        "session_id": "u1-e6-aaaa",
        "path": "/workspace/x",
        "action": "delete",  # not read/write
        "exp": int(time.time()) + 60,
    })
    with pytest.raises(TransferTokenError, match="malformed payload"):
        verify_token(token)


def test_verify_rejects_missing_field():
    token = _mint_with_raw_payload({
        "user_id": 1,
        # missing session_id
        "path": "/workspace/x",
        "action": "read",
        "exp": int(time.time()) + 60,
    })
    with pytest.raises(TransferTokenError, match="malformed payload"):
        verify_token(token)
