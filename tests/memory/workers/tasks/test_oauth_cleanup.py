"""cleanup_expired_sessions prunes expired sessions, abandoned OAuth requests and unused clients."""

from datetime import datetime, timedelta, timezone

import pytest

from memory.common.db.models import (
    OAuthClientInformation,
    OAuthRefreshToken,
    OAuthState,
    UserSession,
)
from memory.workers.tasks.maintenance import cleanup_expired_sessions

def utc_now() -> datetime:
    """Naive UTC now, read when each row is built (not at import: a long run outlives short expiries)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def add_client(db_session, client_id: str, age_days: int) -> OAuthClientInformation:
    issued = datetime.now(timezone.utc) - timedelta(days=age_days)
    client = OAuthClientInformation(
        client_id=client_id,
        client_id_issued_at=issued.timestamp(),
        redirect_uris=["http://localhost/cb"],
        token_endpoint_auth_method="none",
        grant_types=["authorization_code"],
        response_types=["code"],
        scope="read write",
        client_name="",
    )
    db_session.add(client)
    db_session.flush()
    return client


def add_state(db_session, client_id: str, user_id: int | None, expires_in: timedelta) -> OAuthState:
    state = OAuthState(
        state=f"state-{client_id}-{user_id}-{expires_in.total_seconds()}",
        client_id=client_id,
        user_id=user_id,
        redirect_uri="http://localhost/cb",
        redirect_uri_provided_explicitly=True,
        code_challenge="x" * 43,
        scopes=["read"],
        expires_at=utc_now() + expires_in,
    )
    db_session.add(state)
    db_session.flush()
    return state


@pytest.mark.parametrize(
    "logged_in, expires_in, kept",
    [
        (False, timedelta(minutes=-1), False),
        (False, timedelta(minutes=5), True),
        (True, timedelta(minutes=-1), True),
    ],
)
def test_only_expired_requests_nobody_logged_in_through_are_pruned(
    db_session, admin_user, logged_in, expires_in, kept
):
    add_client(db_session, "c", age_days=0)
    state_id = add_state(db_session, "c", admin_user.id * logged_in or None, expires_in).id

    cleanup_expired_sessions()

    db_session.expire_all()
    assert (db_session.get(OAuthState, state_id) is not None) == kept


ABANDONED = (False, timedelta(minutes=-1))
PENDING = (False, timedelta(minutes=5))
COMPLETED = (True, timedelta(days=-60))


@pytest.mark.parametrize(
    "age_days, states, refresh_tokens, kept",
    [
        (31, [], 0, False),
        (31, [ABANDONED], 0, False),
        (29, [], 0, True),
        (31, [PENDING], 0, True),
        (31, [COMPLETED], 0, True),
        (31, [], 1, True),
    ],
)
def test_clients_that_never_completed_a_login_are_pruned_after_the_grace_period(
    db_session, admin_user, age_days, states, refresh_tokens, kept
):
    add_client(db_session, "c", age_days=age_days)
    for logged_in, expires_in in states:
        add_state(db_session, "c", admin_user.id * logged_in or None, expires_in)
    db_session.add_all(
        OAuthRefreshToken(client_id="c", user_id=admin_user.id, scopes=["read"], expires_at=utc_now())
        for _ in range(refresh_tokens)
    )
    db_session.flush()

    cleanup_expired_sessions()

    db_session.expire_all()
    assert (db_session.get(OAuthClientInformation, "c") is not None) == kept


def test_expired_sessions_are_deleted(db_session, admin_user):
    live = UserSession(user_id=admin_user.id, expires_at=utc_now() + timedelta(hours=1))
    expired = UserSession(user_id=admin_user.id, expires_at=utc_now() - timedelta(hours=1))
    db_session.add_all([live, expired])
    db_session.flush()
    live_id, expired_id = live.id, expired.id

    result = cleanup_expired_sessions()

    db_session.expire_all()
    assert db_session.get(UserSession, live_id) is not None
    assert db_session.get(UserSession, expired_id) is None
    assert result["sessions"] >= 1
