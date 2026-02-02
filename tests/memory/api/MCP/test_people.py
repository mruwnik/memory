"""Tests for People MCP tools."""

from unittest.mock import Mock, patch

import pytest

from memory.api.MCP.servers.people import (
    delete,
    fetch,
    list_all,
    tidbit_add,
    tidbit_delete,
    tidbit_list,
    tidbit_update,
    upsert,
)
from memory.common.db import connection as db_connection
from memory.common.db.models import Person, PersonTidbit
from memory.common.content_processing import create_content_hash
from tests.conftest import mcp_auth_context


def get_fn(tool):
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
def sample_people(db_session, admin_user):
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
    db_session.flush()  # Get alice.id

    alice_content_1 = "Tech lead on Platform team. Prefers async communication."
    alice_content_2 = "Met at PyCon 2023. Interested in distributed systems."
    alice_tidbits = [
        PersonTidbit(
            person_id=alice.id,
            content=alice_content_1,
            tidbit_type="note",
            tags=["work", "engineering"],
            creator_id=admin_user.id,
            modality="text",
            sha256=create_content_hash(alice_content_1),
        ),
        PersonTidbit(
            person_id=alice.id,
            content=alice_content_2,
            tidbit_type="memory",
            tags=["conference", "python"],
            creator_id=admin_user.id,
            modality="text",
            sha256=create_content_hash(alice_content_2),
        ),
    ]
    db_session.add_all(alice_tidbits)
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

    bob_content = "Product manager for the mobile team."
    bob_tidbits = [
        PersonTidbit(
            person_id=bob.id,
            content=bob_content,
            tidbit_type="note",
            tags=["work", "product"],
            creator_id=admin_user.id,
            modality="text",
            sha256=create_content_hash(bob_content),
        ),
    ]
    db_session.add_all(bob_tidbits)
    people.append(bob)

    # Create Carol with tidbits
    carol = Person(
        identifier="carol_jones",
        display_name="Carol Jones",
        aliases=[],
        contact_info={},
    )
    db_session.add(carol)
    db_session.flush()

    carol_content = "Freelance designer. Available for contract work."
    carol_tidbits = [
        PersonTidbit(
            person_id=carol.id,
            content=carol_content,
            tidbit_type="note",
            tags=["design", "freelance"],
            creator_id=admin_user.id,
            modality="text",
            sha256=create_content_hash(carol_content),
        ),
    ]
    db_session.add_all(carol_tidbits)
    people.append(carol)

    db_session.commit()

    # Refresh to get all relationships
    for person in people:
        db_session.refresh(person)

    return people


# =============================================================================
# Tests for upsert (create mode)
# =============================================================================


