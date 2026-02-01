"""Tests for People MCP tools."""

import sys
import pytest
from unittest.mock import Mock, MagicMock, patch

# Mock FastMCP - this creates a decorator factory that passes through the function unchanged
class MockFastMCP:
    def __init__(self, name):
        self.name = name

    def tool(self):
        def decorator(func):
            return func
        return decorator


# Mock the fastmcp module before importing anything that uses it
_mock_fastmcp = MagicMock()
_mock_fastmcp.FastMCP = MockFastMCP
sys.modules["fastmcp"] = _mock_fastmcp

# Mock the mcp module and all its submodules
_mock_mcp = MagicMock()
_mock_mcp.tool = lambda: lambda f: f
sys.modules["mcp"] = _mock_mcp
sys.modules["mcp.types"] = MagicMock()
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
_mock_base.mcp.tool = lambda: lambda f: f
sys.modules["memory.api.MCP.base"] = _mock_base

from memory.common.db.models import Person, PersonTidbit  # noqa: E402
from memory.common.db import connection as db_connection  # noqa: E402
from memory.common.content_processing import create_content_hash  # noqa: E402


def get_fn(tool):  # type: ignore[no-untyped-def]
    """Extract underlying function from FunctionTool if wrapped, else return as-is."""
    return getattr(tool, "fn", tool)


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
def sample_people(db_session, qdrant):
    """Create sample people for testing.

    With the new architecture:
    - Person is a thin identity record (no tags, content, etc.)
    - PersonTidbit holds the actual content with access control

    Note: Tidbits are created directly in the session without going through
    process_content_item, so they won't have embeddings. Tests requiring
    vector search won't find these tidbits.
    """
    people = []

    # Create Alice with tidbits
    alice = Person(
        identifier="alice_chen",
        display_name="Alice Chen",
        aliases=["@alice_c", "alice.chen@work.com"],
        contact_info={"email": "alice@example.com", "phone": "555-1234"},
    )
    db_session.add(alice)
    db_session.flush()

    alice_tidbit = PersonTidbit(
        person_id=alice.id,
        tidbit_type="note",
        content="Tech lead on Platform team. Very thorough in code reviews.",
        tags=["work", "engineering"],
        modality="person_tidbit",
        mime_type="text/plain",
        sha256=create_content_hash("tidbit:alice_chen:note"),
        size=100,
        sensitivity="basic",
    )
    db_session.add(alice_tidbit)
    people.append(alice)

    # Create Bob with tidbits
    bob = Person(
        identifier="bob_smith",
        display_name="Bob Smith",
        aliases=["@bobsmith"],
        contact_info={"email": "bob@example.com"},
    )
    db_session.add(bob)
    db_session.flush()

    bob_tidbit = PersonTidbit(
        person_id=bob.id,
        tidbit_type="note",
        content="UX designer. Prefers visual communication.",
        tags=["work", "design"],
        modality="person_tidbit",
        mime_type="text/plain",
        sha256=create_content_hash("tidbit:bob_smith:note"),
        size=50,
        sensitivity="basic",
    )
    db_session.add(bob_tidbit)
    people.append(bob)

    # Create Charlie with tidbits
    charlie = Person(
        identifier="charlie_jones",
        display_name="Charlie Jones",
        aliases=[],
        contact_info={"twitter": "@charlie_j"},
    )
    db_session.add(charlie)
    db_session.flush()

    charlie_tidbit = PersonTidbit(
        person_id=charlie.id,
        tidbit_type="note",
        content="Met at climbing gym. Very reliable.",
        tags=["friend", "climbing"],
        modality="person_tidbit",
        mime_type="text/plain",
        sha256=create_content_hash("tidbit:charlie_jones:note"),
        size=30,
        sensitivity="basic",
    )
    db_session.add(charlie_tidbit)
    people.append(charlie)

    db_session.commit()

    for person in people:
        db_session.refresh(person)

    return people


# =============================================================================
# Tests for add_person
# =============================================================================


