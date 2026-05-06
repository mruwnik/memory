"""Tests for the Slack push-events endpoint helpers (slack-changes.md §3.3).

Focuses on the pure-function security primitives that don't need DB or
fakeredis: HMAC verification, signature prefix discipline, signature
basestring construction. Higher-level dispatch and route tests live with
the broader Slack API tests once the DB-backed fixtures are available.
"""

import hashlib
import hmac

import pytest

from memory.api.slack import _verify_slack_signature


def _slack_sig(secret: str, ts: str, body: bytes) -> str:
    """Build a v0= header the way Slack would."""
    basestring = f"v0:{ts}:".encode() + body
    digest = hmac.new(secret.encode("utf-8"), basestring, hashlib.sha256).hexdigest()
    return f"v0={digest}"


def test_verify_slack_signature_accepts_valid_signature():
    secret = "abc-signing-secret"
    ts = "1620000000"
    body = b'{"type":"event_callback"}'
    sig = _slack_sig(secret, ts, body)
    assert _verify_slack_signature(secret, ts, body, sig) is True


def test_verify_slack_signature_rejects_modified_body():
    secret = "abc-signing-secret"
    ts = "1620000000"
    body = b'{"type":"event_callback"}'
    sig = _slack_sig(secret, ts, body)
    tampered = b'{"type":"event_callback","extra":"x"}'
    assert _verify_slack_signature(secret, ts, tampered, sig) is False


def test_verify_slack_signature_rejects_modified_ts():
    secret = "abc-signing-secret"
    ts = "1620000000"
    body = b'{"type":"event_callback"}'
    sig = _slack_sig(secret, ts, body)
    assert _verify_slack_signature(secret, "1620000001", body, sig) is False


def test_verify_slack_signature_rejects_wrong_secret():
    secret = "abc-signing-secret"
    ts = "1620000000"
    body = b'{"type":"event_callback"}'
    sig = _slack_sig(secret, ts, body)
    assert _verify_slack_signature("different-secret", ts, body, sig) is False


@pytest.mark.parametrize(
    "bad_header",
    [
        # Missing v0= prefix entirely. We must reject — the prefix is the
        # only versioning marker, and accepting a bare hex digest invites
        # rolling-secret confusion later.
        "abcdef0123456789",
        # Wrong version marker. v1 is not Slack's current scheme.
        "v1=abcdef",
        # Empty.
        "",
        # Just the prefix with no digest.
        "v0=",
    ],
)
def test_verify_slack_signature_rejects_malformed_header(bad_header):
    assert (
        _verify_slack_signature("secret", "1620000000", b"body", bad_header)
        is False
    )


def test_verify_slack_signature_rejects_wrong_length_digest():
    """A v0= prefix followed by a non-sha256-length digest must fail.

    Without compare_digest's length check this would still be safe, but
    a mismatch should never accidentally match — be explicit about it.
    """
    short_sig = "v0=" + "a" * 10  # 10 chars instead of 64
    assert (
        _verify_slack_signature("secret", "1620000000", b"body", short_sig)
        is False
    )


def test_verify_slack_signature_basestring_includes_v0_prefix():
    """The basestring is `v0:{ts}:{body}`. A signature computed over
    a different basestring (e.g. omitting the v0: prefix) must fail.

    This mutation discriminator catches a future refactor that
    accidentally drops the `v0:` literal."""
    secret = "abc"
    ts = "1620000000"
    body = b"payload"
    # Signature computed over the WRONG basestring (no v0: prefix).
    wrong_basestring = f"{ts}:".encode() + body
    bad_digest = hmac.new(
        secret.encode(), wrong_basestring, hashlib.sha256
    ).hexdigest()
    assert (
        _verify_slack_signature(secret, ts, body, f"v0={bad_digest}") is False
    )


def test_verify_slack_signature_basestring_uses_colon_separator():
    """Mutation discriminator: separator between ts and body is `:`,
    not space, dash, etc. Catches a refactor that munges the basestring
    format."""
    secret = "abc"
    ts = "1620000000"
    body = b"payload"
    wrong_basestring = f"v0:{ts} ".encode() + body  # space instead of colon
    bad_digest = hmac.new(
        secret.encode(), wrong_basestring, hashlib.sha256
    ).hexdigest()
    assert (
        _verify_slack_signature(secret, ts, body, f"v0={bad_digest}") is False
    )