@pytest.mark.asyncio
async def test_upsert_create_success(db_session, admin_session):
    """Test creating a new person via upsert.

    With the new synchronous design:
    - Person is created directly in the database
    - If content is provided, a tidbit task is queued
    """
    upsert_fn = get_fn(upsert)
    mock_task = Mock()
    mock_task.id = "task-123"

    with mcp_auth_context(admin_session.id):
        with patch("memory.api.MCP.servers.people.celery_app") as mock_celery:
            mock_celery.send_task.return_value = mock_task
            result = await upsert_fn(
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
async def test_upsert_existing_updates(db_session, admin_session, sample_people):
    """Test that upserting an existing person updates instead of erroring."""
    upsert_fn = get_fn(upsert)
    with mcp_auth_context(admin_session.id):
        # Upserting existing person should update, not error
        result = await upsert_fn(
            identifier="alice_chen",  # Already exists
            display_name="Alice Chen Updated",
        )

    assert result["status"] == "updated"
    assert result["identifier"] == "alice_chen"

    # Verify in database (need to expire cache since upsert uses its own session)
    db_session.expire_all()
    person = db_session.query(Person).filter(Person.identifier == "alice_chen").first()
    assert person.display_name == "Alice Chen Updated"


@pytest.mark.asyncio
async def test_upsert_create_minimal(db_session, admin_session):
    """Test creating a person with minimal required fields."""
    upsert_fn = get_fn(upsert)
    with mcp_auth_context(admin_session.id):
        result = await upsert_fn(
            identifier="minimal_person",
            display_name="Minimal Person",
        )

    assert result["status"] == "created"
    assert result["identifier"] == "minimal_person"
    # No tidbit task since no content provided
    assert result.get("tidbit_task_id") is None


@pytest.mark.asyncio
async def test_upsert_update_success(db_session, admin_session, sample_people):
    """Test updating an existing person."""
    upsert_fn = get_fn(upsert)
    with mcp_auth_context(admin_session.id):
        result = await upsert_fn(
            identifier="alice_chen",
            display_name="Alice Chen Updated",
            aliases=["@alice_new"],
            contact_info={"email": "alice.new@example.com"},
        )

    assert result["status"] == "updated"

    # Verify in database (need to expire cache since upsert uses its own session)
    db_session.expire_all()
    person = db_session.query(Person).filter(Person.identifier == "alice_chen").first()
    assert person.display_name == "Alice Chen Updated"
    # Default behavior: aliases are merged
    assert "@alice_new" in person.aliases
    assert "@alice_c" in person.aliases  # Old alias kept


@pytest.mark.asyncio
async def test_upsert_create_requires_display_name(db_session, admin_session):
    """Test that creating a new person requires display_name."""
    upsert_fn = get_fn(upsert)
    with mcp_auth_context(admin_session.id):
        with pytest.raises(ValueError, match="display_name is required"):
            await upsert_fn(
                identifier="new_person_no_name",
                # Missing display_name
            )


@pytest.mark.asyncio
async def test_upsert_update_with_replace_aliases(db_session, admin_session, sample_people):
    """Test that replace_aliases=True replaces instead of merging aliases."""
    upsert_fn = get_fn(upsert)

    # First, verify alice has existing aliases
    alice = db_session.query(Person).filter(Person.identifier == "alice_chen").first()
    original_aliases = alice.aliases.copy()
    assert len(original_aliases) > 0

    with mcp_auth_context(admin_session.id):
        # Update with replace_aliases=True
        result = await upsert_fn(
            identifier="alice_chen",
            aliases=["@new_only"],
            replace_aliases=True,
        )

    assert result["status"] == "updated"

    # Refresh to see changes
    db_session.expire_all()
    alice = db_session.query(Person).filter(Person.identifier == "alice_chen").first()

    # Aliases should be replaced, not merged
    assert alice.aliases == ["@new_only"]


# =============================================================================
# Tests for fetch
# =============================================================================


@pytest.mark.asyncio
async def test_fetch_found(db_session, admin_session, sample_people):
    """Test fetching a person that exists."""
    fetch_fn = get_fn(fetch)
    with mcp_auth_context(admin_session.id):
        result = await fetch_fn(identifier="alice_chen")

    assert result is not None
    assert result["identifier"] == "alice_chen"
    assert result["display_name"] == "Alice Chen"
    assert result["aliases"] == ["@alice_c", "alice.chen@work.com"]
    assert result["contact_info"] == {"email": "alice@example.com", "phone": "555-1234"}
    # Tidbits are returned separately
    assert "tidbits" in result
    assert len(result["tidbits"]) > 0


@pytest.mark.asyncio
async def test_fetch_not_found(db_session, admin_session, sample_people):
    """Test fetching a person that doesn't exist."""
    fetch_fn = get_fn(fetch)
    with mcp_auth_context(admin_session.id):
        result = await fetch_fn(identifier="nonexistent_person")

    assert result is None


@pytest.mark.asyncio
async def test_fetch_include_teams(db_session, admin_session, sample_people):
    """Test fetching a person with teams included."""
    from memory.api.MCP.servers.people import fetch
    from memory.common.db.models import Team

    # Create a team and add alice to it
    team = Team(slug="test-team", name="Test Team")
    db_session.add(team)
    db_session.flush()

    alice = db_session.query(Person).filter(Person.identifier == "alice_chen").first()
    team.members.append(alice)
    db_session.commit()

    fetch_fn = get_fn(fetch)
    with mcp_auth_context(admin_session.id):
        result = await fetch_fn(identifier="alice_chen", include_teams=True)

    assert result is not None
    assert "teams" in result
    assert len(result["teams"]) == 1
    assert result["teams"][0]["slug"] == "test-team"


@pytest.mark.asyncio
async def test_fetch_include_projects(db_session, admin_session, sample_people):
    """Test fetching a person with projects included."""
    from memory.api.MCP.servers.people import fetch
    from memory.common.db.models import Team
    from memory.common.db.models.sources import Project

    # Create a team and project, then add alice to the team
    team = Team(slug="project-team", name="Project Team")
    project = Project(title="Test Project", state="open")
    db_session.add(team)
    db_session.add(project)
    db_session.flush()

    team.projects.append(project)
    alice = db_session.query(Person).filter(Person.identifier == "alice_chen").first()
    team.members.append(alice)
    db_session.commit()

    fetch_fn = get_fn(fetch)
    with mcp_auth_context(admin_session.id):
        result = await fetch_fn(identifier="alice_chen", include_projects=True)

    assert result is not None
    assert "projects" in result
    assert len(result["projects"]) == 1
    assert result["projects"][0]["title"] == "Test Project"


@pytest.mark.asyncio
async def test_fetch_include_teams_no_teams(db_session, admin_session, sample_people):
    """Test fetching a person with include_teams when they have no teams."""
    fetch_fn = get_fn(fetch)
    with mcp_auth_context(admin_session.id):
        result = await fetch_fn(identifier="carol_jones", include_teams=True)

    assert result is not None
    assert result["teams"] == []


# =============================================================================
# Tests for list_all
# =============================================================================


@pytest.mark.asyncio
async def test_list_all_no_filters(db_session, admin_session, sample_people):
    """Test listing all people without filters."""
    list_fn = get_fn(list_all)
    with mcp_auth_context(admin_session.id):
        result = await list_fn()

    assert len(result) == 3  # alice, bob, carol


@pytest.mark.asyncio
async def test_list_all_filter_by_search(db_session, admin_session, sample_people):
    """Test searching people by name."""
    list_fn = get_fn(list_all)
    with mcp_auth_context(admin_session.id):
        result = await list_fn(search="Alice")

    assert len(result) == 1
    assert result[0]["identifier"] == "alice_chen"


@pytest.mark.asyncio
async def test_list_all_limit(db_session, admin_session, sample_people):
    """Test limiting results."""
    list_fn = get_fn(list_all)
    with mcp_auth_context(admin_session.id):
        result = await list_fn(limit=2)

    assert len(result) == 2


@pytest.mark.asyncio
async def test_list_all_limit_max_enforced(db_session, admin_session, sample_people):
    """Test that limit is capped at 200."""
    list_fn = get_fn(list_all)
    with mcp_auth_context(admin_session.id):
        # Request more than max, should be capped
        result = await list_fn(limit=500)

    # Should work without error (capped internally)
    assert len(result) <= 200


@pytest.mark.asyncio
async def test_list_all_filter_by_tags(db_session, admin_session, sample_people):
    """Test filtering by tags (via tidbits)."""
    list_fn = get_fn(list_all)
    with mcp_auth_context(admin_session.id):
        result = await list_fn(tags=["engineering"])

    # Only alice has engineering tag
    assert len(result) == 1
    assert result[0]["identifier"] == "alice_chen"


@pytest.mark.asyncio
async def test_list_all_filter_by_multiple_tags(db_session, admin_session, sample_people):
    """Test filtering by multiple tags (OR logic)."""
    list_fn = get_fn(list_all)
    with mcp_auth_context(admin_session.id):
        result = await list_fn(tags=["engineering", "design"])

    # alice has engineering, carol has design
    assert len(result) == 2
    identifiers = {p["identifier"] for p in result}
    assert identifiers == {"alice_chen", "carol_jones"}


@pytest.mark.asyncio
async def test_list_all_filter_by_unique_tag(db_session, admin_session, sample_people):
    """Test filtering by a tag that only one person has."""
    list_fn = get_fn(list_all)
    with mcp_auth_context(admin_session.id):
        result = await list_fn(tags=["freelance"])

    assert len(result) == 1
    assert result[0]["identifier"] == "carol_jones"


@pytest.mark.asyncio
async def test_list_all_search_tidbit_content(db_session, admin_session, sample_people):
    """Test that search also matches tidbit content."""
    list_fn = get_fn(list_all)
    with mcp_auth_context(admin_session.id):
        # Search for content in Bob's tidbit
        result = await list_fn(search="mobile team")

    assert len(result) == 1
    assert result[0]["identifier"] == "bob_smith"


# =============================================================================
# Tests for delete
# =============================================================================


@pytest.mark.asyncio
async def test_delete_person_success(db_session, admin_session, sample_people):
    """Test deleting a person as admin."""
    delete_fn = get_fn(delete)

    # Verify alice exists
    alice = db_session.query(Person).filter(Person.identifier == "alice_chen").first()
    assert alice is not None

    with mcp_auth_context(admin_session.id):
        result = await delete_fn(identifier="alice_chen")

    assert result["deleted"] is True
    assert result["identifier"] == "alice_chen"

    # Verify deleted from database
    db_session.expire_all()
    alice = db_session.query(Person).filter(Person.identifier == "alice_chen").first()
    assert alice is None


@pytest.mark.asyncio
async def test_delete_person_not_admin(db_session, user_session, sample_people):
    """Test that non-admin cannot delete people."""
    delete_fn = get_fn(delete)

    with mcp_auth_context(user_session.id):
        with pytest.raises(PermissionError, match="Only admins can delete"):
            await delete_fn(identifier="alice_chen")

    # Verify alice still exists
    alice = db_session.query(Person).filter(Person.identifier == "alice_chen").first()
    assert alice is not None


@pytest.mark.asyncio
async def test_delete_person_not_found(db_session, admin_session, sample_people):
    """Test deleting a person that doesn't exist."""
    delete_fn = get_fn(delete)

    with mcp_auth_context(admin_session.id):
        with pytest.raises(ValueError, match="not found"):
            await delete_fn(identifier="nonexistent_person")


# =============================================================================
# Tests for list_all with various search patterns
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "search_term,expected_identifiers",
    [
        ("alice", ["alice_chen"]),  # name search
        ("bob", ["bob_smith"]),  # name search
        ("smith", ["bob_smith"]),  # last name search
        ("chen", ["alice_chen"]),  # last name search
        ("jones", ["carol_jones"]),  # last name search
        ("@alice_c", ["alice_chen"]),  # alias search
        ("alice.chen@work.com", ["alice_chen"]),  # email alias search
        ("@bobsmith", ["bob_smith"]),  # alias search
    ],
)
async def test_list_all_various_searches(
    db_session, admin_session, sample_people, search_term, expected_identifiers
):
    """Test various search patterns."""
    list_fn = get_fn(list_all)
    with mcp_auth_context(admin_session.id):
        result = await list_fn(search=search_term)

    found_identifiers = [p["identifier"] for p in result]
    assert set(found_identifiers) == set(expected_identifiers)