@pytest.mark.asyncio
async def test_add_person_success(db_session, qdrant):
    """Test adding a new person.

    With the new synchronous design:
    - Person is created directly in the database
    - If content is provided, a tidbit task is queued
    """
    from memory.api.MCP.servers.people import add

    add_fn = get_fn(add)
    mock_task = Mock()
    mock_task.id = "task-123"

    with patch("memory.api.MCP.servers.people.make_session", return_value=db_session):
        with patch("memory.api.MCP.servers.people.celery_app") as mock_celery:
            mock_celery.send_task.return_value = mock_task
            result = await add_fn(
                identifier="new_person",
                display_name="New Person",
                aliases=["@newperson"],
                contact_info={"email": "new@example.com"},
                content="A new friend.",
                tags=["friend"],
            )

    # Person is created synchronously
    assert result["status"] == "created"
    assert result["identifier"] == "new_person"
    assert "person_id" in result

    # Content triggers a tidbit task
    assert result["tidbit_task_id"] == "task-123"

    # Verify Celery task was called for the tidbit
    mock_celery.send_task.assert_called_once()
    call_kwargs = mock_celery.send_task.call_args[1]
    assert call_kwargs["kwargs"]["content"] == "A new friend."
    assert call_kwargs["kwargs"]["tags"] == ["friend"]


@pytest.mark.asyncio
async def test_add_person_already_exists(db_session, sample_people):
    """Test adding a person that already exists."""
    from memory.api.MCP.servers.people import add

    add_fn = get_fn(add)
    with patch("memory.api.MCP.servers.people.make_session", return_value=db_session):
        with pytest.raises(ValueError, match="already exists"):
            await add_fn(
                identifier="alice_chen",  # Already exists
                display_name="Alice Chen Duplicate",
            )


@pytest.mark.asyncio
async def test_add_person_minimal(db_session, qdrant):
    """Test adding a person with minimal data (no content, no tidbit)."""
    from memory.api.MCP.servers.people import add

    add_fn = get_fn(add)

    with patch("memory.api.MCP.servers.people.make_session", return_value=db_session):
        with patch("memory.api.MCP.servers.people.celery_app") as mock_celery:
            result = await add_fn(
                identifier="minimal_person",
                display_name="Minimal Person",
            )

    # Person is created synchronously
    assert result["status"] == "created"
    assert result["identifier"] == "minimal_person"
    assert "person_id" in result

    # No content means no tidbit task
    assert "tidbit_task_id" not in result
    mock_celery.send_task.assert_not_called()


# =============================================================================
# Tests for update
# =============================================================================


@pytest.mark.asyncio
async def test_update_person_info_success(db_session, sample_people):
    """Test updating a person's identity info.

    With the new synchronous design, update happens directly in the database.
    """
    from memory.api.MCP.servers.people import update

    update_fn = get_fn(update)

    with patch("memory.api.MCP.servers.people.make_session", return_value=db_session):
        result = await update_fn(
            identifier="alice_chen",
            display_name="Alice M. Chen",
        )

    assert result["status"] == "updated"
    assert result["identifier"] == "alice_chen"
    assert "person_id" in result

    # Verify the update was applied
    alice = db_session.query(Person).filter(Person.identifier == "alice_chen").first()
    assert alice.display_name == "Alice M. Chen"


@pytest.mark.asyncio
async def test_update_person_info_not_found(db_session, sample_people):
    """Test updating a person that doesn't exist."""
    from memory.api.MCP.servers.people import update

    update_fn = get_fn(update)
    with patch("memory.api.MCP.servers.people.make_session", return_value=db_session):
        with pytest.raises(ValueError, match="not found"):
            await update_fn(
                identifier="nonexistent_person",
                display_name="New Name",
            )


@pytest.mark.asyncio
async def test_update_person_info_with_merge_params(db_session, sample_people):
    """Test that update merges aliases and contact_info correctly."""
    from memory.api.MCP.servers.people import update

    update_fn = get_fn(update)

    with patch("memory.api.MCP.servers.people.make_session", return_value=db_session):
        result = await update_fn(
            identifier="alice_chen",
            aliases=["@alice_new"],
            contact_info={"slack": "@alice"},
        )

    assert result["status"] == "updated"

    # Verify the merge was applied
    alice = db_session.query(Person).filter(Person.identifier == "alice_chen").first()
    # Aliases should be merged (union of old and new)
    assert "@alice_new" in alice.aliases
    assert "@alice_c" in alice.aliases  # Original alias preserved
    # Contact info should be deep merged
    assert alice.contact_info["slack"] == "@alice"
    assert alice.contact_info["email"] == "alice@example.com"  # Original preserved


