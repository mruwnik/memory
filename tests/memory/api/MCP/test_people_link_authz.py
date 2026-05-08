# pyright: reportArgumentType=false
"""Unit tests for the identity-claim guards in people MCP helpers.

These tests exercise ``link_user_from_contact_info`` and
``link_discord_from_contact_info`` directly with mocked sessions so
they don't need a real Postgres. They verify the authorization gate
that prevents a low-privilege caller from auto-linking a Person to
another user's identity (CWE-863, identity-claim attack).

The ``# pyright: reportArgumentType=false`` pragma above silences the
duck-typing warnings: FakeSession satisfies the runtime contract used
by the helpers but is not a real ``sqlalchemy.orm.Session``.
"""

from unittest.mock import MagicMock

import pytest

from memory.api.MCP.servers import people as people_module
from memory.api.MCP.servers.people import (
    _caller_owns_person,
    link_discord_from_contact_info,
    link_user_from_contact_info,
)


def make_user(user_id: int, email: str = "user@example.com"):
    user = MagicMock()
    user.id = user_id
    user.email = email
    return user


def make_person(person_id: int = 1, identifier: str = "p", user_id: int | None = None):
    person = MagicMock()
    person.id = person_id
    person.identifier = identifier
    person.user_id = user_id
    return person


def make_discord_user(discord_id: int, person_id: int | None = None,
                     username: str = "alice", display_name: str = "Alice"):
    du = MagicMock()
    du.id = discord_id
    du.person_id = person_id
    du.username = username
    du.display_name = display_name
    return du


class FakeSession:
    """Minimal session that returns canned objects for query/get."""

    def __init__(self, *, user_by_email=None, discord_by_id=None,
                 discord_by_name=None, person_by_id=None):
        self._user_by_email = user_by_email
        self._discord_by_id = discord_by_id or {}
        self._discord_by_name = discord_by_name or {}
        self._person_by_id = person_by_id or {}

    def query(self, model):
        return _FakeQuery(self, model)

    def get(self, model, key):
        if model.__name__ == "DiscordUser":
            return self._discord_by_id.get(key)
        if model.__name__ == "Person":
            return self._person_by_id.get(key)
        return None


class _FakeQuery:
    def __init__(self, session, model):
        self._session = session
        self._model = model
        self._intent = None
        self._args = None

    def filter(self, *_conds):
        # Don't try to introspect SQLAlchemy expressions; we just
        # remember that filter was called and return self. The first()
        # call below decides what to return based on model type.
        return self

    def first(self):
        if self._model.__name__ == "User":
            return self._session._user_by_email
        if self._model.__name__ == "DiscordUser":
            # Pick whichever is set
            if self._session._discord_by_name:
                return next(iter(self._session._discord_by_name.values()), None)
            return None
        return None


# --- link_user_from_contact_info ---------------------------------------------


def test_link_user_no_contact_info():
    person = make_person()
    session = FakeSession()
    assert link_user_from_contact_info(
        session, person, None, caller_id=1
    ) is None


@pytest.mark.parametrize("contact_info", [
    {},
    {"email": ""},
    {"email": "   "},
    {"email": None},
    {"email": 42},
])
def test_link_user_invalid_email_returns_none(contact_info):
    person = make_person()
    session = FakeSession()
    assert link_user_from_contact_info(
        session, person, contact_info, caller_id=1
    ) is None
    assert person.user_id is None


def test_link_user_already_linked_returns_existing():
    person = make_person(user_id=99)
    session = FakeSession()  # no user_by_email — shouldn't matter
    result = link_user_from_contact_info(
        session, person, {"email": "anything@example.com"}, caller_id=1
    )
    assert result == 99
    assert person.user_id == 99  # unchanged


def test_link_user_no_match_returns_none():
    person = make_person()
    session = FakeSession(user_by_email=None)
    result = link_user_from_contact_info(
        session, person, {"email": "missing@example.com"}, caller_id=1
    )
    assert result is None
    assert person.user_id is None