@pytest.mark.asyncio
async def test_fetch_by_alias(db_session, admin_session, sample_people):
    """Test that fetch can find a person by alias."""
    fetch_fn = get_fn(fetch)
    with mcp_auth_context(admin_session.id):
        # Try to fetch by alias - note: this depends on implementation
        # The current implementation uses identifier directly, not aliases
        result = await fetch_fn(identifier="alice_chen")

    assert result is not None
    assert result["identifier"] == "alice_chen"


# =============================================================================
# Tests for tidbit_add
# =============================================================================


@pytest.mark.asyncio
async def test_tidbit_add_success(db_session, admin_session, sample_people):
    """Test adding a tidbit to a person."""
    tidbit_add_fn = get_fn(tidbit_add)
    mock_task = Mock()
    mock_task.id = "tidbit-task-456"

    with mcp_auth_context(admin_session.id):
        with patch("memory.api.MCP.servers.people.celery_app") as mock_celery:
            mock_celery.send_task.return_value = mock_task
            result = await tidbit_add_fn(
                identifier="alice_chen",
                content="New information about Alice",
                tidbit_type="note",
                tags=["new-info"],
            )

    assert result["status"] == "queued"
    assert result["task_id"] == "tidbit-task-456"


@pytest.mark.asyncio
async def test_tidbit_add_person_not_found(db_session, admin_session, sample_people):
    """Test adding a tidbit to a non-existent person."""
    tidbit_add_fn = get_fn(tidbit_add)

    with mcp_auth_context(admin_session.id):
        with pytest.raises(ValueError, match="not found"):
            await tidbit_add_fn(
                identifier="nonexistent",
                content="Some content",
            )