# =============================================================================
# Tests for get_person
# =============================================================================


@pytest.mark.asyncio
async def test_get_person_found(db_session, sample_people):
    """Test getting a person that exists."""
    from memory.api.MCP.servers.people import get_person

    get_person_fn = get_fn(get_person)
    with patch("memory.api.MCP.servers.people.make_session", return_value=db_session):
        result = await get_person_fn(identifier="alice_chen")

    assert result is not None
    assert result["identifier"] == "alice_chen"
    assert result["display_name"] == "Alice Chen"
    assert result["aliases"] == ["@alice_c", "alice.chen@work.com"]
    assert result["contact_info"] == {"email": "alice@example.com", "phone": "555-1234"}
    # Tidbits are returned separately
    assert "tidbits" in result
    assert len(result["tidbits"]) > 0


@pytest.mark.asyncio
async def test_get_person_not_found(db_session, sample_people):
    """Test getting a person that doesn't exist."""
    from memory.api.MCP.servers.people import get_person

    get_person_fn = get_fn(get_person)
    with patch("memory.api.MCP.servers.people.make_session", return_value=db_session):
        result = await get_person_fn(identifier="nonexistent_person")

    assert result is None


# =============================================================================
# Tests for list_people
# =============================================================================


@pytest.mark.asyncio
async def test_list_people_no_filters(db_session, sample_people):
    """Test listing all people without filters."""
    from memory.api.MCP.servers.people import list_people

    list_people_fn = get_fn(list_people)
    with patch("memory.api.MCP.servers.people.make_session", return_value=db_session):
        results = await list_people_fn()

    assert len(results) == 3
    # Should be ordered by display_name
    assert results[0]["display_name"] == "Alice Chen"
    assert results[1]["display_name"] == "Bob Smith"
    assert results[2]["display_name"] == "Charlie Jones"


@pytest.mark.asyncio
async def test_list_people_filter_by_search(db_session, sample_people):
    """Test filtering by search term."""
    from memory.api.MCP.servers.people import list_people

    list_people_fn = get_fn(list_people)
    with patch("memory.api.MCP.servers.people.make_session", return_value=db_session):
        results = await list_people_fn(search="alice")

    assert len(results) == 1
    assert results[0]["identifier"] == "alice_chen"


@pytest.mark.asyncio
async def test_list_people_limit(db_session, sample_people):
    """Test limiting results."""
    from memory.api.MCP.servers.people import list_people

    list_people_fn = get_fn(list_people)
    with patch("memory.api.MCP.servers.people.make_session", return_value=db_session):
        results = await list_people_fn(limit=1)

    assert len(results) == 1


@pytest.mark.asyncio
async def test_list_people_limit_max_enforced(db_session, sample_people):
    """Test that limit is capped at 200."""
    from memory.api.MCP.servers.people import list_people

    list_people_fn = get_fn(list_people)
    with patch("memory.api.MCP.servers.people.make_session", return_value=db_session):
        # Request 500 but should be capped at 200
        results = await list_people_fn(limit=500)

    # We only have 3 people, but the limit logic should cap at 200
    assert len(results) <= 200


@pytest.mark.asyncio
async def test_list_people_filter_by_tags(db_session, sample_people):
    """Test filtering people by tidbit tags."""
    from memory.api.MCP.servers.people import list_people

    list_people_fn = get_fn(list_people)
    with patch("memory.api.MCP.servers.people.make_session", return_value=db_session):
        # Filter by 'work' tag - should match Alice and Bob
        results = await list_people_fn(tags=["work"])

    identifiers = {p["identifier"] for p in results}
    assert identifiers == {"alice_chen", "bob_smith"}


