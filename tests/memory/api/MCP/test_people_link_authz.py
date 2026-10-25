# pyright: reportArgumentType=false
"""Unit tests for ``link_discord_from_contact_info`` with a mocked session, so
they don't need a real Postgres. Who may call it is tested in test_people.py.

The ``# pyright: reportArgumentType=false`` pragma above silences the
duck-typing warnings: FakeSession satisfies the runtime contract used
by the helpers but is not a real ``sqlalchemy.orm.Session``.
"""

from unittest.mock import MagicMock

import pytest

from memory.api.MCP.servers.people import link_discord_from_contact_info


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

    def __init__(self, *, discord_by_id=None, discord_by_name=None, person_by_id=None):
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
        if self._model.__name__ == "DiscordUser":
            # Pick whichever is set
            if self._session._discord_by_name:
                return next(iter(self._session._discord_by_name.values()), None)
            return None
        return None


# --- link_discord_from_contact_info ------------------------------------------


@pytest.mark.parametrize(
    "contact_info",
    [None, {"email": "x@y.com"}, {"discord": {"weird": "shape"}}, {"discord": ["", "   "]}],
)
def test_link_discord_ignores_missing_or_malformed_entries(contact_info):
    assert link_discord_from_contact_info(FakeSession(), make_person(), contact_info) == []


def test_link_discord_already_attached_to_target_person():
    """If discord_user.person_id already == person.id, treat as a no-op success."""
    person = make_person(person_id=10)
    discord = make_discord_user(discord_id=100, person_id=10)
    session = FakeSession(discord_by_id={100: discord})
    assert link_discord_from_contact_info(session, person, {"discord": "100"}) == [100]
    assert discord.person_id == 10


def test_link_discord_reattributes_from_another_person():
    person = make_person(person_id=10, user_id=99)
    discord = make_discord_user(discord_id=100, person_id=20)
    session = FakeSession(discord_by_id={100: discord})
    assert link_discord_from_contact_info(session, person, {"discord": "100"}) == [100]
    assert discord.person_id == 10


def test_link_discord_skips_missing_user():
    session = FakeSession()
    assert link_discord_from_contact_info(session, make_person(), {"discord": ["999"]}) == []