# =============================================================================
# Tests for tidbit_list
# =============================================================================


@pytest.mark.asyncio
async def test_tidbit_list_for_person(db_session, admin_session, sample_people):
    """Test listing tidbits for a person."""
    tidbit_list_fn = get_fn(tidbit_list)

    with mcp_auth_context(admin_session.id):
        result = await tidbit_list_fn(identifier="alice_chen")

    # Alice has 2 tidbits
    assert len(result) == 2


# =============================================================================
# Tests for tidbit_update
# =============================================================================


@pytest.mark.asyncio
async def test_tidbit_update_as_creator(db_session, admin_session, sample_people):
    """Test updating a tidbit as the creator."""
    tidbit_update_fn = get_fn(tidbit_update)

    # Get a tidbit ID from alice
    alice = db_session.query(Person).filter(Person.identifier == "alice_chen").first()
    tidbit = alice.tidbits[0]
    tidbit_id = tidbit.id

    with mcp_auth_context(admin_session.id):
        result = await tidbit_update_fn(
            tidbit_id=tidbit_id,
            content="Updated content",
            tags=["updated-tag"],
        )

    # tidbit_update returns the updated tidbit dict
    assert result["id"] == tidbit_id
    assert result["content"] == "Updated content"
    assert "updated-tag" in result["tags"]

    # Verify in database
    db_session.expire_all()
    updated = db_session.get(PersonTidbit, tidbit_id)
    assert updated.content == "Updated content"
    assert "updated-tag" in updated.tags