@pytest.mark.asyncio
async def test_list_people_filter_by_multiple_tags(db_session, sample_people):
    """Test filtering people by multiple tags (OR logic)."""
    from memory.api.MCP.servers.people import list_people

    list_people_fn = get_fn(list_people)
    with patch("memory.api.MCP.servers.people.make_session", return_value=db_session):
        # Filter by 'engineering' or 'climbing' - should match Alice and Charlie
        results = await list_people_fn(tags=["engineering", "climbing"])

    identifiers = {p["identifier"] for p in results}
    assert identifiers == {"alice_chen", "charlie_jones"}


@pytest.mark.asyncio
async def test_list_people_filter_by_unique_tag(db_session, sample_people):
    """Test filtering by a tag unique to one person."""
    from memory.api.MCP.servers.people import list_people

    list_people_fn = get_fn(list_people)
    with patch("memory.api.MCP.servers.people.make_session", return_value=db_session):
        # Filter by 'design' tag - should only match Bob
        results = await list_people_fn(tags=["design"])

    assert len(results) == 1
    assert results[0]["identifier"] == "bob_smith"


@pytest.mark.asyncio
async def test_list_people_search_tidbit_content(db_session, sample_people):
    """Test searching in tidbit content."""
    from memory.api.MCP.servers.people import list_people

    list_people_fn = get_fn(list_people)
    with patch("memory.api.MCP.servers.people.make_session", return_value=db_session):
        # Search for 'climbing' which is in Charlie's tidbit content
        results = await list_people_fn(search="climbing")

    assert len(results) == 1
    assert results[0]["identifier"] == "charlie_jones"


# =============================================================================
# Tests for delete
# =============================================================================


@pytest.mark.asyncio
async def test_delete_person_success(db_session, sample_people):
    """Test deleting a person (as admin)."""
    from memory.api.MCP.servers.people import delete
    from unittest.mock import MagicMock

    # Create admin user mock
    admin_user = MagicMock()
    admin_user.id = 1
    admin_user.scopes = ["admin"]

    delete_fn = get_fn(delete)
    with (
        patch("memory.api.MCP.servers.people.make_session", return_value=db_session),
        patch("memory.api.MCP.servers.people.get_current_user", return_value=admin_user),
    ):
        result = await delete_fn(identifier="bob_smith")

    assert result["deleted"] is True
    assert result["identifier"] == "bob_smith"
    assert result["display_name"] == "Bob Smith"

    # Verify person was deleted
    remaining = db_session.query(Person).filter_by(identifier="bob_smith").first()
    assert remaining is None


@pytest.mark.asyncio
async def test_delete_person_not_admin(db_session, sample_people):
    """Test that non-admin users cannot delete people."""
    from memory.api.MCP.servers.people import delete
    from unittest.mock import MagicMock

    # Create non-admin user mock
    regular_user = MagicMock()
    regular_user.id = 1
    regular_user.scopes = ["people"]

    delete_fn = get_fn(delete)
    with (
        patch("memory.api.MCP.servers.people.make_session", return_value=db_session),
        patch("memory.api.MCP.servers.people.get_current_user", return_value=regular_user),
    ):
        with pytest.raises(PermissionError, match="Only admins can delete people"):
            await delete_fn(identifier="bob_smith")

    # Verify person was NOT deleted
    remaining = db_session.query(Person).filter_by(identifier="bob_smith").first()
    assert remaining is not None


@pytest.mark.asyncio
async def test_delete_person_not_found(db_session, sample_people):
    """Test deleting a person that doesn't exist."""
    from memory.api.MCP.servers.people import delete
    from unittest.mock import MagicMock

    # Create admin user mock
    admin_user = MagicMock()
    admin_user.id = 1
    admin_user.scopes = ["admin"]

    delete_fn = get_fn(delete)
    with (
        patch("memory.api.MCP.servers.people.make_session", return_value=db_session),
        patch("memory.api.MCP.servers.people.get_current_user", return_value=admin_user),
    ):
        with pytest.raises(ValueError, match="not found"):
            await delete_fn(identifier="nonexistent_person")


# =============================================================================
# Tests for _person_to_dict helper
# =============================================================================


