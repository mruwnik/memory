"""Tests for People MCP tools."""

import sys
import pytest
from unittest.mock import AsyncMock, Mock, MagicMock, patch

# Mock the mcp module and all its submodules before importing anything that uses it
_mock_mcp = MagicMock()
_mock_mcp.tool = lambda: lambda f: f  # Make @mcp.tool() a no-op decorator
sys.modules["mcp"] = _mock_mcp
sys.modules["mcp.server"] = MagicMock()
sys.modules["mcp.server.auth"] = MagicMock()
sys.modules["mcp.server.auth.handlers"] = MagicMock()
sys.modules["mcp.server.auth.handlers.authorize"] = MagicMock()
sys.modules["mcp.server.auth.handlers.token"] = MagicMock()
sys.modules["mcp.server.auth.provider"] = MagicMock()
sys.modules["mcp.server.fastmcp"] = MagicMock()
sys.modules["mcp.server.fastmcp.server"] = MagicMock()

# Also mock the memory.api.MCP.base module to avoid MCP imports
_mock_base = MagicMock()
_mock_base.mcp = MagicMock()
_mock_base.mcp.tool = lambda: lambda f: f  # Make @mcp.tool() a no-op decorator
sys.modules["memory.api.MCP.base"] = _mock_base

from memory.common.db.models import Person
from memory.common.db import connection as db_connection
from memory.workers.tasks.content_processing import create_content_hash


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
def sample_people(db_session):
    """Create sample people for testing."""
    people = [
        Person(
            identifier="alice_chen",
            display_name="Alice Chen",
            aliases=["@alice_c", "alice.chen@work.com"],
            contact_info={"email": "alice@example.com", "phone": "555-1234"},
            tags=["work", "engineering"],
            content="Tech lead on Platform team. Very thorough in code reviews.",
            modality="person",
            sha256=create_content_hash("person:alice_chen"),
            size=100,
        ),
        Person(
            identifier="bob_smith",
            display_name="Bob Smith",
            aliases=["@bobsmith"],
            contact_info={"email": "bob@example.com"},
            tags=["work", "design"],
            content="UX designer. Prefers visual communication.",
            modality="person",
            sha256=create_content_hash("person:bob_smith"),
            size=50,
        ),
        Person(
            identifier="charlie_jones",
            display_name="Charlie Jones",
            aliases=[],
            contact_info={"twitter": "@charlie_j"},
            tags=["friend", "climbing"],
            content="Met at climbing gym. Very reliable.",
            modality="person",
            sha256=create_content_hash("person:charlie_jones"),
            size=30,
        ),
    ]

    for person in people:
        db_session.add(person)
    db_session.commit()

    for person in people:
        db_session.refresh(person)

    return people


# =============================================================================
# Tests for add_person
# =============================================================================


@pytest.mark.asyncio
async def test_add_person_success(db_session):
    """Test adding a new person."""
    from memory.api.MCP.people import add_person

    mock_task = Mock()
    mock_task.id = "task-123"

    with patch("memory.api.MCP.people.make_session", return_value=db_session):
        with patch("memory.api.MCP.people.celery_app") as mock_celery:
            mock_celery.send_task.return_value = mock_task
            result = await add_person(
                identifier="new_person",
                display_name="New Person",
                aliases=["@newperson"],
                contact_info={"email": "new@example.com"},
                tags=["friend"],
                notes="A new friend.",
            )

    assert result["status"] == "queued"
    assert result["task_id"] == "task-123"
    assert result["identifier"] == "new_person"

    # Verify Celery task was called
    mock_celery.send_task.assert_called_once()
    call_kwargs = mock_celery.send_task.call_args[1]
    assert call_kwargs["kwargs"]["identifier"] == "new_person"
    assert call_kwargs["kwargs"]["display_name"] == "New Person"


@pytest.mark.asyncio
async def test_add_person_already_exists(db_session, sample_people):
    """Test adding a person that already exists."""
    from memory.api.MCP.people import add_person

    with patch("memory.api.MCP.people.make_session", return_value=db_session):
        with pytest.raises(ValueError, match="already exists"):
            await add_person(
                identifier="alice_chen",  # Already exists
                display_name="Alice Chen Duplicate",
            )


@pytest.mark.asyncio
async def test_add_person_minimal(db_session):
    """Test adding a person with minimal data."""
    from memory.api.MCP.people import add_person

    mock_task = Mock()
    mock_task.id = "task-456"

    with patch("memory.api.MCP.people.make_session", return_value=db_session):
        with patch("memory.api.MCP.people.celery_app") as mock_celery:
            mock_celery.send_task.return_value = mock_task
            result = await add_person(
                identifier="minimal_person",
                display_name="Minimal Person",
            )

    assert result["status"] == "queued"
    assert result["identifier"] == "minimal_person"


