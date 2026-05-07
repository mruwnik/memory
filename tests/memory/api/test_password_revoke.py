"""Tests for revoke_user_credentials called by change_password / reset_password.

Hermetic — uses MagicMock for the SQLAlchemy session so no Postgres needed.
End-to-end DB tests for change_password/reset_password behaviour are covered
by the existing test_users_api.py suite (which depends on db_session).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from memory.api.users import revoke_user_credentials


def _build_session(sessions_deleted: int, refresh_revoked: int):
    """Build a SQLAlchemy session double whose chained .query().filter()*.delete()
    and .update() return the requested row counts. Tracks how many filter()
    calls happened on the session-query path so we can assert on the
    keep_session_id behaviour."""
    db = MagicMock()

    session_query = MagicMock()
    session_query.filter.return_value = session_query
    session_query.delete.return_value = sessions_deleted

    refresh_query = MagicMock()
    refresh_query.filter.return_value = refresh_query
    refresh_query.update.return_value = refresh_revoked

    # First .query() call → UserSession; second → OAuthRefreshToken
    db.query.side_effect = [session_query, refresh_query]
    return db, session_query, refresh_query


def test_revoke_credentials_returns_counts():
    db, _, _ = _build_session(sessions_deleted=3, refresh_revoked=2)

    deleted, revoked = revoke_user_credentials(db, user_id=42)

    assert (deleted, revoked) == (3, 2)


def test_revoke_credentials_no_keep_session_uses_one_filter():
    """Admin reset path: only one filter() (user_id), no exclude-current-session."""
    db, session_query, _ = _build_session(sessions_deleted=5, refresh_revoked=0)

    revoke_user_credentials(db, user_id=42, keep_session_id=None)

    # One filter call on the UserSession query: user_id == 42
    assert session_query.filter.call_count == 1
    session_query.delete.assert_called_once_with(synchronize_session=False)


def test_revoke_credentials_with_keep_session_id_excludes_current():
    """Self-change path: a second filter excludes the user's current session."""
    db, session_query, _ = _build_session(sessions_deleted=2, refresh_revoked=1)

    revoke_user_credentials(db, user_id=42, keep_session_id="sess-keep")

    # Two filter calls: user_id == 42, then id != "sess-keep"
    assert session_query.filter.call_count == 2


def test_revoke_credentials_does_not_touch_api_keys():
    """API keys are intentionally preserved across password rotation
    (long-lived integration credentials with separate rotation cadence)."""
    db, _, _ = _build_session(sessions_deleted=0, refresh_revoked=0)

    revoke_user_credentials(db, user_id=42)

    # Exactly two queries — UserSession + OAuthRefreshToken. No third
    # query for APIKey.
    assert db.query.call_count == 2


def test_revoke_credentials_revokes_refresh_tokens_via_update():
    """OAuthRefreshToken rows get .revoked = True (no row deletion)."""
    db, _, refresh_query = _build_session(sessions_deleted=0, refresh_revoked=4)

    _, revoked = revoke_user_credentials(db, user_id=42)

    assert revoked == 4
    refresh_query.update.assert_called_once_with(
        {"revoked": True}, synchronize_session=False
    )