def test_person_to_dict(sample_people):
    """Test the _person_to_dict helper function."""
    from memory.api.MCP.servers.people import _person_to_dict

    person = sample_people[0]
    result = _person_to_dict(person)

    assert result["identifier"] == "alice_chen"
    assert result["display_name"] == "Alice Chen"
    assert result["aliases"] == ["@alice_c", "alice.chen@work.com"]
    assert result["contact_info"] == {"email": "alice@example.com", "phone": "555-1234"}
    assert result["created_at"] is not None
    # Note: _person_to_dict does NOT include tidbits (see docstring)
    assert "tidbits" not in result


def test_person_to_dict_empty_fields(db_session, qdrant):
    """Test _person_to_dict with empty optional fields."""
    from memory.api.MCP.servers.people import _person_to_dict

    person = Person(
        identifier="empty_person",
        display_name="Empty Person",
        aliases=[],
        contact_info={},
    )

    result = _person_to_dict(person)

    assert result["identifier"] == "empty_person"
    assert result["aliases"] == []
    assert result["contact_info"] == {}
    # Note: _person_to_dict does NOT include tidbits (see docstring)
    assert "tidbits" not in result


# =============================================================================
# Parametrized tests
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "search_term,expected_identifiers",
    [
        ("alice", ["alice_chen"]),
        ("bob", ["bob_smith"]),
        ("smith", ["bob_smith"]),
        ("chen", ["alice_chen"]),
        ("jones", ["charlie_jones"]),
        ("@alice_c", ["alice_chen"]),  # Search in aliases
        ("alice.chen@work.com", ["alice_chen"]),  # Search in aliases (email format)
        ("@bobsmith", ["bob_smith"]),  # Search in aliases
    ],
)
async def test_list_people_various_searches(
    db_session, sample_people, search_term, expected_identifiers
):
    """Test search with various terms."""
    from memory.api.MCP.servers.people import list_people

    list_people_fn = get_fn(list_people)
    with patch("memory.api.MCP.servers.people.make_session", return_value=db_session):
        results = await list_people_fn(search=search_term)

    result_identifiers = [r["identifier"] for r in results]
    assert result_identifiers == expected_identifiers


@pytest.mark.asyncio
async def test_get_person_by_alias(db_session, sample_people):
    """Test getting a person by alias instead of identifier."""
    from memory.api.MCP.servers.people import get_person

    get_person_fn = get_fn(get_person)
    with patch("memory.api.MCP.servers.people.make_session", return_value=db_session):
        # Get by alias
        result = await get_person_fn(identifier="alice.chen@work.com")

    assert result is not None
    assert result["identifier"] == "alice_chen"
    assert result["display_name"] == "Alice Chen"


# =============================================================================
# Tests for new tidbit tools
# =============================================================================


@pytest.mark.asyncio
async def test_add_tidbit_success(db_session, sample_people):
    """Test adding a tidbit to an existing person."""
    from memory.api.MCP.servers.people import add_tidbit

    add_tidbit_fn = get_fn(add_tidbit)
    mock_task = Mock()
    mock_task.id = "task-tidbit-123"

    with patch("memory.api.MCP.servers.people.make_session", return_value=db_session):
        with patch("memory.api.MCP.servers.people.celery_app") as mock_celery:
            mock_celery.send_task.return_value = mock_task
            result = await add_tidbit_fn(
                identifier="alice_chen",
                content="Excellent at mentoring junior engineers",
                tidbit_type="observation",
                tags=["leadership", "mentoring"],
            )

    assert result["status"] == "queued"
    assert result["task_id"] == "task-tidbit-123"
    assert result["person_identifier"] == "alice_chen"


@pytest.mark.asyncio
async def test_add_tidbit_person_not_found(db_session, sample_people):
    """Test adding a tidbit to a non-existent person."""
    from memory.api.MCP.servers.people import add_tidbit

    add_tidbit_fn = get_fn(add_tidbit)
    with patch("memory.api.MCP.servers.people.make_session", return_value=db_session):
        with pytest.raises(ValueError, match="not found"):
            await add_tidbit_fn(
                identifier="nonexistent_person",
                content="Some content",
            )