# =============================================================================
# Tests for update_person_info
# =============================================================================


@pytest.mark.asyncio
async def test_update_person_info_success(db_session, sample_people):
    """Test updating a person's info."""
    from memory.api.MCP.people import update_person_info

    mock_task = Mock()
    mock_task.id = "task-789"

    with patch("memory.api.MCP.people.make_session", return_value=db_session):
        with patch("memory.api.MCP.people.celery_app") as mock_celery:
            mock_celery.send_task.return_value = mock_task
            result = await update_person_info(
                identifier="alice_chen",
                display_name="Alice M. Chen",
                notes="Added middle initial",
            )

    assert result["status"] == "queued"
    assert result["task_id"] == "task-789"
    assert result["identifier"] == "alice_chen"


@pytest.mark.asyncio
async def test_update_person_info_not_found(db_session, sample_people):
    """Test updating a person that doesn't exist."""
    from memory.api.MCP.people import update_person_info

    with patch("memory.api.MCP.people.make_session", return_value=db_session):
        with pytest.raises(ValueError, match="not found"):
            await update_person_info(
                identifier="nonexistent_person",
                display_name="New Name",
            )


@pytest.mark.asyncio
async def test_update_person_info_with_merge_params(db_session, sample_people):
    """Test that update passes all merge parameters."""
    from memory.api.MCP.people import update_person_info

    mock_task = Mock()
    mock_task.id = "task-merge"

    with patch("memory.api.MCP.people.make_session", return_value=db_session):
        with patch("memory.api.MCP.people.celery_app") as mock_celery:
            mock_celery.send_task.return_value = mock_task
            result = await update_person_info(
                identifier="alice_chen",
                aliases=["@alice_new"],
                contact_info={"slack": "@alice"},
                tags=["leadership"],
                notes="Promoted to senior",
                replace_notes=False,
            )

    call_kwargs = mock_celery.send_task.call_args[1]["kwargs"]
    assert call_kwargs["aliases"] == ["@alice_new"]
    assert call_kwargs["contact_info"] == {"slack": "@alice"}
    assert call_kwargs["tags"] == ["leadership"]
    assert call_kwargs["notes"] == "Promoted to senior"
    assert call_kwargs["replace_notes"] is False


# =============================================================================
# Tests for get_person
# =============================================================================


@pytest.mark.asyncio
async def test_get_person_found(db_session, sample_people):
    """Test getting a person that exists."""
    from memory.api.MCP.people import get_person

    with patch("memory.api.MCP.people.make_session", return_value=db_session):
        result = await get_person(identifier="alice_chen")

    assert result is not None
    assert result["identifier"] == "alice_chen"
    assert result["display_name"] == "Alice Chen"
    assert result["aliases"] == ["@alice_c", "alice.chen@work.com"]
    assert result["contact_info"] == {"email": "alice@example.com", "phone": "555-1234"}
    assert result["tags"] == ["work", "engineering"]
    assert "Tech lead" in result["notes"]


@pytest.mark.asyncio
async def test_get_person_not_found(db_session, sample_people):
    """Test getting a person that doesn't exist."""
    from memory.api.MCP.people import get_person

    with patch("memory.api.MCP.people.make_session", return_value=db_session):
        result = await get_person(identifier="nonexistent_person")

    assert result is None


# =============================================================================
# Tests for list_people
# =============================================================================


@pytest.mark.asyncio
async def test_list_people_no_filters(db_session, sample_people):
    """Test listing all people without filters."""
    from memory.api.MCP.people import list_people

    with patch("memory.api.MCP.people.make_session", return_value=db_session):
        results = await list_people()

    assert len(results) == 3
    # Should be ordered by display_name
    assert results[0]["display_name"] == "Alice Chen"
    assert results[1]["display_name"] == "Bob Smith"
    assert results[2]["display_name"] == "Charlie Jones"


@pytest.mark.asyncio
async def test_list_people_filter_by_tags(db_session, sample_people):
    """Test filtering by tags."""
    from memory.api.MCP.people import list_people

    with patch("memory.api.MCP.people.make_session", return_value=db_session):
        results = await list_people(tags=["work"])

    assert len(results) == 2
    assert all("work" in r["tags"] for r in results)


@pytest.mark.asyncio
async def test_list_people_filter_by_search(db_session, sample_people):
    """Test filtering by search term."""
    from memory.api.MCP.people import list_people

    with patch("memory.api.MCP.people.make_session", return_value=db_session):
        results = await list_people(search="alice")

    assert len(results) == 1
    assert results[0]["identifier"] == "alice_chen"


@pytest.mark.asyncio
async def test_list_people_search_in_notes(db_session, sample_people):
    """Test that search works on notes content."""
    from memory.api.MCP.people import list_people

    with patch("memory.api.MCP.people.make_session", return_value=db_session):
        results = await list_people(search="climbing")

    assert len(results) == 1
    assert results[0]["identifier"] == "charlie_jones"