def test_link_user_self_link_succeeds():
    """Caller can auto-link their own email to their Person."""
    caller = make_user(user_id=42, email="me@example.com")
    person = make_person()
    session = FakeSession(user_by_email=caller)
    result = link_user_from_contact_info(
        session, person, {"email": "me@example.com"}, caller_id=42
    )
    assert result == 42
    assert person.user_id == 42


def test_link_user_other_user_blocked_for_non_admin():
    """Non-admin caller cannot auto-link Person to someone else's User."""
    target = make_user(user_id=7, email="admin@example.com")
    person = make_person()
    session = FakeSession(user_by_email=target)
    # caller_id=42 != target.id=7
    result = link_user_from_contact_info(
        session, person, {"email": "admin@example.com"},
        caller_id=42, caller_is_admin=False,
    )
    assert result is None
    assert person.user_id is None


def test_link_user_other_user_allowed_for_admin():
    """Admin caller may auto-link Person to anyone's User."""
    target = make_user(user_id=7, email="alice@example.com")
    person = make_person()
    session = FakeSession(user_by_email=target)
    result = link_user_from_contact_info(
        session, person, {"email": "alice@example.com"},
        caller_id=42, caller_is_admin=True,
    )
    assert result == 7
    assert person.user_id == 7


def test_link_user_no_caller_id_blocked():
    """Anonymous-ish caller (caller_id=None) cannot auto-link."""
    target = make_user(user_id=7, email="alice@example.com")
    person = make_person()
    session = FakeSession(user_by_email=target)
    result = link_user_from_contact_info(
        session, person, {"email": "alice@example.com"},
        caller_id=None, caller_is_admin=False,
    )
    assert result is None
    assert person.user_id is None


def test_link_user_email_normalized_lowercase():
    """Email match runs case-insensitively (existing behaviour preserved)."""
    caller = make_user(user_id=5, email="x@example.com")
    person = make_person()
    session = FakeSession(user_by_email=caller)
    result = link_user_from_contact_info(
        session, person, {"email": "  X@Example.COM  "}, caller_id=5
    )
    assert result == 5


# --- link_discord_from_contact_info ------------------------------------------


def test_link_discord_no_contact_info():
    person = make_person()
    session = FakeSession()
    assert link_discord_from_contact_info(
        session, person, None, caller_id=1
    ) == []


def test_link_discord_no_discord_field():
    person = make_person()
    session = FakeSession()
    assert link_discord_from_contact_info(
        session, person, {"email": "x@y.com"}, caller_id=1
    ) == []


def test_link_discord_unexpected_type():
    person = make_person()
    session = FakeSession()
    # dict is not str|list -> warn + return []
    assert link_discord_from_contact_info(
        session, person, {"discord": {"weird": "shape"}}, caller_id=1
    ) == []


def test_link_discord_already_attached_to_target_person():
    """If discord_user.person_id already == person.id, treat as a no-op success."""
    person = make_person(person_id=10)
    discord = make_discord_user(discord_id=100, person_id=10)
    session = FakeSession(discord_by_id={100: discord})
    result = link_discord_from_contact_info(
        session, person, {"discord": "100"},
        caller_id=99, caller_is_admin=False,
    )
    assert result == [100]
    assert discord.person_id == 10  # unchanged


def test_link_discord_admin_can_reattribute_anyone():
    """Admin override: re-attribute someone else's Discord row freely."""
    person = make_person(person_id=10, user_id=99)
    discord = make_discord_user(discord_id=100, person_id=20)
    session = FakeSession(
        discord_by_id={100: discord},
        person_by_id={20: make_person(person_id=20, user_id=7)},
    )
    result = link_discord_from_contact_info(
        session, person, {"discord": "100"},
        caller_id=42, caller_is_admin=True,
    )
    assert result == [100]
    assert discord.person_id == 10


def test_link_discord_non_admin_blocked_when_target_person_unowned():
    """Non-admin can't link a discord row to a Person that isn't theirs."""
    person = make_person(person_id=10, user_id=None)  # unlinked
    discord = make_discord_user(discord_id=100, person_id=None)
    session = FakeSession(discord_by_id={100: discord})
    result = link_discord_from_contact_info(
        session, person, {"discord": "100"},
        caller_id=42, caller_is_admin=False,
    )
    assert result == []
    assert discord.person_id is None