@pytest.mark.asyncio
async def test_list_tidbits_for_person(db_session, sample_people):
    """Test listing tidbits for a person."""
    from memory.api.MCP.servers.people import list_tidbits

    list_tidbits_fn = get_fn(list_tidbits)
    with patch("memory.api.MCP.servers.people.make_session", return_value=db_session):
        results = await list_tidbits_fn(identifier="alice_chen")

    assert len(results) > 0
    assert all(t["person_identifier"] == "alice_chen" for t in results)


# =============================================================================
# Tests for update_tidbit
# =============================================================================


@pytest.mark.asyncio
async def test_update_tidbit_as_creator(db_session, sample_people):
    """Test updating a tidbit as the creator."""
    from memory.api.MCP.servers.people import update_tidbit

    # Get an existing tidbit
    tidbit = db_session.query(PersonTidbit).filter(PersonTidbit.person_id == sample_people[0].id).first()
    tidbit.creator_id = 1  # Set a creator_id
    db_session.commit()

    # Create user mock that matches creator
    creator_user = MagicMock()
    creator_user.id = 1
    creator_user.scopes = ["people"]

    update_tidbit_fn = get_fn(update_tidbit)
    with (
        patch("memory.api.MCP.servers.people.make_session", return_value=db_session),
        patch("memory.api.MCP.servers.people.get_current_user", return_value=creator_user),
    ):
        result = await update_tidbit_fn(
            tidbit_id=tidbit.id,
            content="Updated content by creator",
            tags=["new_tag"],
        )

    assert result["id"] == tidbit.id
    assert result["content"] == "Updated content by creator"
    assert result["tags"] == ["new_tag"]

    # Verify in DB
    db_session.refresh(tidbit)
    assert tidbit.content == "Updated content by creator"
    assert tidbit.tags == ["new_tag"]


@pytest.mark.asyncio
async def test_update_tidbit_as_admin(db_session, sample_people):
    """Test updating a tidbit as admin (not the creator)."""
    from memory.api.MCP.servers.people import update_tidbit

    # Get an existing tidbit with a different creator
    tidbit = db_session.query(PersonTidbit).filter(PersonTidbit.person_id == sample_people[0].id).first()
    tidbit.creator_id = 999  # Different creator
    db_session.commit()

    # Admin user
    admin_user = MagicMock()
    admin_user.id = 1
    admin_user.scopes = ["admin"]

    update_tidbit_fn = get_fn(update_tidbit)
    with (
        patch("memory.api.MCP.servers.people.make_session", return_value=db_session),
        patch("memory.api.MCP.servers.people.get_current_user", return_value=admin_user),
    ):
        result = await update_tidbit_fn(
            tidbit_id=tidbit.id,
            content="Updated by admin",
        )

    assert result["content"] == "Updated by admin"


@pytest.mark.asyncio
async def test_update_tidbit_permission_denied(db_session, sample_people):
    """Test that non-creator non-admin cannot update tidbit."""
    from memory.api.MCP.servers.people import update_tidbit

    # Get an existing tidbit with a different creator
    tidbit = db_session.query(PersonTidbit).filter(PersonTidbit.person_id == sample_people[0].id).first()
    tidbit.creator_id = 999  # Different creator
    db_session.commit()

    # Non-admin user who is not the creator
    other_user = MagicMock()
    other_user.id = 1
    other_user.scopes = ["people"]

    update_tidbit_fn = get_fn(update_tidbit)
    with (
        patch("memory.api.MCP.servers.people.make_session", return_value=db_session),
        patch("memory.api.MCP.servers.people.get_current_user", return_value=other_user),
    ):
        with pytest.raises(PermissionError, match="You can only edit tidbits you created"):
            await update_tidbit_fn(
                tidbit_id=tidbit.id,
                content="Should fail",
            )


@pytest.mark.asyncio
async def test_update_tidbit_not_found(db_session, sample_people):
    """Test updating a non-existent tidbit."""
    from memory.api.MCP.servers.people import update_tidbit

    user = MagicMock()
    user.id = 1
    user.scopes = ["admin"]

    update_tidbit_fn = get_fn(update_tidbit)
    with (
        patch("memory.api.MCP.servers.people.make_session", return_value=db_session),
        patch("memory.api.MCP.servers.people.get_current_user", return_value=user),
    ):
        with pytest.raises(ValueError, match="not found"):
            await update_tidbit_fn(
                tidbit_id=999999,
                content="Should fail",
            )