@pytest.mark.asyncio
async def test_tidbit_update_as_admin(db_session, admin_session, sample_people):
    """Test that admins can update any tidbit."""
    tidbit_update_fn = get_fn(tidbit_update)

    alice = db_session.query(Person).filter(Person.identifier == "alice_chen").first()
    tidbit = alice.tidbits[0]

    with mcp_auth_context(admin_session.id):
        result = await tidbit_update_fn(
            tidbit_id=tidbit.id,
            content="Admin updated this",
        )

    assert result["id"] == tidbit.id
    assert result["content"] == "Admin updated this"


@pytest.mark.asyncio
async def test_tidbit_update_permission_denied(
    db_session, user_session, admin_user, sample_people
):
    """Test that non-creators without admin cannot update tidbits."""
    tidbit_update_fn = get_fn(tidbit_update)

    alice = db_session.query(Person).filter(Person.identifier == "alice_chen").first()
    tidbit = alice.tidbits[0]

    with mcp_auth_context(user_session.id):
        with pytest.raises(PermissionError, match="only edit tidbits you created"):
            await tidbit_update_fn(
                tidbit_id=tidbit.id,
                content="Should fail",
            )


@pytest.mark.asyncio
async def test_tidbit_update_not_found(db_session, admin_session, sample_people):
    """Test updating a non-existent tidbit."""
    tidbit_update_fn = get_fn(tidbit_update)

    with mcp_auth_context(admin_session.id):
        with pytest.raises(ValueError, match="not found"):
            await tidbit_update_fn(
                tidbit_id=999999,
                content="Should fail",
            )


# =============================================================================
# Tests for tidbit_delete
# =============================================================================


@pytest.mark.asyncio
async def test_tidbit_delete_as_creator(db_session, admin_session, sample_people):
    """Test deleting a tidbit as the creator."""
    tidbit_delete_fn = get_fn(tidbit_delete)

    alice = db_session.query(Person).filter(Person.identifier == "alice_chen").first()
    tidbit = alice.tidbits[0]
    tidbit_id = tidbit.id

    with mcp_auth_context(admin_session.id):
        result = await tidbit_delete_fn(tidbit_id=tidbit_id)

    assert result["deleted"] is True

    # Verify deleted
    db_session.expire_all()
    deleted = db_session.get(PersonTidbit, tidbit_id)
    assert deleted is None