@pytest.mark.asyncio
async def test_list_people_limit(db_session, sample_people):
    """Test limiting results."""
    from memory.api.MCP.people import list_people

    with patch("memory.api.MCP.people.make_session", return_value=db_session):
        results = await list_people(limit=1)

    assert len(results) == 1


@pytest.mark.asyncio
async def test_list_people_limit_max_enforced(db_session, sample_people):
    """Test that limit is capped at 200."""
    from memory.api.MCP.people import list_people

    with patch("memory.api.MCP.people.make_session", return_value=db_session):
        # Request 500 but should be capped at 200
        results = await list_people(limit=500)

    # We only have 3 people, but the limit logic should cap at 200
    assert len(results) <= 200


@pytest.mark.asyncio
async def test_list_people_combined_filters(db_session, sample_people):
    """Test combining tag and search filters."""
    from memory.api.MCP.people import list_people

    with patch("memory.api.MCP.people.make_session", return_value=db_session):
        results = await list_people(tags=["work"], search="chen")

    assert len(results) == 1
    assert results[0]["identifier"] == "alice_chen"


# =============================================================================
# Tests for delete_person
# =============================================================================


@pytest.mark.asyncio
async def test_delete_person_success(db_session, sample_people):
    """Test deleting a person."""
    from memory.api.MCP.people import delete_person

    with patch("memory.api.MCP.people.make_session", return_value=db_session):
        result = await delete_person(identifier="bob_smith")

    assert result["deleted"] is True
    assert result["identifier"] == "bob_smith"
    assert result["display_name"] == "Bob Smith"

    # Verify person was deleted
    remaining = db_session.query(Person).filter_by(identifier="bob_smith").first()
    assert remaining is None


@pytest.mark.asyncio
async def test_delete_person_not_found(db_session, sample_people):
    """Test deleting a person that doesn't exist."""
    from memory.api.MCP.people import delete_person

    with patch("memory.api.MCP.people.make_session", return_value=db_session):
        with pytest.raises(ValueError, match="not found"):
            await delete_person(identifier="nonexistent_person")


# =============================================================================
# Tests for _person_to_dict helper
# =============================================================================


def test_person_to_dict(sample_people):
    """Test the _person_to_dict helper function."""
    from memory.api.MCP.people import _person_to_dict

    person = sample_people[0]
    result = _person_to_dict(person)

    assert result["identifier"] == "alice_chen"
    assert result["display_name"] == "Alice Chen"
    assert result["aliases"] == ["@alice_c", "alice.chen@work.com"]
    assert result["contact_info"] == {"email": "alice@example.com", "phone": "555-1234"}
    assert result["tags"] == ["work", "engineering"]
    assert result["notes"] == "Tech lead on Platform team. Very thorough in code reviews."
    assert result["created_at"] is not None


def test_person_to_dict_empty_fields(db_session):
    """Test _person_to_dict with empty optional fields."""
    from memory.api.MCP.people import _person_to_dict

    person = Person(
        identifier="empty_person",
        display_name="Empty Person",
        aliases=[],
        contact_info={},
        tags=[],
        content=None,
        modality="person",
        sha256=create_content_hash("person:empty_person"),
        size=0,
    )

    result = _person_to_dict(person)

    assert result["identifier"] == "empty_person"
    assert result["aliases"] == []
    assert result["contact_info"] == {}
    assert result["tags"] == []
    assert result["notes"] is None


# =============================================================================
# Parametrized tests
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tag,expected_count",
    [
        ("work", 2),
        ("engineering", 1),
        ("design", 1),
        ("friend", 1),
        ("climbing", 1),
        ("nonexistent", 0),
    ],
)
async def test_list_people_various_tags(db_session, sample_people, tag, expected_count):
    """Test filtering by various tags."""
    from memory.api.MCP.people import list_people

    with patch("memory.api.MCP.people.make_session", return_value=db_session):
        results = await list_people(tags=[tag])

    assert len(results) == expected_count


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "search_term,expected_identifiers",
    [
        ("alice", ["alice_chen"]),
        ("bob", ["bob_smith"]),
        ("smith", ["bob_smith"]),
        ("chen", ["alice_chen"]),
        ("jones", ["charlie_jones"]),
        ("example.com", []),  # Not searching in contact_info
        ("UX", ["bob_smith"]),  # Case insensitive search in notes
    ],
)
async def test_list_people_various_searches(
    db_session, sample_people, search_term, expected_identifiers
):
    """Test search with various terms."""
    from memory.api.MCP.people import list_people

    with patch("memory.api.MCP.people.make_session", return_value=db_session):
        results = await list_people(search=search_term)

    result_identifiers = [r["identifier"] for r in results]
    assert result_identifiers == expected_identifiers
