"""Tests for the Person and PersonTidbit models."""

import pytest

from memory.common.db.models import Person, PersonTidbit
from memory.common.content_processing import create_content_hash


@pytest.fixture
def person_data():
    """Standard person test data."""
    return {
        "identifier": "alice_chen",
        "display_name": "Alice Chen",
        "aliases": ["@alice_c", "alice.chen@work.com"],
        "contact_info": {"email": "alice@example.com", "phone": "555-1234"},
    }


@pytest.fixture
def minimal_person_data():
    """Minimal person test data."""
    return {
        "identifier": "bob_smith",
        "display_name": "Bob Smith",
    }


def test_person_creation(person_data):
    """Test creating a Person with all fields."""
    person = Person(**person_data)

    assert person.identifier == "alice_chen"
    assert person.display_name == "Alice Chen"
    assert person.aliases == ["@alice_c", "alice.chen@work.com"]
    assert person.contact_info == {"email": "alice@example.com", "phone": "555-1234"}


def test_person_creation_minimal(minimal_person_data):
    """Test creating a Person with minimal fields."""
    person = Person(**minimal_person_data)

    assert person.identifier == "bob_smith"
    assert person.display_name == "Bob Smith"
    assert person.aliases == [] or person.aliases is None
    assert person.contact_info == {} or person.contact_info is None


def test_person_display_contents(person_data):
    """Test the display_contents property."""
    person = Person(**person_data)

    contents = person.display_contents

    assert contents["identifier"] == "alice_chen"
    assert contents["display_name"] == "Alice Chen"
    assert contents["aliases"] == ["@alice_c", "alice.chen@work.com"]
    assert contents["contact_info"] == {"email": "alice@example.com", "phone": "555-1234"}


@pytest.mark.parametrize(
    "identifier,display_name,aliases",
    [
        ("john_doe", "John Doe", []),
        ("jane_smith", "Jane Smith", ["@jane"]),
        ("bob_jones", "Bob Jones", ["@bob", "bobby"]),
        (
            "alice_wong",
            "Alice Wong",
            ["@alice", "alice@work.com", "Alice W."],
        ),
    ],
)
def test_person_various_configurations(identifier, display_name, aliases):
    """Test Person creation with various configurations."""
    person = Person(
        identifier=identifier,
        display_name=display_name,
        aliases=aliases,
    )

    assert person.identifier == identifier
    assert person.display_name == display_name
    assert person.aliases == aliases


def test_person_contact_info_flexible():
    """Test that contact_info can hold various structures."""
    contact_info = {
        "email": "test@example.com",
        "phone": "+1-555-1234",
        "twitter": "@testuser",
        "linkedin": "linkedin.com/in/testuser",
        "address": {
            "street": "123 Main St",
            "city": "San Francisco",
            "country": "USA",
        },
    }

    person = Person(
        identifier="test_user",
        display_name="Test User",
        contact_info=contact_info,
    )

    assert person.contact_info == contact_info
    assert person.contact_info["address"]["city"] == "San Francisco"


def test_person_in_db(db_session, qdrant):
    """Test Person persistence in database."""
    person = Person(
        identifier="db_test_user",
        display_name="DB Test User",
        aliases=["@dbtest"],
        contact_info={"email": "dbtest@example.com"},
    )

    db_session.add(person)
    db_session.commit()

    # Query it back
    retrieved = db_session.query(Person).filter_by(identifier="db_test_user").first()

    assert retrieved is not None
    assert retrieved.display_name == "DB Test User"
    assert retrieved.aliases == ["@dbtest"]
    assert retrieved.contact_info == {"email": "dbtest@example.com"}


def test_person_unique_identifier(db_session, qdrant):
    """Test that identifier must be unique."""
    person1 = Person(
        identifier="unique_test",
        display_name="Person 1",
    )
    db_session.add(person1)
    db_session.commit()

    # Try to add another with same identifier
    person2 = Person(
        identifier="unique_test",
        display_name="Person 2",
    )
    db_session.add(person2)

    with pytest.raises(Exception):  # Should raise IntegrityError
        db_session.commit()


def test_person_from_profile_markdown():
    """Test parsing profile markdown back to Person fields."""
    markdown = """---
identifier: john_doe
display_name: John Doe
aliases:
  - "@johnd"
  - john.doe@work.com
contact_info:
  email: john@example.com
  phone: "555-9876"
tags:
  - friend
  - climbing
---

Met John at the climbing gym. Great belayer."""

    data = Person.from_profile_markdown(markdown)

    assert data["identifier"] == "john_doe"
    assert data["display_name"] == "John Doe"
    assert data["aliases"] == ["@johnd", "john.doe@work.com"]
    assert data["contact_info"]["email"] == "john@example.com"
    assert data["contact_info"]["phone"] == "555-9876"
    assert data["tags"] == ["friend", "climbing"]
    assert "Met John at the climbing gym" in data["notes"]


