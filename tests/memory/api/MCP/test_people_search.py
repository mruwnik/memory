"""people_list_all search across names, contact info, linked accounts and tidbits."""

import pytest

from memory.api.MCP.servers.people import list_all
from memory.common.content_processing import create_content_hash
from memory.common.db.models import Person, PersonTidbit
from memory.common.db.models.discord import DiscordUser
from memory.common.db.models.sources import GithubUser
from tests.conftest import mcp_auth_context

SESSIONS = ["admin_session", "user_session"]


@pytest.fixture
def linked_person(db_session):
    """A Person whose handles live only in contact_info and linked account rows."""
    person = Person(
        identifier="alexb",
        display_name="Alexander Brightwater",
        contact_info={
            "email": ["alex.personal@gmail.com", "abright@work.example"],
            "phone": "+1-555-0100",
            "discord": 424242424242,
            "slack": {
                "T01": {"user_id": "U0SLACK99", "username": "slackhandle", "display_name": "Sash Slacks"}
            },
        },
    )
    bystander = Person(identifier="zed", display_name="Zed Other")
    db_session.add_all([person, bystander])
    db_session.flush()
    db_session.add_all(
        [
            DiscordUser(
                id=424242424242, username="emptykent", display_name="Hollow Monarch", person_id=person.id
            ),
            DiscordUser(id=777, username="orphanhandle", display_name="Nobody Linked"),
            GithubUser(
                id=9001,
                username="lunarcoder",
                display_name="Sasha Bright",
                email="gh-mail@noreply.example",
                person_id=person.id,
            ),
        ]
    )
    db_session.commit()
    return person


async def search_people(session_token: str, **kwargs) -> list[str]:
    with mcp_auth_context(session_token):
        result = await list_all.fn(**kwargs)
    return [p["identifier"] for p in result]


@pytest.mark.asyncio
@pytest.mark.parametrize("session_fixture", SESSIONS)
@pytest.mark.parametrize(
    "search",
    [
        "alex.personal@gmail.com",  # email, second shape: list element
        "ABRIGHT@WORK",  # email, case-insensitive substring
        "555-0100",  # phone
        "424242424242",  # numeric Discord id in contact_info
        "U0SLACK99",  # Slack user id
        "slackhandle",  # Slack username
        "sash slacks",  # Slack display name
        "emptykent",  # Discord username
        "hollow monarch",  # Discord display name
        "lunarcoder",  # GitHub login
        "Sasha Bright",  # GitHub display name
        "gh-mail@noreply",  # GitHub email
        "Brightwater",  # Person display name
        "alexb",  # Person identifier
    ],
)
async def test_list_all_search_matches_contact_and_linked_accounts(
    request, db_session, linked_person, session_fixture, search
):
    token = request.getfixturevalue(session_fixture).token
    assert await search_people(token, search=search) == ["alexb"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "search",
    [
        "phone",  # a contact_info key, not a value
        "user_id",
        "T01",  # a Slack workspace id, also a key
        "orphanhandle",  # a Discord account linked to no Person
        "Nobody Linked",
    ],
)
async def test_list_all_search_ignores_keys_and_unlinked_accounts(
    db_session, admin_session, linked_person, search
):
    assert await search_people(admin_session.token, search=search) == []


@pytest.mark.asyncio
async def test_list_all_search_returns_each_person_once(db_session, admin_session):
    person = Person(
        identifier="dup",
        display_name="Dupe Person",
        aliases=["dupe-alias"],
        contact_info={"email": ["dupe@a.example", "dupe@b.example"]},
    )
    db_session.add(person)
    db_session.flush()
    db_session.add_all(
        [
            DiscordUser(id=1001, username="dupe1", display_name="Dupe One", person_id=person.id),
            DiscordUser(id=1002, username="dupe2", display_name="Dupe Two", person_id=person.id),
            GithubUser(id=1003, username="dupe-gh", person_id=person.id),
        ]
    )
    db_session.commit()

    assert await search_people(admin_session.token, search="dupe") == ["dup"]


@pytest.fixture
def person_with_tidbit(db_session, admin_user):
    """A Person whose only mention of the search term is a tidbit, at a parametrized sensitivity."""

    def make(sensitivity: str) -> Person:
        person = Person(identifier="secretive", display_name="Secretive Person")
        db_session.add(person)
        db_session.flush()
        content = "Planning to resign in March"
        db_session.add(
            PersonTidbit(
                person_id=person.id,
                content=content,
                tidbit_type="note",
                tags=["confidential-hr"],
                sensitivity=sensitivity,
                creator_id=admin_user.id,
                modality="text",
                sha256=create_content_hash(content),
            )
        )
        db_session.commit()
        return person

    return make


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session_fixture, sensitivity, expected",
    [
        ("admin_session", "basic", ["secretive"]),
        ("user_session", "basic", []),
        ("user_session", "public", ["secretive"]),
        ("admin_session", "hidden", []),
    ],
)
@pytest.mark.parametrize(
    "filters",
    [{"search": "resign in march"}, {"tags": ["confidential-hr"]}],
)
async def test_list_all_tidbit_matches_only_readable_tidbits(
    request, db_session, person_with_tidbit, session_fixture, sensitivity, expected, filters
):
    person_with_tidbit(sensitivity)
    token = request.getfixturevalue(session_fixture).token
    assert await search_people(token, **filters) == expected


@pytest.mark.asyncio
async def test_list_all_tidbit_readable_by_its_creator(db_session, regular_user, user_session):
    person = Person(identifier="mine", display_name="My Contact")
    db_session.add(person)
    db_session.flush()
    content = "Owes me lunch"
    db_session.add(
        PersonTidbit(
            person_id=person.id,
            content=content,
            tidbit_type="note",
            creator_id=regular_user.id,
            modality="text",
            sha256=create_content_hash(content),
        )
    )
    db_session.commit()

    assert await search_people(user_session.token, search="owes me lunch") == ["mine"]
