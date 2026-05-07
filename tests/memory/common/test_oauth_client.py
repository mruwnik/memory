"""Tests for memory.common.oauth_client (OAuth CSRF state helpers).

Most of the test surface here is the logging-discipline guarantees
introduced by SECURITY/MED 7c02ac7c (CWE-532). The functional behavior
of state generation and signature verification is also exercised.
"""

from datetime import datetime, timedelta, timezone

import pytest

from memory.common.db.models import OAuthClientState, User
from memory.common.oauth_client import (
    generate_state,
    log_corr_id,
    sign_state,
    store_state,
    validate_and_consume_state,
    verify_state_signature,
)


@pytest.fixture
def oauth_user(db_session):
    user = User(name="OAuth User", email="oauth@example.com")
    db_session.add(user)
    db_session.commit()
    return user


# ---------------------------------------------------------------------------
# log_corr_id — non-reversible correlation IDs for sensitive values
# ---------------------------------------------------------------------------


def test_log_corr_id_is_deterministic():
    """Same input → same correlation id; useful for joining log lines."""
    assert log_corr_id("hello") == log_corr_id("hello")


def test_log_corr_id_differs_for_different_inputs():
    assert log_corr_id("a") != log_corr_id("b")


def test_log_corr_id_handles_empty():
    assert log_corr_id("") == "<empty>"
    assert log_corr_id(None) == "<empty>"


def test_log_corr_id_does_not_leak_input():
    """The id is a SHA256 prefix — must not contain any prefix of the input."""
    secret = "supersecret_state_value_with_characteristic_substring"
    cid = log_corr_id(secret)
    # No 4-char window of the secret appears in the correlation id.
    for i in range(len(secret) - 4):
        window = secret[i : i + 4]
        # Allow accidental hex coincidence, but the secret has chars outside
        # [0-9a-f] so any window with non-hex chars must not appear.
        if any(c not in "0123456789abcdef" for c in window.lower()):
            assert window not in cid


def test_log_corr_id_is_short_and_hex():
    cid = log_corr_id("any-state-value")
    assert len(cid) == 8
    assert all(c in "0123456789abcdef" for c in cid)


# ---------------------------------------------------------------------------
# Logging-discipline regressions (CWE-532): state values must not appear in logs
# ---------------------------------------------------------------------------


def test_validate_state_does_not_enumerate_active_states(db_session, oauth_user, caplog):
    """Previously: a missed-lookup logged ALL active state prefixes for the
    provider — an enumeration oracle. Now we log only the missing value's
    correlation id and an active-state count.
    """
    # Seed three in-flight legitimate states.
    legitimate_states = [generate_state() for _ in range(3)]
    for s in legitimate_states:
        store_state(db_session, s, "slack", oauth_user.id)

    # Bogus signed-state — well-formed enough to pass the dot-format check
    # but won't match anything in the DB.
    bogus = "no-such-state.deadbeefcafebabe"

    with caplog.at_level("WARNING", logger="memory.common.oauth_client"):
        result = validate_and_consume_state(db_session, bogus, "slack")

    assert result is None
    full_log = "\n".join(record.getMessage() for record in caplog.records)

    # No legitimate state value (or any prefix of it) appears in the logs.
    for s in legitimate_states:
        assert s not in full_log, "Active state values must not appear in logs"
        assert s[:8] not in full_log, "Even short prefixes leak — must not appear"

    # The active-states count IS logged (useful for ops, no info leak).
    assert "active_states_for_provider=3" in full_log


def test_validate_state_logs_missing_value_as_corr_id_only(db_session, caplog):
    """The missing-state's value should appear only as a correlation id."""
    # 'state' part is 'thiswillnotmatch'
    bogus_state = "thiswillnotmatch"
    bogus = f"{bogus_state}.signature1"

    with caplog.at_level("WARNING", logger="memory.common.oauth_client"):
        result = validate_and_consume_state(db_session, bogus, "slack")

    assert result is None
    full_log = "\n".join(record.getMessage() for record in caplog.records)

    # The raw state must NOT appear.
    assert bogus_state not in full_log
    # But its correlation id MUST appear (so two reports of the same bad
    # state can be correlated by an operator).
    assert log_corr_id(bogus_state) in full_log


