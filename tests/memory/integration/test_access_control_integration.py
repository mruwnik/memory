"""
Integration tests for access control in indexing and search.

These tests verify:
1. SourceItem.as_payload() correctly includes people IDs
2. Items indexed to Qdrant have correct people/project/sensitivity metadata
3. Search correctly filters by person_id, project_id, and sensitivity

Requires --run-slow flag to run (uses real PostgreSQL and Qdrant).
"""

import hashlib
import uuid

import pytest

from memory.common.db.models import Note, Person
from memory.common.db.models.sources import GithubAccount, GithubRepo, Project
from memory.common.db.models.users import HumanUser


def unique_sha256(prefix: str = "") -> bytes:
    """Generate a unique sha256 hash for test data."""
    return hashlib.sha256(f"{prefix}-{uuid.uuid4()}".encode()).digest()


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def test_user(db_session):
    """Create a test user for fixtures that need user_id."""
    user = HumanUser(
        name="Test User",
        email=f"test-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="bcrypt_hash_placeholder",
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def person_alice(db_session):
    """Create a test person named Alice."""
    person = Person(
        identifier=f"alice-test-{uuid.uuid4().hex[:8]}",
        display_name="Alice Test",
        contact_info={"email": "alice@example.com"},
    )
    db_session.add(person)
    db_session.commit()
    return person


@pytest.fixture
def person_bob(db_session):
    """Create a test person named Bob."""
    person = Person(
        identifier=f"bob-test-{uuid.uuid4().hex[:8]}",
        display_name="Bob Test",
        contact_info={"email": "bob@example.com"},
    )
    db_session.add(person)
    db_session.commit()
    return person


@pytest.fixture
def person_charlie(db_session):
    """Create a test person named Charlie."""
    person = Person(
        identifier=f"charlie-test-{uuid.uuid4().hex[:8]}",
        display_name="Charlie Test",
        contact_info={"email": "charlie@example.com"},
    )
    db_session.add(person)
    db_session.commit()
    return person


@pytest.fixture
def github_account(db_session, test_user):
    """Create a GitHub account for testing."""
    account = GithubAccount(
        user_id=test_user.id,
        name="Test Account",
        auth_type="pat",
        access_token="test_token",
    )
    db_session.add(account)
    db_session.commit()
    return account


@pytest.fixture
def github_repo(db_session, github_account):
    """Create a GitHub repo for testing."""
    repo = GithubRepo(
        account_id=github_account.id,
        owner="testowner",
        name="testrepo",
    )
    db_session.add(repo)
    db_session.commit()
    return repo


@pytest.fixture
def project_alpha(db_session, github_repo):
    """Create a GitHub milestone (project) named Alpha."""
    milestone = Project(
        repo_id=github_repo.id,
        github_id=100,
        number=1,
        title="Project Alpha",
        state="open",
    )
    db_session.add(milestone)
    db_session.commit()
    return milestone


@pytest.fixture
def project_beta(db_session, github_repo):
    """Create a GitHub milestone (project) named Beta."""
    milestone = Project(
        repo_id=github_repo.id,
        github_id=101,
        number=2,
        title="Project Beta",
        state="open",
    )
    db_session.add(milestone)
    db_session.commit()
    return milestone


# ============================================================================
# Tests for as_payload() people field
# ============================================================================


def test_as_payload_with_no_people(db_session):
    """Test that as_payload returns empty people list when no people associated."""
    note = Note(
        content="Test note without people",
        modality="text",
        sha256=unique_sha256("no-people"),
    )
    db_session.add(note)
    db_session.commit()

    payload = note.as_payload()
    assert "people" in payload
    assert payload["people"] == []


def test_as_payload_with_single_person(db_session, person_alice):
    """Test that as_payload includes single person ID."""
    note = Note(
        content="Test note with Alice",
        modality="text",
        sha256=unique_sha256("with-alice"),
    )
    note.people.append(person_alice)
    db_session.add(note)
    db_session.commit()

    payload = note.as_payload()
    assert payload["people"] == [person_alice.id]


def test_as_payload_with_multiple_people(db_session, person_alice, person_bob):
    """Test that as_payload includes all person IDs."""
    note = Note(
        content="Test note with Alice and Bob",
        modality="text",
        sha256=unique_sha256("with-alice-bob"),
    )
    note.people.append(person_alice)
    note.people.append(person_bob)
    db_session.add(note)
    db_session.commit()

    payload = note.as_payload()
    assert set(payload["people"]) == {person_alice.id, person_bob.id}


def test_as_payload_with_project_and_sensitivity(db_session, project_alpha):
    """Test that as_payload works correctly with project and sensitivity."""
    note = Note(
        content="Test note with project",
        modality="text",
        sha256=unique_sha256("with-project"),
        project_id=project_alpha.id,
        sensitivity="internal",
    )
    db_session.add(note)
    db_session.commit()

    # as_payload doesn't include project_id/sensitivity directly,
    # but we verify the item was created correctly
    assert note.project_id == project_alpha.id
    assert note.sensitivity == "internal"

    payload = note.as_payload()
    assert "source_id" in payload
    assert "people" in payload


# ============================================================================
# Tests for chunk metadata (item_metadata includes people)
# ============================================================================


def test_chunk_metadata_includes_people(db_session, person_alice, person_bob):
    """Test that chunk metadata includes people IDs from the source item."""
    from memory.common.extract import DataChunk

    note = Note(
        content="Test note for chunk metadata",
        modality="text",
        sha256=unique_sha256("chunk-metadata"),
        size=100,
    )
    note.people.append(person_alice)
    note.people.append(person_bob)
    db_session.add(note)
    db_session.commit()

    # Create a chunk via the source item's _make_chunk method
    data_chunk = DataChunk(data=["Test content"])
    chunk = note._make_chunk(data_chunk, {})

    # Verify chunk metadata includes people
    assert "people" in chunk.item_metadata
    assert set(chunk.item_metadata["people"]) == {person_alice.id, person_bob.id}


def test_chunk_metadata_empty_people(db_session):
    """Test that chunk metadata includes empty people list when none associated."""
    from memory.common.extract import DataChunk

    note = Note(
        content="Test note without people",
        modality="text",
        sha256=unique_sha256("chunk-metadata-empty"),
        size=100,
    )
    db_session.add(note)
    db_session.commit()

    data_chunk = DataChunk(data=["Test content"])
    chunk = note._make_chunk(data_chunk, {})

    assert "people" in chunk.item_metadata
    assert chunk.item_metadata["people"] == []


# ============================================================================
# Tests for Qdrant indexing with people metadata
# ============================================================================


def test_qdrant_upsert_includes_people_in_payload(
    db_session, qdrant, person_alice, person_bob
):
    """Test that upsert to Qdrant includes people IDs in payload."""
    from memory.common.content_processing import process_content_item

    note = Note(
        content="Test note for Qdrant indexing with people",
        modality="text",
        sha256=unique_sha256("qdrant-with-people"),
    )
    note.people.append(person_alice)
    note.people.append(person_bob)

    # Process the content item (this will embed and push to Qdrant)
    result = process_content_item(note, db_session)

    assert result["status"] == "processed"
    assert result["chunks_count"] > 0

    # Query Qdrant to verify the payload
    points = qdrant.scroll(
        collection_name="text",
        scroll_filter={"must": [{"key": "source_id", "match": {"value": note.id}}]},
        with_payload=True,
        limit=10,
    )[0]

    assert len(points) > 0
    for point in points:
        assert "people" in point.payload
        assert set(point.payload["people"]) == {person_alice.id, person_bob.id}


def test_qdrant_upsert_with_empty_people(db_session, qdrant):
    """Test that upsert to Qdrant includes empty people list."""
    from memory.common.content_processing import process_content_item

    note = Note(
        content="Test note for Qdrant indexing without people",
        modality="text",
        sha256=unique_sha256("qdrant-no-people"),
    )

    result = process_content_item(note, db_session)

    assert result["status"] == "processed"

    # Query Qdrant to verify the payload
    points = qdrant.scroll(
        collection_name="text",
        scroll_filter={"must": [{"key": "source_id", "match": {"value": note.id}}]},
        with_payload=True,
        limit=10,
    )[0]

    assert len(points) > 0
    for point in points:
        assert "people" in point.payload
        assert point.payload["people"] == []


def test_qdrant_indexing_preserves_project_sensitivity(
    db_session, qdrant, project_alpha
):
    """Test that project_id and sensitivity are correctly indexed."""
    from memory.common.content_processing import process_content_item

    _ = qdrant  # Fixture required for Qdrant connection

    note = Note(
        content="Test note with project and sensitivity for Qdrant",
        modality="text",
        sha256=unique_sha256("qdrant-project-sensitivity"),
        project_id=project_alpha.id,
        sensitivity="confidential",
    )

    result = process_content_item(note, db_session)
    assert result["status"] == "processed"

    # Verify the note was indexed correctly
    assert note.project_id == project_alpha.id
    assert note.sensitivity == "confidential"


# ============================================================================
# Tests for Qdrant search with person filter
# ============================================================================


def test_qdrant_search_person_filter_finds_associated_items(
    db_session, qdrant, person_alice, person_bob
):
    """Test that search with person_id filter finds items associated with that person."""
    from memory.common.content_processing import process_content_item
    from memory.api.search.embeddings import build_person_filter

    # Create notes with different person associations
    note_alice = Note(
        content="Note belonging to Alice about Python programming",
        modality="text",
        sha256=unique_sha256("search-alice"),
    )
    note_alice.people.append(person_alice)
    process_content_item(note_alice, db_session)

    note_bob = Note(
        content="Note belonging to Bob about Java programming",
        modality="text",
        sha256=unique_sha256("search-bob"),
    )
    note_bob.people.append(person_bob)
    process_content_item(note_bob, db_session)

    note_both = Note(
        content="Note shared between Alice and Bob about databases",
        modality="text",
        sha256=unique_sha256("search-both"),
    )
    note_both.people.append(person_alice)
    note_both.people.append(person_bob)
    process_content_item(note_both, db_session)

    # Search for Alice's items
    person_filter = build_person_filter(person_alice.id)
    results = qdrant.scroll(
        collection_name="text",
        scroll_filter=person_filter,
        with_payload=True,
        limit=100,
    )[0]

    # Should find note_alice and note_both
    source_ids = {p.payload["source_id"] for p in results}
    assert note_alice.id in source_ids
    assert note_both.id in source_ids
    # note_bob should NOT be found (it's associated only with Bob)
    assert note_bob.id not in source_ids


def test_qdrant_search_person_filter_finds_unassociated_items(
    db_session, qdrant, person_alice
):
    """Test that search with person_id filter also finds items with no people."""
    from memory.common.content_processing import process_content_item
    from memory.api.search.embeddings import build_person_filter

    # Create a note with person association
    note_alice = Note(
        content="Note belonging to Alice about quantum computing",
        modality="text",
        sha256=unique_sha256("search-quantum"),
    )
    note_alice.people.append(person_alice)
    process_content_item(note_alice, db_session)

    # Create a note with no person association (public)
    note_public = Note(
        content="Public note about machine learning available to all",
        modality="text",
        sha256=unique_sha256("search-public"),
    )
    process_content_item(note_public, db_session)

    # Search for Alice's items
    person_filter = build_person_filter(person_alice.id)
    results = qdrant.scroll(
        collection_name="text",
        scroll_filter=person_filter,
        with_payload=True,
        limit=100,
    )[0]

    source_ids = {p.payload["source_id"] for p in results}

    # Should find both Alice's note AND the public note
    assert note_alice.id in source_ids
    assert note_public.id in source_ids


def test_qdrant_search_person_filter_excludes_others_items(
    db_session, qdrant, person_alice, person_bob, person_charlie
):
    """Test that search with person_id filter excludes items for other people."""
    from memory.common.content_processing import process_content_item
    from memory.api.search.embeddings import build_person_filter

    # Create notes for different people
    note_bob_charlie = Note(
        content="Secret note for Bob and Charlie only",
        modality="text",
        sha256=unique_sha256("search-secret"),
    )
    note_bob_charlie.people.append(person_bob)
    note_bob_charlie.people.append(person_charlie)
    process_content_item(note_bob_charlie, db_session)

    # Search as Alice
    person_filter = build_person_filter(person_alice.id)
    results = qdrant.scroll(
        collection_name="text",
        scroll_filter=person_filter,
        with_payload=True,
        limit=100,
    )[0]

    source_ids = {p.payload["source_id"] for p in results}

    # Alice should NOT see Bob and Charlie's note
    assert note_bob_charlie.id not in source_ids


# ============================================================================
# Tests for Qdrant search with access control filter
# ============================================================================


def test_qdrant_search_access_filter_single_project(
    db_session, qdrant, project_alpha, project_beta
):
    """Test that access filter correctly restricts to single project."""
    from memory.common.content_processing import process_content_item
    from memory.common.access_control import AccessCondition, AccessFilter
    from memory.api.search.embeddings import build_access_qdrant_filter

    # Create notes in different projects
    note_alpha = Note(
        content="Note in Project Alpha about backend services",
        modality="text",
        sha256=unique_sha256("access-alpha"),
        project_id=project_alpha.id,
        sensitivity="basic",
    )
    process_content_item(note_alpha, db_session)

    note_beta = Note(
        content="Note in Project Beta about frontend features",
        modality="text",
        sha256=unique_sha256("access-beta"),
        project_id=project_beta.id,
        sensitivity="basic",
    )
    process_content_item(note_beta, db_session)

    # Search with access to only Project Alpha
    access_filter = AccessFilter(
        conditions=[
            AccessCondition(
                project_id=project_alpha.id,
                sensitivities=frozenset({"basic", "internal", "confidential"}),
            )
        ]
    )
    qdrant_conditions = build_access_qdrant_filter(access_filter)

    # Build the full filter
    results = qdrant.scroll(
        collection_name="text",
        scroll_filter={"should": qdrant_conditions},
        with_payload=True,
        limit=100,
    )[0]

    source_ids = {p.payload["source_id"] for p in results}

    # Should find Alpha note but not Beta note
    assert note_alpha.id in source_ids
    assert note_beta.id not in source_ids


def test_qdrant_search_access_filter_sensitivity_levels(
    db_session, qdrant, project_alpha
):
    """Test that access filter correctly filters by sensitivity."""
    from memory.common.content_processing import process_content_item
    from memory.common.access_control import AccessCondition, AccessFilter
    from memory.api.search.embeddings import build_access_qdrant_filter

    # Create notes with different sensitivity levels
    note_basic = Note(
        content="Basic sensitivity note for all contributors",
        modality="text",
        sha256=unique_sha256("sensitivity-basic"),
        project_id=project_alpha.id,
        sensitivity="basic",
    )
    process_content_item(note_basic, db_session)

    note_internal = Note(
        content="Internal sensitivity note for managers",
        modality="text",
        sha256=unique_sha256("sensitivity-internal"),
        project_id=project_alpha.id,
        sensitivity="internal",
    )
    process_content_item(note_internal, db_session)

    note_confidential = Note(
        content="Confidential sensitivity note for admins only",
        modality="text",
        sha256=unique_sha256("sensitivity-confidential"),
        project_id=project_alpha.id,
        sensitivity="confidential",
    )
    process_content_item(note_confidential, db_session)

    # Search as contributor (basic only)
    contributor_filter = AccessFilter(
        conditions=[
            AccessCondition(
                project_id=project_alpha.id,
                sensitivities=frozenset({"basic"}),
            )
        ]
    )
    contributor_conditions = build_access_qdrant_filter(contributor_filter)
    contributor_results = qdrant.scroll(
        collection_name="text",
        scroll_filter={"should": contributor_conditions},
        with_payload=True,
        limit=100,
    )[0]

    contributor_source_ids = {p.payload["source_id"] for p in contributor_results}
    assert note_basic.id in contributor_source_ids
    assert note_internal.id not in contributor_source_ids
    assert note_confidential.id not in contributor_source_ids

    # Search as manager (basic + internal)
    manager_filter = AccessFilter(
        conditions=[
            AccessCondition(
                project_id=project_alpha.id,
                sensitivities=frozenset({"basic", "internal"}),
            )
        ]
    )
    manager_conditions = build_access_qdrant_filter(manager_filter)
    manager_results = qdrant.scroll(
        collection_name="text",
        scroll_filter={"should": manager_conditions},
        with_payload=True,
        limit=100,
    )[0]

    manager_source_ids = {p.payload["source_id"] for p in manager_results}
    assert note_basic.id in manager_source_ids
    assert note_internal.id in manager_source_ids
    assert note_confidential.id not in manager_source_ids

    # Search as admin (all)
    admin_filter = AccessFilter(
        conditions=[
            AccessCondition(
                project_id=project_alpha.id,
                sensitivities=frozenset({"basic", "internal", "confidential"}),
            )
        ]
    )
    admin_conditions = build_access_qdrant_filter(admin_filter)
    admin_results = qdrant.scroll(
        collection_name="text",
        scroll_filter={"should": admin_conditions},
        with_payload=True,
        limit=100,
    )[0]

    admin_source_ids = {p.payload["source_id"] for p in admin_results}
    assert note_basic.id in admin_source_ids
    assert note_internal.id in admin_source_ids
    assert note_confidential.id in admin_source_ids


def test_qdrant_search_access_filter_multiple_projects(
    db_session, qdrant, project_alpha, project_beta
):
    """Test that access filter works with multiple project memberships."""
    from memory.common.content_processing import process_content_item
    from memory.common.access_control import AccessCondition, AccessFilter
    from memory.api.search.embeddings import build_access_qdrant_filter

    # Create notes in different projects with different sensitivities
    note_alpha_basic = Note(
        content="Alpha basic note about infrastructure",
        modality="text",
        sha256=unique_sha256("multi-alpha-basic"),
        project_id=project_alpha.id,
        sensitivity="basic",
    )
    process_content_item(note_alpha_basic, db_session)

    note_alpha_internal = Note(
        content="Alpha internal note about security",
        modality="text",
        sha256=unique_sha256("multi-alpha-internal"),
        project_id=project_alpha.id,
        sensitivity="internal",
    )
    process_content_item(note_alpha_internal, db_session)

    note_beta_basic = Note(
        content="Beta basic note about features",
        modality="text",
        sha256=unique_sha256("multi-beta-basic"),
        project_id=project_beta.id,
        sensitivity="basic",
    )
    process_content_item(note_beta_basic, db_session)

    note_beta_confidential = Note(
        content="Beta confidential note about finances",
        modality="text",
        sha256=unique_sha256("multi-beta-confidential"),
        project_id=project_beta.id,
        sensitivity="confidential",
    )
    process_content_item(note_beta_confidential, db_session)

    # User is contributor on Alpha (basic) and admin on Beta (all)
    access_filter = AccessFilter(
        conditions=[
            AccessCondition(
                project_id=project_alpha.id,
                sensitivities=frozenset({"basic"}),
            ),
            AccessCondition(
                project_id=project_beta.id,
                sensitivities=frozenset({"basic", "internal", "confidential"}),
            ),
        ]
    )

    qdrant_conditions = build_access_qdrant_filter(access_filter)
    results = qdrant.scroll(
        collection_name="text",
        scroll_filter={"should": qdrant_conditions},
        with_payload=True,
        limit=100,
    )[0]

    source_ids = {p.payload["source_id"] for p in results}

    # Should see: alpha_basic, beta_basic, beta_confidential
    # Should NOT see: alpha_internal
    assert note_alpha_basic.id in source_ids
    assert note_alpha_internal.id not in source_ids
    assert note_beta_basic.id in source_ids
    assert note_beta_confidential.id in source_ids


def test_qdrant_search_superadmin_no_filter(db_session, qdrant, project_alpha):
    """Test that superadmin (None filter) sees all items."""
    from memory.common.content_processing import process_content_item
    from memory.api.search.embeddings import build_access_qdrant_filter

    # Create a confidential note
    note = Note(
        content="Top secret confidential note for superadmin test",
        modality="text",
        sha256=unique_sha256("superadmin-test"),
        project_id=project_alpha.id,
        sensitivity="confidential",
    )
    process_content_item(note, db_session)

    # Superadmin filter (None)
    qdrant_conditions = build_access_qdrant_filter(None)
    assert qdrant_conditions == []  # Empty conditions = no filtering

    # Query without filters (superadmin)
    results = qdrant.scroll(
        collection_name="text",
        scroll_filter={"must": [{"key": "source_id", "match": {"value": note.id}}]},
        with_payload=True,
        limit=100,
    )[0]

    assert len(results) > 0


def test_qdrant_search_empty_access_filter_matches_nothing(db_session, qdrant):
    """Test that empty access filter (no project access, no public bypass) matches nothing."""
    from memory.common.content_processing import process_content_item
    from memory.common.access_control import AccessFilter
    from memory.api.search.embeddings import build_access_qdrant_filter

    # Create a note
    note = Note(
        content="Note that should not be found by users with no access",
        modality="text",
        sha256=unique_sha256("empty-filter-test"),
    )
    process_content_item(note, db_session)

    # Empty access filter without public bypass (user has no access at all)
    access_filter = AccessFilter(conditions=[], include_public=False)
    qdrant_conditions = build_access_qdrant_filter(access_filter)

    # Should return impossible condition
    assert len(qdrant_conditions) == 1
    assert qdrant_conditions[0]["key"] == "project_id"
    assert qdrant_conditions[0]["match"]["value"] == -1

    # Search with this filter should find nothing
    results = qdrant.scroll(
        collection_name="text",
        scroll_filter={"must": qdrant_conditions},
        with_payload=True,
        limit=100,
    )[0]

    assert len(results) == 0


# ============================================================================
# Combined tests: person + access control
# ============================================================================


def test_qdrant_search_combined_person_and_access_filter(
    db_session, qdrant, person_alice, person_bob, project_alpha
):
    """Test that both person and access filters work together."""
    from memory.common.content_processing import process_content_item
    from memory.common.access_control import AccessCondition, AccessFilter
    from memory.api.search.embeddings import build_person_filter, build_access_qdrant_filter

    # Create notes with various combinations
    # Note for Alice in Alpha (basic) - should be found
    note_alice_alpha = Note(
        content="Alice's note in Alpha project",
        modality="text",
        sha256=unique_sha256("combined-alice-alpha"),
        project_id=project_alpha.id,
        sensitivity="basic",
    )
    note_alice_alpha.people.append(person_alice)
    process_content_item(note_alice_alpha, db_session)

    # Note for Bob in Alpha (basic) - should NOT be found (wrong person)
    note_bob_alpha = Note(
        content="Bob's note in Alpha project",
        modality="text",
        sha256=unique_sha256("combined-bob-alpha"),
        project_id=project_alpha.id,
        sensitivity="basic",
    )
    note_bob_alpha.people.append(person_bob)
    process_content_item(note_bob_alpha, db_session)

    # Note for Alice in Alpha (confidential) - should NOT be found (wrong sensitivity)
    note_alice_confidential = Note(
        content="Alice's confidential note",
        modality="text",
        sha256=unique_sha256("combined-alice-confidential"),
        project_id=project_alpha.id,
        sensitivity="confidential",
    )
    note_alice_confidential.people.append(person_alice)
    process_content_item(note_alice_confidential, db_session)

    # Public note in Alpha (basic) - should be found (no person restriction)
    note_public = Note(
        content="Public note in Alpha",
        modality="text",
        sha256=unique_sha256("combined-public"),
        project_id=project_alpha.id,
        sensitivity="basic",
    )
    process_content_item(note_public, db_session)

    # Build combined filter: Alice + contributor access to Alpha
    person_filter = build_person_filter(person_alice.id)
    access_filter = AccessFilter(
        conditions=[
            AccessCondition(
                project_id=project_alpha.id,
                sensitivities=frozenset({"basic"}),
            )
        ]
    )
    access_conditions = build_access_qdrant_filter(access_filter)

    # Combine filters
    combined_filter = {
        "must": [
            person_filter,  # Person filter (should)
            {"should": access_conditions},  # Access filter
        ]
    }

    results = qdrant.scroll(
        collection_name="text",
        scroll_filter=combined_filter,
        with_payload=True,
        limit=100,
    )[0]

    source_ids = {p.payload["source_id"] for p in results}

    # Should find: alice_alpha, public
    # Should NOT find: bob_alpha (wrong person), alice_confidential (wrong sensitivity)
    assert note_alice_alpha.id in source_ids
    assert note_public.id in source_ids
    assert note_bob_alpha.id not in source_ids
    assert note_alice_confidential.id not in source_ids


# ============================================================================
# ADVERSARIAL TESTS: Attempt to break the access control system
#
# These tests verify that the system correctly blocks unauthorized access
# in edge cases and attack scenarios.
# ============================================================================


# --- NULL project_id bypass attempts ---


def test_adversarial_null_project_id_invisible_to_regular_users(
    db_session, qdrant, project_alpha
):
    """
    ATTACK: Items with NULL project_id should NEVER be visible to non-superadmins.

    This is critical - NULL project_id means unclassified content that
    should only be accessible by superadmins.
    """
    from memory.common.content_processing import process_content_item
    from memory.common.access_control import AccessCondition, AccessFilter
    from memory.api.search.embeddings import build_access_qdrant_filter

    # Create an item with NULL project_id (unclassified)
    unclassified_note = Note(
        content="Unclassified sensitive data with NULL project_id",
        modality="text",
        sha256=unique_sha256("adversarial-null-project"),
        project_id=None,  # Explicitly NULL
        sensitivity="basic",  # Even basic sensitivity shouldn't help
    )
    process_content_item(unclassified_note, db_session)

    # Also create a legitimate item in project_alpha for comparison
    classified_note = Note(
        content="Properly classified data in project",
        modality="text",
        sha256=unique_sha256("adversarial-classified"),
        project_id=project_alpha.id,
        sensitivity="basic",
    )
    process_content_item(classified_note, db_session)

    # User has ADMIN access to project_alpha - maximum possible access
    # But they should still NOT see NULL project_id items
    access_filter = AccessFilter(
        conditions=[
            AccessCondition(
                project_id=project_alpha.id,
                sensitivities=frozenset({"basic", "internal", "confidential"}),
            )
        ]
    )
    qdrant_conditions = build_access_qdrant_filter(access_filter)

    results = qdrant.scroll(
        collection_name="text",
        scroll_filter={"should": qdrant_conditions},
        with_payload=True,
        limit=100,
    )[0]

    source_ids = {p.payload["source_id"] for p in results}

    # Should see classified note
    assert classified_note.id in source_ids
    # MUST NOT see unclassified (NULL project_id) note
    assert unclassified_note.id not in source_ids


def test_adversarial_null_project_id_invisible_in_bm25(
    db_session, qdrant, project_alpha
):
    """
    ATTACK: Verify BM25 also blocks NULL project_id items.

    Access control must be consistent across both search backends.
    """
    import asyncio
    from memory.common.content_processing import process_content_item
    from memory.common.access_control import AccessCondition, AccessFilter
    from memory.api.search.bm25 import search_bm25
    from memory.common.db.models import Chunk

    # Create items with unique searchable content
    search_term = f"nullprojecttest{uuid.uuid4().hex[:8]}"

    unclassified = Note(
        content=f"Secret {search_term} unclassified content",
        modality="text",
        sha256=unique_sha256("bm25-null-project"),
        project_id=None,
        sensitivity="basic",
    )
    process_content_item(unclassified, db_session)

    classified = Note(
        content=f"Safe {search_term} classified content",
        modality="text",
        sha256=unique_sha256("bm25-classified"),
        project_id=project_alpha.id,
        sensitivity="basic",
    )
    process_content_item(classified, db_session)

    # Search with admin access to project_alpha
    access_filter = AccessFilter(
        conditions=[
            AccessCondition(
                project_id=project_alpha.id,
                sensitivities=frozenset({"basic", "internal", "confidential"}),
            )
        ]
    )

    results = asyncio.run(
        search_bm25(
            query=search_term,
            modalities={"text"},
            limit=100,
            filters={"access_filter": access_filter},
        )
    )

    # Get source IDs from chunks using the test's db_session
    if results:
        chunk_ids = list(results.keys())
        chunks = (
            db_session.query(Chunk.source_id)
            .filter(Chunk.id.in_([uuid.UUID(cid) for cid in chunk_ids]))
            .all()
        )
        source_ids = {c.source_id for c in chunks}
    else:
        source_ids = set()

    # Unclassified item must not be found
    assert unclassified.id not in source_ids, (
        "BM25 returned unclassified item with NULL project_id!"
    )


# --- Sensitivity escalation attempts ---


def test_adversarial_contributor_cannot_escalate_to_internal(
    db_session, qdrant, project_alpha
):
    """ATTACK: Contributor tries to access internal-sensitivity content."""
    from memory.common.content_processing import process_content_item
    from memory.common.access_control import AccessCondition, AccessFilter
    from memory.api.search.embeddings import build_access_qdrant_filter

    internal_secret = Note(
        content="Internal sensitive information about salaries",
        modality="text",
        sha256=unique_sha256("escalation-internal"),
        project_id=project_alpha.id,
        sensitivity="internal",
    )
    process_content_item(internal_secret, db_session)

    # Contributor only has basic access
    contributor_filter = AccessFilter(
        conditions=[
            AccessCondition(
                project_id=project_alpha.id,
                sensitivities=frozenset({"basic"}),  # Only basic!
            )
        ]
    )
    qdrant_conditions = build_access_qdrant_filter(contributor_filter)

    results = qdrant.scroll(
        collection_name="text",
        scroll_filter={"should": qdrant_conditions},
        with_payload=True,
        limit=100,
    )[0]

    source_ids = {p.payload["source_id"] for p in results}

    assert internal_secret.id not in source_ids, (
        "Contributor saw internal-sensitivity content!"
    )


def test_adversarial_manager_cannot_escalate_to_confidential(
    db_session, qdrant, project_alpha
):
    """ATTACK: Manager tries to access confidential-sensitivity content."""
    from memory.common.content_processing import process_content_item
    from memory.common.access_control import AccessCondition, AccessFilter
    from memory.api.search.embeddings import build_access_qdrant_filter

    confidential_secret = Note(
        content="Confidential executive compensation information",
        modality="text",
        sha256=unique_sha256("escalation-confidential"),
        project_id=project_alpha.id,
        sensitivity="confidential",
    )
    process_content_item(confidential_secret, db_session)

    # Manager has basic + internal, but NOT confidential
    manager_filter = AccessFilter(
        conditions=[
            AccessCondition(
                project_id=project_alpha.id,
                sensitivities=frozenset({"basic", "internal"}),  # No confidential!
            )
        ]
    )
    qdrant_conditions = build_access_qdrant_filter(manager_filter)

    results = qdrant.scroll(
        collection_name="text",
        scroll_filter={"should": qdrant_conditions},
        with_payload=True,
        limit=100,
    )[0]

    source_ids = {p.payload["source_id"] for p in results}

    assert confidential_secret.id not in source_ids, (
        "Manager saw confidential-sensitivity content!"
    )


# --- Cross-project access attempts ---


def test_adversarial_project_boundary_strictly_enforced(
    db_session, qdrant, project_alpha, project_beta
):
    """
    ATTACK: User with access to project A tries to see project B content.

    Even if they have admin access to A, they should see NOTHING from B.
    """
    from memory.common.content_processing import process_content_item
    from memory.common.access_control import AccessCondition, AccessFilter
    from memory.api.search.embeddings import build_access_qdrant_filter

    # Create content in project_beta at all sensitivity levels
    beta_basic = Note(
        content="Project Beta basic information",
        modality="text",
        sha256=unique_sha256("cross-project-basic"),
        project_id=project_beta.id,
        sensitivity="basic",
    )
    beta_internal = Note(
        content="Project Beta internal information",
        modality="text",
        sha256=unique_sha256("cross-project-internal"),
        project_id=project_beta.id,
        sensitivity="internal",
    )
    beta_confidential = Note(
        content="Project Beta confidential information",
        modality="text",
        sha256=unique_sha256("cross-project-confidential"),
        project_id=project_beta.id,
        sensitivity="confidential",
    )
    process_content_item(beta_basic, db_session)
    process_content_item(beta_internal, db_session)
    process_content_item(beta_confidential, db_session)

    # User has ADMIN access to project_alpha only
    alpha_admin_filter = AccessFilter(
        conditions=[
            AccessCondition(
                project_id=project_alpha.id,
                sensitivities=frozenset({"basic", "internal", "confidential"}),
            )
        ]
    )
    qdrant_conditions = build_access_qdrant_filter(alpha_admin_filter)

    results = qdrant.scroll(
        collection_name="text",
        scroll_filter={"should": qdrant_conditions},
        with_payload=True,
        limit=100,
    )[0]

    source_ids = {p.payload["source_id"] for p in results}

    # MUST NOT see any project_beta content
    assert beta_basic.id not in source_ids, "Saw beta basic content!"
    assert beta_internal.id not in source_ids, "Saw beta internal content!"
    assert beta_confidential.id not in source_ids, "Saw beta confidential content!"


# --- Person filter bypass attempts ---


def test_adversarial_person_filter_blocks_other_peoples_content(
    db_session, qdrant, person_alice, person_bob, project_alpha
):
    """
    ATTACK: Alice tries to see Bob's private content.

    Items explicitly associated with Bob should NOT be visible to Alice.
    """
    from memory.common.content_processing import process_content_item
    from memory.common.access_control import AccessCondition, AccessFilter
    from memory.api.search.embeddings import build_person_filter, build_access_qdrant_filter

    # Bob's private note
    bobs_private = Note(
        content="Bob's private notes about his medical condition",
        modality="text",
        sha256=unique_sha256("person-bypass-bob"),
        project_id=project_alpha.id,
        sensitivity="basic",
    )
    bobs_private.people.append(person_bob)
    process_content_item(bobs_private, db_session)

    # Alice has access to the same project
    person_filter = build_person_filter(person_alice.id)
    access_filter = AccessFilter(
        conditions=[
            AccessCondition(
                project_id=project_alpha.id,
                sensitivities=frozenset({"basic", "internal", "confidential"}),
            )
        ]
    )
    access_conditions = build_access_qdrant_filter(access_filter)

    combined_filter = {
        "must": [
            person_filter,
            {"should": access_conditions},
        ]
    }

    results = qdrant.scroll(
        collection_name="text",
        scroll_filter=combined_filter,
        with_payload=True,
        limit=100,
    )[0]

    source_ids = {p.payload["source_id"] for p in results}

    assert bobs_private.id not in source_ids, (
        "Alice saw Bob's private content!"
    )


def test_adversarial_public_items_visible_but_private_items_not(
    db_session, qdrant, person_alice, person_bob, project_alpha
):
    """
    ATTACK: Verify that public items (no people) are visible,
    but items for specific other people are not.

    This ensures the "is_empty" condition works correctly.
    """
    from memory.common.content_processing import process_content_item
    from memory.common.access_control import AccessCondition, AccessFilter
    from memory.api.search.embeddings import build_person_filter, build_access_qdrant_filter

    # Public item (no people restriction)
    public_item = Note(
        content="Public company announcement",
        modality="text",
        sha256=unique_sha256("public-vs-private-public"),
        project_id=project_alpha.id,
        sensitivity="basic",
    )
    process_content_item(public_item, db_session)

    # Alice's private item
    alices_item = Note(
        content="Alice's performance review",
        modality="text",
        sha256=unique_sha256("public-vs-private-alice"),
        project_id=project_alpha.id,
        sensitivity="basic",
    )
    alices_item.people.append(person_alice)
    process_content_item(alices_item, db_session)

    # Bob's private item
    bobs_item = Note(
        content="Bob's performance review",
        modality="text",
        sha256=unique_sha256("public-vs-private-bob"),
        project_id=project_alpha.id,
        sensitivity="basic",
    )
    bobs_item.people.append(person_bob)
    process_content_item(bobs_item, db_session)

    # Search as Alice
    person_filter = build_person_filter(person_alice.id)
    access_filter = AccessFilter(
        conditions=[
            AccessCondition(
                project_id=project_alpha.id,
                sensitivities=frozenset({"basic"}),
            )
        ]
    )
    access_conditions = build_access_qdrant_filter(access_filter)

    combined_filter = {
        "must": [
            person_filter,
            {"should": access_conditions},
        ]
    }

    results = qdrant.scroll(
        collection_name="text",
        scroll_filter=combined_filter,
        with_payload=True,
        limit=100,
    )[0]

    source_ids = {p.payload["source_id"] for p in results}

    # Alice should see public item and her own item
    assert public_item.id in source_ids, "Alice can't see public item"
    assert alices_item.id in source_ids, "Alice can't see her own item"
    # Alice must NOT see Bob's item
    assert bobs_item.id not in source_ids, "Alice saw Bob's private item!"


# --- Empty filter edge cases ---


def test_adversarial_empty_access_filter_blocks_everything(
    db_session, qdrant, project_alpha
):
    """
    ATTACK: User with no project access should see only public items.

    With include_public=True (default), users can see public items even without
    project access. Basic/internal/confidential items remain hidden.
    """
    from memory.common.content_processing import process_content_item
    from memory.common.access_control import AccessFilter
    from memory.api.search.embeddings import build_access_qdrant_filter

    # Create a basic note
    basic_note = Note(
        content="Even basic content should be hidden from unauthorized users",
        modality="text",
        sha256=unique_sha256("empty-filter-test"),
        project_id=project_alpha.id,
        sensitivity="basic",
    )
    process_content_item(basic_note, db_session)

    # User has NO project access but include_public is True by default
    empty_filter = AccessFilter(conditions=[])
    qdrant_conditions = build_access_qdrant_filter(empty_filter)

    # Should return public sensitivity filter (users can see public items)
    assert len(qdrant_conditions) == 1
    assert qdrant_conditions[0]["key"] == "sensitivity"
    assert qdrant_conditions[0]["match"]["value"] == "public"

    results = qdrant.scroll(
        collection_name="text",
        scroll_filter={"should": qdrant_conditions},
        with_payload=True,
        limit=100,
    )[0]

    # Should find NOTHING (basic items don't match public filter)
    assert len(results) == 0, "Empty filter returned non-public results!"


def test_adversarial_empty_access_filter_no_public_blocks_everything(
    db_session, qdrant, project_alpha
):
    """
    ATTACK: User with no project access AND include_public=False should see NOTHING.

    This tests the truly empty filter edge case.
    """
    from memory.common.content_processing import process_content_item
    from memory.common.access_control import AccessFilter
    from memory.api.search.embeddings import build_access_qdrant_filter

    # Create a basic note
    basic_note = Note(
        content="Even basic content should be hidden from unauthorized users",
        modality="text",
        sha256=unique_sha256("empty-filter-no-public-test"),
        project_id=project_alpha.id,
        sensitivity="basic",
    )
    process_content_item(basic_note, db_session)

    # User has NO project access AND no public bypass
    empty_filter = AccessFilter(conditions=[], include_public=False)
    qdrant_conditions = build_access_qdrant_filter(empty_filter)

    # Should return impossible condition
    assert len(qdrant_conditions) == 1
    assert qdrant_conditions[0]["key"] == "project_id"
    assert qdrant_conditions[0]["match"]["value"] == -1

    results = qdrant.scroll(
        collection_name="text",
        scroll_filter={"must": qdrant_conditions},
        with_payload=True,
        limit=100,
    )[0]

    # Should find NOTHING
    assert len(results) == 0, "Empty filter returned results!"


def test_adversarial_invalid_role_grants_no_access(db_session):
    """
    ATTACK: What if project_roles contains an invalid role?

    Invalid roles should be skipped, not grant access.
    """
    from memory.common.access_control import build_access_filter

    from memory.common.db.models.users import HumanUser

    user = HumanUser(
        name="Test User",
        email=f"invalid-role-test-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hash",
    )
    db_session.add(user)
    db_session.commit()

    # Project roles with invalid role
    project_roles = {
        1: "hacker",  # Invalid role
        2: "superadmin",  # Invalid role (not a real role)
        3: "root",  # Invalid role
    }

    access_filter = build_access_filter(user, project_roles)

    # Should return empty filter (invalid roles are skipped)
    assert access_filter is not None
    assert access_filter.is_empty(), "Invalid roles granted access!"


def test_adversarial_mixed_valid_invalid_roles(db_session, project_alpha):
    """ATTACK: Mix of valid and invalid roles should only grant valid access."""
    from memory.common.access_control import build_access_filter
    from memory.common.db.models.users import HumanUser

    user = HumanUser(
        name="Test User",
        email=f"mixed-role-test-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hash",
    )
    db_session.add(user)
    db_session.commit()

    project_roles = {
        project_alpha.id: "contributor",  # Valid
        999: "hacker",  # Invalid role
        998: "admin",  # Valid role but different project
    }

    access_filter = build_access_filter(user, project_roles)

    assert access_filter is not None
    # Should have 2 conditions (for the valid roles on projects alpha and 998)
    assert len(access_filter.conditions) == 2

    # Find the condition for project_alpha
    alpha_condition = next(
        (c for c in access_filter.conditions if c.project_id == project_alpha.id),
        None,
    )
    assert alpha_condition is not None
    # Contributors have access to public and basic sensitivity levels
    assert alpha_condition.sensitivities == frozenset({"public", "basic"})


def test_adversarial_invalid_sensitivity_level_not_indexed(db_session, qdrant, project_alpha):
    """
    ATTACK: Items with invalid sensitivity should not be matched by normal filters.

    If an item somehow gets an invalid sensitivity value, it should effectively
    be invisible to all users (fail closed).
    """
    from memory.common.content_processing import process_content_item
    from memory.common.access_control import AccessCondition, AccessFilter
    from memory.api.search.embeddings import build_access_qdrant_filter
    from qdrant_client.http.models import PointStruct
    import uuid as uuid_module

    # Create a legitimate item
    legit_note = Note(
        content="Legitimate note with valid sensitivity",
        modality="text",
        sha256=unique_sha256("invalid-sensitivity-legit"),
        project_id=project_alpha.id,
        sensitivity="basic",
    )
    process_content_item(legit_note, db_session)

    # Manually insert a point with an invalid sensitivity value
    invalid_point_id = str(uuid_module.uuid4())
    invalid_point = PointStruct(
        id=invalid_point_id,
        vector=[0.1] * 1024,  # Match mock embedder dimension
        payload={
            "source_id": 999998,
            "tags": [],
            "size": 100,
            "people": [],
            "project_id": project_alpha.id,
            "sensitivity": "hacked",  # Invalid sensitivity!
        },
    )
    qdrant.upsert(
        collection_name="text",
        points=[invalid_point],
    )

    # User with full access to project_alpha (all valid sensitivities)
    access_filter = AccessFilter(
        conditions=[
            AccessCondition(
                project_id=project_alpha.id,
                sensitivities=frozenset({"basic", "internal", "confidential"}),
            )
        ]
    )
    qdrant_conditions = build_access_qdrant_filter(access_filter)

    results = qdrant.scroll(
        collection_name="text",
        scroll_filter={"should": qdrant_conditions},
        with_payload=True,
        limit=100,
    )[0]

    result_ids = {str(p.id) for p in results}

    # Item with invalid sensitivity should NOT be found
    # (since "hacked" is not in {"basic", "internal", "confidential"})
    assert invalid_point_id not in result_ids, (
        "Item with invalid sensitivity value was returned!"
    )

    # Legitimate item should still be found
    legit_ids = {p.payload["source_id"] for p in results}
    assert legit_note.id in legit_ids


# --- Qdrant payload verification ---


def test_adversarial_qdrant_payload_contains_required_access_fields(
    db_session, qdrant, project_alpha, person_alice
):
    """
    VERIFY: All access control fields must be in Qdrant payload.

    If any field is missing, access control filtering won't work!
    """
    from memory.common.content_processing import process_content_item

    note = Note(
        content="Test note with all access control fields",
        modality="text",
        sha256=unique_sha256("payload-verify"),
        project_id=project_alpha.id,
        sensitivity="internal",
    )
    note.people.append(person_alice)
    process_content_item(note, db_session)

    # Query Qdrant directly
    points = qdrant.scroll(
        collection_name="text",
        scroll_filter={"must": [{"key": "source_id", "match": {"value": note.id}}]},
        with_payload=True,
        limit=1,
    )[0]

    assert len(points) > 0, "Note not found in Qdrant!"
    payload = points[0].payload

    # Verify ALL required access control fields exist
    assert "project_id" in payload, "project_id missing from Qdrant payload!"
    assert "sensitivity" in payload, "sensitivity missing from Qdrant payload!"
    assert "people" in payload, "people missing from Qdrant payload!"

    # Verify values are correct
    assert payload["project_id"] == project_alpha.id
    assert payload["sensitivity"] == "internal"
    assert person_alice.id in payload["people"]


# --- Combined filter stress tests ---


def test_adversarial_all_filters_must_pass(
    db_session, qdrant, person_alice, person_bob, project_alpha, project_beta
):
    """
    STRESS TEST: All filters (person + project + sensitivity) must pass.

    Create items that fail each filter individually and verify none pass.
    """
    from memory.common.content_processing import process_content_item
    from memory.common.access_control import AccessCondition, AccessFilter
    from memory.api.search.embeddings import build_person_filter, build_access_qdrant_filter

    # Item 1: Wrong person (Bob's), right project, right sensitivity
    wrong_person = Note(
        content="Wrong person test content",
        modality="text",
        sha256=unique_sha256("stress-wrong-person"),
        project_id=project_alpha.id,
        sensitivity="basic",
    )
    wrong_person.people.append(person_bob)
    process_content_item(wrong_person, db_session)

    # Item 2: Right person (Alice's), wrong project, right sensitivity
    wrong_project = Note(
        content="Wrong project test content",
        modality="text",
        sha256=unique_sha256("stress-wrong-project"),
        project_id=project_beta.id,
        sensitivity="basic",
    )
    wrong_project.people.append(person_alice)
    process_content_item(wrong_project, db_session)

    # Item 3: Right person (Alice's), right project, wrong sensitivity
    wrong_sensitivity = Note(
        content="Wrong sensitivity test content",
        modality="text",
        sha256=unique_sha256("stress-wrong-sensitivity"),
        project_id=project_alpha.id,
        sensitivity="confidential",  # User only has basic access
    )
    wrong_sensitivity.people.append(person_alice)
    process_content_item(wrong_sensitivity, db_session)

    # Item 4: Passes all filters
    passes_all = Note(
        content="Passes all filters test content",
        modality="text",
        sha256=unique_sha256("stress-passes-all"),
        project_id=project_alpha.id,
        sensitivity="basic",
    )
    passes_all.people.append(person_alice)
    process_content_item(passes_all, db_session)

    # Search as Alice with contributor access to alpha only
    person_filter = build_person_filter(person_alice.id)
    access_filter = AccessFilter(
        conditions=[
            AccessCondition(
                project_id=project_alpha.id,
                sensitivities=frozenset({"basic"}),  # Contributor = basic only
            )
        ]
    )
    access_conditions = build_access_qdrant_filter(access_filter)

    combined_filter = {
        "must": [
            person_filter,
            {"should": access_conditions},
        ]
    }

    results = qdrant.scroll(
        collection_name="text",
        scroll_filter=combined_filter,
        with_payload=True,
        limit=100,
    )[0]

    source_ids = {p.payload["source_id"] for p in results}

    # Only item 4 should pass
    assert wrong_person.id not in source_ids, "Item with wrong person passed!"
    assert wrong_project.id not in source_ids, "Item with wrong project passed!"
    assert wrong_sensitivity.id not in source_ids, "Item with wrong sensitivity passed!"
    assert passes_all.id in source_ids, "Item that should pass was blocked!"


# --- BM25 and Qdrant consistency ---


def test_adversarial_qdrant_hides_confidential_even_for_same_person(
    db_session, qdrant, person_alice, project_alpha
):
    """
    VERIFY: Even content associated with the user is hidden if sensitivity is wrong.

    This tests that person association doesn't override sensitivity restrictions.
    Alice can see her own basic content but not her own confidential content
    (when she only has basic access).
    """
    from memory.common.content_processing import process_content_item
    from memory.common.access_control import AccessCondition, AccessFilter
    from memory.api.search.embeddings import build_person_filter, build_access_qdrant_filter

    # Create items with the search term - both associated with Alice
    visible_item = Note(
        content="Alice's visible basic content",
        modality="text",
        sha256=unique_sha256("sensitivity-override-visible"),
        project_id=project_alpha.id,
        sensitivity="basic",
    )
    visible_item.people.append(person_alice)
    process_content_item(visible_item, db_session)

    hidden_item = Note(
        content="Alice's hidden confidential content",
        modality="text",
        sha256=unique_sha256("sensitivity-override-hidden"),
        project_id=project_alpha.id,
        sensitivity="confidential",  # Alice only has basic access
    )
    hidden_item.people.append(person_alice)
    process_content_item(hidden_item, db_session)

    # Access filter: Alice with basic access to alpha
    access_filter = AccessFilter(
        conditions=[
            AccessCondition(
                project_id=project_alpha.id,
                sensitivities=frozenset({"basic"}),
            )
        ]
    )

    # Qdrant search
    person_filter = build_person_filter(person_alice.id)
    access_conditions = build_access_qdrant_filter(access_filter)
    qdrant_filter = {
        "must": [
            person_filter,
            {"should": access_conditions},
        ]
    }
    qdrant_results = qdrant.scroll(
        collection_name="text",
        scroll_filter=qdrant_filter,
        with_payload=True,
        limit=100,
    )[0]

    source_ids = {p.payload["source_id"] for p in qdrant_results}

    # Should see visible_item (Alice's basic content)
    assert visible_item.id in source_ids, "Can't see own basic content"

    # MUST NOT see hidden_item (Alice's confidential content - sensitivity overrides person)
    assert hidden_item.id not in source_ids, (
        "Person association overrode sensitivity restriction! "
        "Alice saw her own confidential content despite only having basic access."
    )


# --- Missing payload fields (legacy items) ---


def test_adversarial_missing_payload_fields_are_blocked(
    db_session, qdrant, project_alpha
):
    """
    BLOCKING TEST: Items missing access control fields should be blocked.

    Legacy items that were indexed before access control was added may not
    have project_id, sensitivity, or people fields. These should NOT be
    visible to regular users (fail closed, not fail open).
    """
    from memory.common.content_processing import process_content_item
    from memory.common.access_control import AccessCondition, AccessFilter
    from memory.api.search.embeddings import build_access_qdrant_filter
    from qdrant_client.http.models import PointStruct
    import uuid as uuid_module

    # Create a legitimate item with all fields
    legit_note = Note(
        content="Legitimate note with all access control fields",
        modality="text",
        sha256=unique_sha256("missing-fields-legit"),
        project_id=project_alpha.id,
        sensitivity="basic",
    )
    process_content_item(legit_note, db_session)

    # Manually insert a point into Qdrant WITHOUT access control fields
    # This simulates a legacy item that was indexed before access control
    legacy_point_id = str(uuid_module.uuid4())
    legacy_point = PointStruct(
        id=legacy_point_id,
        vector=[0.1] * 1024,  # Match mock embedder dimension
        payload={
            "source_id": 999999,  # Fake source ID
            "tags": ["legacy"],
            "size": 100,
            # NOTE: Intentionally missing project_id, sensitivity, people
        },
    )
    qdrant.upsert(
        collection_name="text",
        points=[legacy_point],
    )

    # User with admin access to project_alpha
    access_filter = AccessFilter(
        conditions=[
            AccessCondition(
                project_id=project_alpha.id,
                sensitivities=frozenset({"basic", "internal", "confidential"}),
            )
        ]
    )
    qdrant_conditions = build_access_qdrant_filter(access_filter)

    results = qdrant.scroll(
        collection_name="text",
        scroll_filter={"should": qdrant_conditions},
        with_payload=True,
        limit=100,
    )[0]

    result_ids = {str(p.id) for p in results}

    # Legacy point should NOT be found (missing fields = no match)
    # Qdrant filters require fields to exist for matching
    assert legacy_point_id not in result_ids, (
        "Legacy item without access control fields was returned! "
        "This is a security vulnerability - items should fail closed."
    )

    # But the legitimate item should be found
    legit_ids = {p.payload["source_id"] for p in results}
    assert legit_note.id in legit_ids, "Legitimate item not found"


# --- Superadmin verification ---


def test_adversarial_superadmin_sees_everything(
    db_session, qdrant, person_alice, person_bob, project_alpha
):
    """
    VERIFY: Superadmin (None filter) should see all items regardless of restrictions.

    This is the positive test for superadmin access.
    """
    from memory.common.content_processing import process_content_item
    from memory.api.search.embeddings import build_access_qdrant_filter

    # Create items with various restrictions
    null_project = Note(
        content="Unclassified superadmin-only content",
        modality="text",
        sha256=unique_sha256("superadmin-null"),
        project_id=None,
        sensitivity="basic",
    )
    process_content_item(null_project, db_session)

    confidential = Note(
        content="Confidential content for superadmin test",
        modality="text",
        sha256=unique_sha256("superadmin-confidential"),
        project_id=project_alpha.id,
        sensitivity="confidential",
    )
    process_content_item(confidential, db_session)

    bob_only = Note(
        content="Bob's private content for superadmin test",
        modality="text",
        sha256=unique_sha256("superadmin-bob"),
        project_id=project_alpha.id,
        sensitivity="basic",
    )
    bob_only.people.append(person_bob)
    process_content_item(bob_only, db_session)

    # Superadmin filter (None) should return empty conditions
    superadmin_conditions = build_access_qdrant_filter(None)
    assert superadmin_conditions == [], "Superadmin filter should be empty!"

    # Query Qdrant without access filter (superadmin)
    # For this test, we'll just verify the items exist and are indexable
    all_results = qdrant.scroll(
        collection_name="text",
        scroll_filter=None,  # No filter = see everything
        with_payload=True,
        limit=1000,
    )[0]

    source_ids = {p.payload["source_id"] for p in all_results}

    # Superadmin should see everything
    assert null_project.id in source_ids, "Superadmin can't see null project item"
    assert confidential.id in source_ids, "Superadmin can't see confidential item"
    assert bob_only.id in source_ids, "Superadmin can't see Bob's item"