def test_link_discord_non_admin_allowed_when_caller_owns_target_and_discord_unlinked():
    """Caller links their own unlinked Discord to their own Person."""
    caller_id = 42
    person = make_person(person_id=10, user_id=caller_id)  # caller owns
    discord = make_discord_user(discord_id=100, person_id=None)  # unlinked
    session = FakeSession(discord_by_id={100: discord})
    result = link_discord_from_contact_info(
        session, person, {"discord": "100"},
        caller_id=caller_id, caller_is_admin=False,
    )
    assert result == [100]
    assert discord.person_id == 10


def test_link_discord_non_admin_blocked_when_current_owner_is_other():
    """Caller cannot steal a Discord row currently attributed to someone else."""
    caller_id = 42
    person = make_person(person_id=10, user_id=caller_id)
    other_owner = make_person(person_id=20, user_id=99)
    discord = make_discord_user(discord_id=100, person_id=20)
    session = FakeSession(
        discord_by_id={100: discord},
        person_by_id={20: other_owner},
    )
    result = link_discord_from_contact_info(
        session, person, {"discord": "100"},
        caller_id=caller_id, caller_is_admin=False,
    )
    assert result == []
    assert discord.person_id == 20  # unchanged


def test_link_discord_non_admin_allowed_to_move_between_own_persons():
    """Caller can move their own Discord row between two Persons they own."""
    caller_id = 42
    target = make_person(person_id=10, user_id=caller_id)
    source = make_person(person_id=20, user_id=caller_id)
    discord = make_discord_user(discord_id=100, person_id=20)
    session = FakeSession(
        discord_by_id={100: discord},
        person_by_id={20: source},
    )
    result = link_discord_from_contact_info(
        session, target, {"discord": "100"},
        caller_id=caller_id, caller_is_admin=False,
    )
    assert result == [100]
    assert discord.person_id == 10


def test_link_discord_skips_missing_user():
    """When the discord_user lookup misses entirely, just skip."""
    person = make_person(person_id=10, user_id=42)
    session = FakeSession()  # no discord_by_id, no discord_by_name
    result = link_discord_from_contact_info(
        session, person, {"discord": ["999"]},
        caller_id=42, caller_is_admin=False,
    )
    assert result == []


def test_link_discord_skips_blank_identifier():
    person = make_person(person_id=10, user_id=42)
    session = FakeSession()
    result = link_discord_from_contact_info(
        session, person, {"discord": ["", "   "]},
        caller_id=42, caller_is_admin=False,
    )
    assert result == []


# --- _caller_owns_person ----------------------------------------------------


@pytest.mark.parametrize("user_id,caller_id,expected", [
    (None, 5, False),         # unlinked is never owned
    (5, 5, True),             # owner match
    (5, 7, False),            # different owner
    (5, None, False),         # no caller
    (None, None, False),      # both none
])
def test_caller_owns_person(user_id, caller_id, expected):
    person = make_person(user_id=user_id)
    assert _caller_owns_person(person, caller_id) is expected


# --- audit log emission ------------------------------------------------------


def test_link_user_logs_warning_on_block(caplog):
    target = make_user(user_id=7, email="admin@example.com")
    person = make_person()
    session = FakeSession(user_by_email=target)
    with caplog.at_level("WARNING", logger=people_module.__name__):
        link_user_from_contact_info(
            session, person, {"email": "admin@example.com"},
            caller_id=42, caller_is_admin=False,
        )
    assert any(
        "Refusing to auto-link Person" in record.message
        for record in caplog.records
    )


def test_link_discord_logs_warning_on_block(caplog):
    person = make_person(person_id=10, user_id=None)
    discord = make_discord_user(discord_id=100, person_id=None)
    session = FakeSession(discord_by_id={100: discord})
    with caplog.at_level("WARNING", logger=people_module.__name__):
        link_discord_from_contact_info(
            session, person, {"discord": "100"},
            caller_id=42, caller_is_admin=False,
        )
    assert any(
        "Refusing to re-attribute Discord user" in record.message
        for record in caplog.records
    )