def test_store_state_logs_corr_id_only(db_session, oauth_user, caplog):
    """store_state previously logged state[:16] of the unsigned state.
    Now it should log only a correlation id.
    """
    state = generate_state()
    with caplog.at_level("INFO", logger="memory.common.oauth_client"):
        store_state(db_session, state, "slack", oauth_user.id)

    full_log = "\n".join(record.getMessage() for record in caplog.records)
    assert state not in full_log
    assert state[:16] not in full_log
    assert log_corr_id(state) in full_log


def test_validate_signature_failure_uses_corr_ids_only(
    db_session, oauth_user, caplog
):
    """Tampered signature path must log corr ids, not state values."""
    state = generate_state()
    store_state(db_session, state, "slack", oauth_user.id)
    tampered = sign_state(state, oauth_user.id) + "XXX"  # break the signature

    with caplog.at_level("WARNING", logger="memory.common.oauth_client"):
        result = validate_and_consume_state(db_session, tampered, "slack")

    assert result is None
    full_log = "\n".join(record.getMessage() for record in caplog.records)
    assert state not in full_log
    assert state[:16] not in full_log


# ---------------------------------------------------------------------------
# Functional sanity (existing behavior, not regression-prone but worth pinning)
# ---------------------------------------------------------------------------


def test_validate_and_consume_state_happy_path(db_session, oauth_user):
    state = generate_state()
    store_state(db_session, state, "slack", oauth_user.id)
    signed = sign_state(state, oauth_user.id)

    user_id = validate_and_consume_state(db_session, signed, "slack")

    assert user_id == oauth_user.id
    # State is consumed (one-time use).
    rows = db_session.query(OAuthClientState).filter_by(state=state).all()
    assert rows == []


def test_verify_state_signature_rejects_tampered():
    state = generate_state()
    signed = sign_state(state, 42)

    # Right user, right signature → returns the original state.
    assert verify_state_signature(signed, 42) == state
    # Wrong user → None.
    assert verify_state_signature(signed, 43) is None
    # Tampered signature → None.
    assert verify_state_signature(signed + "X", 42) is None
    # Missing dot → None (malformed).
    assert verify_state_signature("nodot", 42) is None


def test_validate_state_expired(db_session, oauth_user):
    """Expired states must be rejected and cleaned up."""
    state = generate_state()
    expired = OAuthClientState(
        state=state,
        provider="slack",
        user_id=oauth_user.id,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db_session.add(expired)
    db_session.commit()

    signed = sign_state(state, oauth_user.id)
    assert validate_and_consume_state(db_session, signed, "slack") is None
    # Expired row was deleted.
    assert db_session.query(OAuthClientState).filter_by(state=state).count() == 0


def test_validate_state_consumes_atomically(db_session, oauth_user):
    """Regression: validate_and_consume_state must reject the second of two
    concurrent attempts.

    Previous implementation: SELECT-then-DELETE, where the SELECT could
    succeed on both calls (race window covered signature/expiry checks)
    so both completions happened. New impl: single ``DELETE ... RETURNING``
    so only the race-winner gets a row. We can't trivially simulate true
    concurrency in a single-process test, but a sequential second call
    with the same signed state gives the same observable outcome — the
    second call must return None and find no row to delete.
    """
    state = generate_state()
    store_state(db_session, state, "slack", oauth_user.id)
    signed = sign_state(state, oauth_user.id)

    # First call wins.
    first = validate_and_consume_state(db_session, signed, "slack")
    assert first == oauth_user.id

    # Second call (replay) must NOT succeed — the row was atomically
    # consumed by the first call.
    second = validate_and_consume_state(db_session, signed, "slack")
    assert second is None
    assert db_session.query(OAuthClientState).filter_by(state=state).count() == 0


def test_validate_state_with_bad_signature_consumes_row(db_session, oauth_user):
    """Brute-forcing the signature against a single token must not work:
    a single attempt consumes the row regardless of whether the signature
    verifies. This matches the previous behaviour (which also deleted on
    bad-signature paths) but now happens atomically.
    """
    state = generate_state()
    store_state(db_session, state, "slack", oauth_user.id)
    # Sign with the wrong user_id.
    bogus_signed = sign_state(state, oauth_user.id + 9999)

    assert validate_and_consume_state(db_session, bogus_signed, "slack") is None
    # The legitimate user can no longer use this state — it was consumed
    # by the brute-force attempt. (This is by design and matches the
    # pre-fix semantics; the alternative would let an attacker bombard
    # signatures with no cost.)
    assert db_session.query(OAuthClientState).filter_by(state=state).count() == 0
