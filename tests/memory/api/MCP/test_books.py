"""Tests for MCP books server."""
# pyright: reportFunctionMemberAccess=false

import pytest

from memory.api.MCP.servers.books import fetch, list_books
from memory.common.content_processing import create_content_hash
from memory.common.db import connection as db_connection
from memory.common.db.models import Book, BookSection, HumanUser, JournalEntry, Person, Team
from memory.common.db.models.sources import Project, project_teams, team_members
from tests.conftest import mcp_auth_context


@pytest.fixture(autouse=True)
def reset_db_cache():
    """Reset the cached database engine between tests."""
    db_connection._engine = None
    db_connection._session_factory = None
    db_connection._scoped_session = None
    yield
    db_connection._engine = None
    db_connection._session_factory = None
    db_connection._scoped_session = None


@pytest.fixture
def other_user(db_session):
    user = HumanUser(
        name="Other", email="other@example.com", password_hash="x", scopes=[]
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def projects(db_session, regular_user):
    """regular_user is a team member (contributor) of "member"; "other" has no link to them."""
    person = Person(identifier="regular", display_name="Regular")
    db_session.add(person)
    db_session.flush()
    regular_user.person = person

    member, other = Project(title="Member", state="open"), Project(title="Other", state="open")
    team = Team(name="Team", slug="team", is_active=True)
    db_session.add_all([member, other, team])
    db_session.flush()
    db_session.execute(
        team_members.insert().values(team_id=team.id, person_id=person.id, role="member")
    )
    db_session.execute(project_teams.insert().values(project_id=member.id, team_id=team.id))
    db_session.commit()
    return {"member": member.id, "other": other.id, None: None}


@pytest.fixture
def viewers(admin_user, regular_user, other_user, admin_session, user_session):
    users = {"admin": admin_user, "regular": regular_user, "other": other_user}
    tokens = {
        "admin": (admin_session.id, None),
        "admin read-only key": (admin_session.id, ["read"]),
        "regular": (user_session.id, None),
    }
    return users, tokens


def add_book(db_session, title="Book", **book_fields):
    book = Book(title=title, **book_fields)
    db_session.add(book)
    db_session.flush()
    return book


def add_section(db_session, book, title="Chapter", parent=None, **access):
    section = BookSection(
        book_id=book.id,
        section_title=title,
        modality="book",
        sha256=create_content_hash(f"{book.title} {title}"),
        content=f"{title} content",
        parent_section_id=parent and parent.id,
        **access,
    )
    db_session.add(section)
    db_session.flush()
    return section


TITLE = "Moby Dick"


def add_matrix_book(db_session, case, users, projects):
    project, sensitivity, creator = case
    book = add_book(db_session, TITLE)
    add_section(
        db_session,
        book,
        TITLE,
        project_id=projects[project],
        sensitivity=sensitivity,
        creator_id=users[creator].id if creator else None,
    )
    db_session.commit()


VISIBLE, DENIED = [TITLE], []
ACCESS_CASES = [
    # (viewer, (project, sensitivity, creator), expected)
    ("admin", ("other", "confidential", "other"), VISIBLE),
    ("admin", (None, "basic", None), VISIBLE),
    ("admin", ("other", "hidden", None), DENIED),
    ("admin read-only key", ("other", "confidential", "other"), DENIED),
    ("admin read-only key", (None, "basic", "admin"), VISIBLE),
    ("admin read-only key", ("other", "public", "other"), VISIBLE),
    ("regular", (None, "basic", "regular"), VISIBLE),
    ("regular", ("other", "public", "other"), VISIBLE),
    ("regular", ("member", "basic", "other"), VISIBLE),
    ("regular", ("member", "confidential", "other"), DENIED),
    ("regular", ("other", "basic", "other"), DENIED),
    ("regular", (None, "basic", "other"), DENIED),
    ("regular", ("member", "hidden", "regular"), DENIED),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("viewer, case, expected", ACCESS_CASES)
async def test_list_books_access(db_session, viewers, projects, viewer, case, expected):
    users, tokens = viewers
    add_matrix_book(db_session, case, users, projects)

    with mcp_auth_context(*tokens[viewer]):
        result = await list_books.fn()

    assert [b["title"] for b in result] == expected


@pytest.mark.parametrize("viewer, case, expected", ACCESS_CASES)
def test_fetch_access(db_session, viewers, projects, viewer, case, expected):
    users, tokens = viewers
    add_matrix_book(db_session, case, users, projects)
    book_id = db_session.query(Book.id).scalar()

    with mcp_auth_context(*tokens[viewer]):
        result = fetch.fn(book_id=book_id)

    assert [s["section_title"] for s in result] == expected


@pytest.mark.asyncio
async def test_list_and_fetch_return_only_accessible_sections_in_order(
    db_session, user_session, projects
):
    book = add_book(db_session)
    for title, level, number in [("1.2", 2, 2), ("1.1", 2, 1), ("Ch 2", 1, 2), ("Ch 1", 1, 1)]:
        add_section(
            db_session, book, title, sensitivity="public", section_level=level, section_number=number
        )
    add_section(
        db_session, book, "Secret", project_id=projects["other"], sensitivity="basic"
    )
    db_session.commit()

    with mcp_auth_context(user_session.id):
        listed = await list_books.fn(sections=True)
        fetched = fetch.fn(book_id=book.id)

    titles = ["Ch 1", "Ch 2", "1.1", "1.2"]
    assert [s["section_title"] for s in listed[0]["sections"]] == titles
    assert sorted(s["section_title"] for s in fetched) == sorted(titles)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filters, expected",
    [
        ({}, ["Go Basics", "Python Programming"]),
        ({"title": "python"}, ["Python Programming"]),
        ({"author": "jane"}, ["Go Basics"]),
        ({"tags": ["go", "rust"]}, ["Go Basics"]),
        ({"title": "%"}, []),
        ({"limit": 1, "offset": 1}, ["Python Programming"]),
    ],
)
async def test_list_books_filters(db_session, admin_session, filters, expected):
    for title, author, tags in [
        ("Python Programming", "John Doe", ["python"]),
        ("Go Basics", "Jane Smith", ["go"]),
    ]:
        add_section(db_session, add_book(db_session, title, author=author, tags=tags))
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await list_books.fn(**filters)

    assert [b["title"] for b in result] == expected


def test_fetch_returns_leaf_sections(db_session, admin_session):
    book = add_book(db_session)
    part = add_section(db_session, book, "Part 1")
    chapter = add_section(db_session, book, "Chapter 1", parent=part)
    leaf = add_section(db_session, book, "Section 1.1", parent=chapter)
    other_part = add_section(db_session, book, "Part 2")
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = fetch.fn(book_id=book.id)
        only = fetch.fn(book_id=book.id, sections=[leaf.id])

    assert {s["source_id"] for s in result} == {leaf.id, other_part.id}
    assert [s["source_id"] for s in only] == [leaf.id]


def test_fetch_with_include_journal(db_session, admin_user, admin_session):
    book = add_book(db_session)
    section = add_section(db_session, book, "Chapter 1")
    db_session.add(
        JournalEntry(
            target_type="source_item",
            target_id=section.id,
            creator_id=admin_user.id,
            content="Note about this book",
        )
    )
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = fetch.fn(book_id=book.id, include_journal=True)

    assert [s["section_title"] for s in result["sections"]] == ["Chapter 1"]
    assert [e["content"] for e in result["journal_entries"]] == ["Note about this book"]