# =============================================================================
# Tests for delete_tidbit
# =============================================================================


@pytest.mark.asyncio
async def test_delete_tidbit_as_creator(db_session, sample_people):
    """Test deleting a tidbit as the creator."""
    from memory.api.MCP.servers.people import delete_tidbit

    # Get an existing tidbit
    tidbit = db_session.query(PersonTidbit).filter(PersonTidbit.person_id == sample_people[1].id).first()
    tidbit_id = tidbit.id
    tidbit.creator_id = 1  # Set a creator_id
    db_session.commit()

    # Creator user
    creator_user = MagicMock()
    creator_user.id = 1
    creator_user.scopes = ["people"]

    delete_tidbit_fn = get_fn(delete_tidbit)
    with (
        patch("memory.api.MCP.servers.people.make_session", return_value=db_session),
        patch("memory.api.MCP.servers.people.get_current_user", return_value=creator_user),
    ):
        result = await delete_tidbit_fn(tidbit_id=tidbit_id)

    assert result["deleted"] is True
    assert result["tidbit_id"] == tidbit_id
    assert result["person_identifier"] == "bob_smith"

    # Verify deleted
    assert db_session.get(PersonTidbit, tidbit_id) is None


@pytest.mark.asyncio
async def test_delete_tidbit_as_admin(db_session, sample_people):
    """Test deleting a tidbit as admin (not the creator)."""
    from memory.api.MCP.servers.people import delete_tidbit

    # Get an existing tidbit with a different creator
    tidbit = db_session.query(PersonTidbit).filter(PersonTidbit.person_id == sample_people[2].id).first()
    tidbit_id = tidbit.id
    tidbit.creator_id = 999  # Different creator
    db_session.commit()

    # Admin user
    admin_user = MagicMock()
    admin_user.id = 1
    admin_user.scopes = ["admin"]

    delete_tidbit_fn = get_fn(delete_tidbit)
    with (
        patch("memory.api.MCP.servers.people.make_session", return_value=db_session),
        patch("memory.api.MCP.servers.people.get_current_user", return_value=admin_user),
    ):
        result = await delete_tidbit_fn(tidbit_id=tidbit_id)

    assert result["deleted"] is True


@pytest.mark.asyncio
async def test_delete_tidbit_permission_denied(db_session, sample_people):
    """Test that non-creator non-admin cannot delete tidbit."""
    from memory.api.MCP.servers.people import delete_tidbit

    # Get an existing tidbit with a different creator
    tidbit = db_session.query(PersonTidbit).filter(PersonTidbit.person_id == sample_people[0].id).first()
    tidbit.creator_id = 999  # Different creator
    db_session.commit()

    # Non-admin user who is not the creator
    other_user = MagicMock()
    other_user.id = 1
    other_user.scopes = ["people"]

    delete_tidbit_fn = get_fn(delete_tidbit)
    with (
        patch("memory.api.MCP.servers.people.make_session", return_value=db_session),
        patch("memory.api.MCP.servers.people.get_current_user", return_value=other_user),
    ):
        with pytest.raises(PermissionError, match="You can only delete tidbits you created"):
            await delete_tidbit_fn(tidbit_id=tidbit.id)


@pytest.mark.asyncio
async def test_delete_tidbit_not_found(db_session, sample_people):
    """Test deleting a non-existent tidbit."""
    from memory.api.MCP.servers.people import delete_tidbit

    user = MagicMock()
    user.id = 1
    user.scopes = ["admin"]

    delete_tidbit_fn = get_fn(delete_tidbit)
    with (
        patch("memory.api.MCP.servers.people.make_session", return_value=db_session),
        patch("memory.api.MCP.servers.people.get_current_user", return_value=user),
    ):
        with pytest.raises(ValueError, match="not found"):
            await delete_tidbit_fn(tidbit_id=999999)