def test_person_from_profile_markdown_no_frontmatter():
    """Test parsing markdown without frontmatter."""
    markdown = "Just some notes about a person."

    data = Person.from_profile_markdown(markdown)

    assert data["notes"] == "Just some notes about a person."
    assert "identifier" not in data


def test_person_from_profile_markdown_empty_body():
    """Test parsing markdown with frontmatter but no body."""
    markdown = """---
identifier: jane_smith
display_name: Jane Smith
---
"""

    data = Person.from_profile_markdown(markdown)

    assert data["identifier"] == "jane_smith"
    assert data["display_name"] == "Jane Smith"
    assert "notes" not in data or data.get("notes") is None


def test_person_get_profile_path():
    """Test getting the profile path for a person."""
    person = Person(
        identifier="test_user",
        display_name="Test User",
    )

    path = person.get_profile_path()

    # Should be in profiles folder with .md extension
    assert path.endswith(".md")
    assert "test_user" in path
    assert "/" in path  # Should have folder separator


def test_person_save_profile_note(tmp_path):
    """Test saving Person data to a profile note file."""
    from unittest.mock import patch

    person = Person(
        identifier="file_test_user",
        display_name="File Test User",
        aliases=["@filetest"],
        contact_info={"email": "filetest@example.com"},
    )

    with patch("memory.common.settings.NOTES_STORAGE_DIR", tmp_path):
        person.save_profile_note()

        # Verify file was created
        profile_path = tmp_path / "profiles" / "file_test_user.md"
        assert profile_path.exists()

        # Verify content
        content = profile_path.read_text()
        assert "identifier: file_test_user" in content
        assert "display_name: File Test User" in content
        assert "@filetest" in content
        assert "email: filetest@example.com" in content


def test_person_save_profile_note_creates_directory(tmp_path):
    """Test that save_profile_note creates the profiles directory if needed."""
    from unittest.mock import patch

    person = Person(
        identifier="dir_test_user",
        display_name="Dir Test User",
    )

    # profiles directory doesn't exist yet
    profiles_dir = tmp_path / "profiles"
    assert not profiles_dir.exists()

    with patch("memory.common.settings.NOTES_STORAGE_DIR", tmp_path):
        person.save_profile_note()

        # Directory should now exist
        assert profiles_dir.exists()
        assert (profiles_dir / "dir_test_user.md").exists()


# ============== PersonTidbit Tests ==============


@pytest.fixture
def tidbit_data():
    """Standard tidbit test data."""
    sha256 = create_content_hash("tidbit:test")
    return {
        "person_id": 1,  # Will be set in tests
        "creator_id": 1,
        "tidbit_type": "note",
        "content": "Very thorough in code reviews. Prefers morning meetings.",
        "tags": ["work", "preferences"],
        "modality": "person_tidbit",
        "mime_type": "text/plain",
        "sha256": sha256,
        "size": 50,
        "sensitivity": "basic",
    }


def test_tidbit_creation(tidbit_data):
    """Test creating a PersonTidbit with all fields."""
    tidbit = PersonTidbit(**tidbit_data)

    assert tidbit.person_id == 1
    assert tidbit.creator_id == 1
    assert tidbit.tidbit_type == "note"
    assert tidbit.content == "Very thorough in code reviews. Prefers morning meetings."
    assert tidbit.tags == ["work", "preferences"]
    assert tidbit.sensitivity == "basic"


def test_tidbit_polymorphic_identity():
    """Test that PersonTidbit has correct polymorphic identity."""
    assert PersonTidbit.__mapper_args__["polymorphic_identity"] == "person_tidbit"


def test_tidbit_get_collections():
    """Test that PersonTidbit returns correct collections."""
    collections = PersonTidbit.get_collections()
    assert collections == ["person_tidbit"]


def test_tidbit_in_db(db_session, qdrant):
    """Test PersonTidbit persistence with Person relationship."""
    # Create person first
    person = Person(
        identifier="tidbit_test_user",
        display_name="Tidbit Test User",
    )
    db_session.add(person)
    db_session.flush()

    # Create tidbit
    sha256 = create_content_hash("tidbit:db_test")
    tidbit = PersonTidbit(
        person_id=person.id,
        creator_id=None,
        tidbit_type="note",
        content="Test tidbit content",
        tags=["test"],
        modality="person_tidbit",
        mime_type="text/plain",
        sha256=sha256,
        size=20,
        sensitivity="basic",
    )
    db_session.add(tidbit)
    db_session.commit()

    # Query it back
    retrieved = db_session.query(PersonTidbit).filter_by(person_id=person.id).first()

    assert retrieved is not None
    assert retrieved.content == "Test tidbit content"
    assert retrieved.tidbit_type == "note"
    assert retrieved.person.identifier == "tidbit_test_user"


