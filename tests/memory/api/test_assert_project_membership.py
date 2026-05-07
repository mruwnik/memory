"""Tests for the auth.assert_project_membership helper.

Hermetic — uses MagicMock for the DB session so no Postgres is needed.
DB-backed integration tests already exist for the affected endpoints.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from memory.api.auth import assert_project_membership


def _user(scopes: list[str], user_id: int = 1) -> MagicMock:
    user = MagicMock()
    user.id = user_id
    user.scopes = scopes
    return user


def test_none_project_is_no_op():
    """project_id=None is the API's "don't change"/"unset" sentinel."""
    db = MagicMock()
    user = _user(scopes=["read"])
    assert_project_membership(db, user, None)
    db.query.assert_not_called()


def test_admin_bypasses_membership_check():
    """Admins (scopes=['*']) can place content into any project."""
    db = MagicMock()
    user = _user(scopes=["*"])
    with patch("memory.api.auth.get_user_project_roles") as roles:
        assert_project_membership(db, user, 999)
        roles.assert_not_called()


def test_member_allowed():
    db = MagicMock()
    user = _user(scopes=["read"], user_id=42)
    with patch(
        "memory.api.auth.get_user_project_roles",
        return_value={7: "manager", 99: "contributor"},
    ):
        # No exception
        assert_project_membership(db, user, 7)
        assert_project_membership(db, user, 99)


def test_non_member_403():
    db = MagicMock()
    user = _user(scopes=["read"], user_id=42)
    with patch("memory.api.auth.get_user_project_roles", return_value={7: "manager"}):
        with pytest.raises(HTTPException) as exc_info:
            assert_project_membership(db, user, 8)
    assert exc_info.value.status_code == 403
    assert "not a member" in exc_info.value.detail.lower()


def test_user_with_no_projects_blocked():
    db = MagicMock()
    user = _user(scopes=["read"], user_id=42)
    with patch("memory.api.auth.get_user_project_roles", return_value={}):
        with pytest.raises(HTTPException) as exc_info:
            assert_project_membership(db, user, 1)
    assert exc_info.value.status_code == 403


@pytest.mark.parametrize(
    "role",
    ["contributor", "manager", "admin", "owner"],
)
def test_any_membership_role_passes(role: str):
    """The check is membership, not role-level — role-vs-sensitivity is
    enforced at read time elsewhere. Any role on the project is enough to
    write into it."""
    db = MagicMock()
    user = _user(scopes=["read"], user_id=1)
    with patch(
        "memory.api.auth.get_user_project_roles", return_value={5: role}
    ):
        assert_project_membership(db, user, 5)