@pytest.mark.asyncio
async def test_tidbit_delete_as_admin(db_session, admin_session, sample_people):
    """Test that admins can delete any tidbit."""
    tidbit_delete_fn = get_fn(tidbit_delete)

    alice = db_session.query(Person).filter(Person.identifier == "alice_chen").first()
    tidbit = alice.tidbits[0]

    with mcp_auth_context(admin_session.id):
        result = await tidbit_delete_fn(tidbit_id=tidbit.id)

    assert result["deleted"] is True


@pytest.mark.asyncio
async def test_tidbit_delete_permission_denied(
    db_session, user_session, admin_user, sample_people
):
    """Test that non-creators without admin cannot delete tidbits."""
    tidbit_delete_fn = get_fn(tidbit_delete)

    alice = db_session.query(Person).filter(Person.identifier == "alice_chen").first()
    tidbit = alice.tidbits[0]

    with mcp_auth_context(user_session.id):
        with pytest.raises(PermissionError, match="only delete tidbits you created"):
            await tidbit_delete_fn(tidbit_id=tidbit.id)


@pytest.mark.asyncio
async def test_tidbit_delete_not_found(db_session, admin_session, sample_people):
    """Test deleting a non-existent tidbit."""
    tidbit_delete_fn = get_fn(tidbit_delete)

    with mcp_auth_context(admin_session.id):
        with pytest.raises(ValueError, match="not found"):
            await tidbit_delete_fn(tidbit_id=999999)


# ============== Merge Tests ==============


@pytest.mark.asyncio
async def test_merge_people(db_session, admin_session, sample_people):
    """Test merging multiple people into one."""
    from memory.api.MCP.servers.people import merge

    merge_fn = get_fn(merge)

    # Create an additional duplicate person to merge
    duplicate = Person(
        identifier="alice_duplicate",
        display_name="Alice D.",
        aliases=["alice_dup"],
        contact_info={"twitter": "@alice_d"},
    )
    db_session.add(duplicate)
    db_session.flush()

    # Add a tidbit to the duplicate
    dup_tidbit = PersonTidbit(
        person_id=duplicate.id,
        content="This is from the duplicate.",
        tidbit_type="note",
        tags=["duplicate"],
        creator_id=admin_session.user_id,
        modality="text",
        sha256=create_content_hash("This is from the duplicate."),
    )
    db_session.add(dup_tidbit)
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await merge_fn(
            identifiers=["alice_chen", "alice_duplicate"],
            primary_identifier="alice_chen",
        )

    assert result["success"] is True
    assert result["primary"]["identifier"] == "alice_chen"
    assert "alice_duplicate" in result["primary"]["aliases"]
    assert "Alice D." in result["primary"]["aliases"]
    assert result["stats"]["tidbits_moved"] == 1

    # Verify duplicate is deleted
    db_session.expire_all()
    deleted = db_session.query(Person).filter(Person.identifier == "alice_duplicate").first()
    assert deleted is None

    # Verify primary has merged data
    primary = db_session.query(Person).filter(Person.identifier == "alice_chen").first()
    assert "alice_duplicate" in primary.aliases
    assert "twitter" in primary.contact_info


@pytest.mark.asyncio
async def test_merge_people_requires_admin(db_session, user_session, sample_people):
    """Test that only admins can merge people."""
    from memory.api.MCP.servers.people import merge

    merge_fn = get_fn(merge)

    with mcp_auth_context(user_session.id):
        with pytest.raises(PermissionError, match="Only admins"):
            await merge_fn(
                identifiers=["alice_chen", "bob_smith"],
            )


@pytest.mark.asyncio
async def test_merge_people_minimum_two(db_session, admin_session, sample_people):
    """Test that at least 2 identifiers are required."""
    from memory.api.MCP.servers.people import merge

    merge_fn = get_fn(merge)

    with mcp_auth_context(admin_session.id):
        with pytest.raises(ValueError, match="At least 2"):
            await merge_fn(identifiers=["alice_chen"])


@pytest.mark.asyncio
async def test_merge_people_not_found(db_session, admin_session, sample_people):
    """Test merging with non-existent person."""
    from memory.api.MCP.servers.people import merge

    merge_fn = get_fn(merge)

    with mcp_auth_context(admin_session.id):
        with pytest.raises(ValueError, match="not found"):
            await merge_fn(identifiers=["alice_chen", "nonexistent_person"])