def test_person_tidbits_relationship(db_session, qdrant):
    """Test Person -> tidbits relationship."""
    # Create person
    person = Person(
        identifier="multi_tidbit_user",
        display_name="Multi Tidbit User",
    )
    db_session.add(person)
    db_session.flush()

    # Create multiple tidbits
    for i, tidbit_type in enumerate(["note", "preference", "fact"]):
        sha256 = create_content_hash(f"tidbit:multi_{i}")
        tidbit = PersonTidbit(
            person_id=person.id,
            tidbit_type=tidbit_type,
            content=f"Content for {tidbit_type}",
            modality="person_tidbit",
            mime_type="text/plain",
            sha256=sha256,
            size=20,
            sensitivity="basic",
        )
        db_session.add(tidbit)

    db_session.commit()

    # Query person with tidbits
    retrieved = db_session.query(Person).filter_by(identifier="multi_tidbit_user").first()

    assert retrieved is not None
    assert len(retrieved.tidbits) == 3
    tidbit_types = {t.tidbit_type for t in retrieved.tidbits}
    assert tidbit_types == {"note", "preference", "fact"}


def test_tidbit_cascade_delete(db_session, qdrant):
    """Test that deleting a Person cascades to tidbits."""
    # Create person
    person = Person(
        identifier="cascade_test_user",
        display_name="Cascade Test User",
    )
    db_session.add(person)
    db_session.flush()
    person_id = person.id

    # Create tidbit
    sha256 = create_content_hash("tidbit:cascade_test")
    tidbit = PersonTidbit(
        person_id=person.id,
        tidbit_type="note",
        content="Should be deleted with person",
        modality="person_tidbit",
        mime_type="text/plain",
        sha256=sha256,
        size=30,
        sensitivity="basic",
    )
    db_session.add(tidbit)
    db_session.commit()
    tidbit_id = tidbit.id

    # Delete person
    db_session.delete(person)
    db_session.commit()

    # Verify tidbit is also deleted
    assert db_session.get(Person, person_id) is None
    assert db_session.get(PersonTidbit, tidbit_id) is None


def test_tidbit_chunk_contents(db_session, qdrant):
    """Test that PersonTidbit generates searchable chunks."""
    # Create person
    person = Person(
        identifier="chunk_test_user",
        display_name="Chunk Test User",
    )
    db_session.add(person)
    db_session.flush()

    # Create tidbit
    sha256 = create_content_hash("tidbit:chunk_test")
    tidbit = PersonTidbit(
        person_id=person.id,
        tidbit_type="preference",
        content="Prefers morning meetings. Drinks black coffee.",
        tags=["scheduling", "food"],
        modality="person_tidbit",
        mime_type="text/plain",
        sha256=sha256,
        size=50,
        sensitivity="basic",
    )
    db_session.add(tidbit)
    db_session.flush()

    # Attach person relationship
    tidbit.person = person

    chunks = tidbit._chunk_contents()

    assert len(chunks) > 0
    chunk_text = str(chunks[0].data[0])

    # Should include person name
    assert "Chunk Test User" in chunk_text
    # Should include type
    assert "preference" in chunk_text
    # Should include content
    assert "morning meetings" in chunk_text


def test_tidbit_access_control_fields(db_session, qdrant):
    """Test that PersonTidbit has access control fields."""
    # Create person
    person = Person(
        identifier="access_test_user",
        display_name="Access Test User",
    )
    db_session.add(person)
    db_session.flush()

    # Create tidbit with access control
    sha256 = create_content_hash("tidbit:access_test")
    tidbit = PersonTidbit(
        person_id=person.id,
        creator_id=42,
        tidbit_type="note",
        content="Private note",
        modality="person_tidbit",
        mime_type="text/plain",
        sha256=sha256,
        size=12,
        project_id=None,  # Creator-only
        sensitivity="confidential",
    )
    db_session.add(tidbit)
    db_session.commit()

    retrieved = db_session.get(PersonTidbit, tidbit.id)

    assert retrieved.creator_id == 42
    assert retrieved.project_id is None
    assert retrieved.sensitivity == "confidential"
