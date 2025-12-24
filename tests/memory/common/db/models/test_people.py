"""Tests for the Person model."""

import pytest

from memory.common.db.models import Person
from memory.workers.tasks.content_processing import create_content_hash


@pytest.fixture
def person_data():
    """Standard person test data."""
    return {
        "identifier": "alice_chen",
        "display_name": "Alice Chen",
        "aliases": ["@alice_c", "alice.chen@work.com"],
        "contact_info": {"email": "alice@example.com", "phone": "555-1234"},
        "tags": ["work", "engineering"],
        "content": "Tech lead on Platform team. Very thorough in code reviews.",
        "modality": "person",
        "mime_type": "text/plain",
    }


@pytest.fixture
def minimal_person_data():
    """Minimal person test data."""
    return {
        "identifier": "bob_smith",
        "display_name": "Bob Smith",
        "modality": "person",
    }


def test_person_creation(person_data):
    """Test creating a Person with all fields."""
    sha256 = create_content_hash(f"person:{person_data['identifier']}")
    person = Person(**person_data, sha256=sha256, size=100)

    assert person.identifier == "alice_chen"
    assert person.display_name == "Alice Chen"
    assert person.aliases == ["@alice_c", "alice.chen@work.com"]
    assert person.contact_info == {"email": "alice@example.com", "phone": "555-1234"}
    assert person.tags == ["work", "engineering"]
    assert person.content == "Tech lead on Platform team. Very thorough in code reviews."
    assert person.modality == "person"


def test_person_creation_minimal(minimal_person_data):
    """Test creating a Person with minimal fields."""
    sha256 = create_content_hash(f"person:{minimal_person_data['identifier']}")
    person = Person(**minimal_person_data, sha256=sha256, size=0)

    assert person.identifier == "bob_smith"
    assert person.display_name == "Bob Smith"
    assert person.aliases == [] or person.aliases is None
    assert person.contact_info == {} or person.contact_info is None
    assert person.tags == [] or person.tags is None
    assert person.content is None


def test_person_as_payload(person_data):
    """Test the as_payload method."""
    sha256 = create_content_hash(f"person:{person_data['identifier']}")
    person = Person(**person_data, sha256=sha256, size=100)

    payload = person.as_payload()

    assert payload["identifier"] == "alice_chen"
    assert payload["display_name"] == "Alice Chen"
    assert payload["aliases"] == ["@alice_c", "alice.chen@work.com"]
    assert payload["contact_info"] == {"email": "alice@example.com", "phone": "555-1234"}


def test_person_display_contents(person_data):
    """Test the display_contents property."""
    sha256 = create_content_hash(f"person:{person_data['identifier']}")
    person = Person(**person_data, sha256=sha256, size=100)

    contents = person.display_contents

    assert contents["identifier"] == "alice_chen"
    assert contents["display_name"] == "Alice Chen"
    assert contents["aliases"] == ["@alice_c", "alice.chen@work.com"]
    assert contents["contact_info"] == {"email": "alice@example.com", "phone": "555-1234"}
    assert contents["notes"] == "Tech lead on Platform team. Very thorough in code reviews."
    assert contents["tags"] == ["work", "engineering"]


def test_person_chunk_contents(person_data):
    """Test the _chunk_contents method generates searchable chunks."""
    sha256 = create_content_hash(f"person:{person_data['identifier']}")
    person = Person(**person_data, sha256=sha256, size=100)

    chunks = person._chunk_contents()

    assert len(chunks) > 0
    chunk_text = chunks[0].data[0]

    # Should include display name
    assert "Alice Chen" in chunk_text
    # Should include aliases
    assert "@alice_c" in chunk_text
    # Should include tags
    assert "work" in chunk_text
    # Should include notes/content
    assert "Tech lead" in chunk_text


def test_person_chunk_contents_minimal(minimal_person_data):
    """Test _chunk_contents with minimal data."""
    sha256 = create_content_hash(f"person:{minimal_person_data['identifier']}")
    person = Person(**minimal_person_data, sha256=sha256, size=0)

    chunks = person._chunk_contents()

    assert len(chunks) > 0
    chunk_text = chunks[0].data[0]
    assert "Bob Smith" in chunk_text


def test_person_get_collections():
    """Test that Person returns correct collections."""
    collections = Person.get_collections()

    assert collections == ["person"]


def test_person_polymorphic_identity():
    """Test that Person has correct polymorphic identity."""
    assert Person.__mapper_args__["polymorphic_identity"] == "person"


@pytest.mark.parametrize(
    "identifier,display_name,aliases,tags",
    [
        ("john_doe", "John Doe", [], []),
        ("jane_smith", "Jane Smith", ["@jane"], ["friend"]),
        ("bob_jones", "Bob Jones", ["@bob", "bobby"], ["work", "climbing", "london"]),
        (
            "alice_wong",
            "Alice Wong",
            ["@alice", "alice@work.com", "Alice W."],
            ["family", "close"],
        ),
    ],
)
def test_person_various_configurations(identifier, display_name, aliases, tags):
    """Test Person creation with various configurations."""
    sha256 = create_content_hash(f"person:{identifier}")
    person = Person(
        identifier=identifier,
        display_name=display_name,
        aliases=aliases,
        tags=tags,
        modality="person",
        sha256=sha256,
        size=0,
    )

    assert person.identifier == identifier
    assert person.display_name == display_name
    assert person.aliases == aliases
    assert person.tags == tags


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

    sha256 = create_content_hash("person:test_user")
    person = Person(
        identifier="test_user",
        display_name="Test User",
        contact_info=contact_info,
        modality="person",
        sha256=sha256,
        size=0,
    )

    assert person.contact_info == contact_info
    assert person.contact_info["address"]["city"] == "San Francisco"


def test_person_in_db(db_session, qdrant):
    """Test Person persistence in database."""
    sha256 = create_content_hash("person:db_test_user")
    person = Person(
        identifier="db_test_user",
        display_name="DB Test User",
        aliases=["@dbtest"],
        contact_info={"email": "dbtest@example.com"},
        tags=["test"],
        content="Test notes",
        modality="person",
        mime_type="text/plain",
        sha256=sha256,
        size=10,
    )

    db_session.add(person)
    db_session.commit()

    # Query it back
    retrieved = db_session.query(Person).filter_by(identifier="db_test_user").first()

    assert retrieved is not None
    assert retrieved.display_name == "DB Test User"
    assert retrieved.aliases == ["@dbtest"]
    assert retrieved.contact_info == {"email": "dbtest@example.com"}
    assert retrieved.tags == ["test"]
    assert retrieved.content == "Test notes"


def test_person_unique_identifier(db_session, qdrant):
    """Test that identifier must be unique."""
    sha256 = create_content_hash("person:unique_test")

    person1 = Person(
        identifier="unique_test",
        display_name="Person 1",
        modality="person",
        sha256=sha256,
        size=0,
    )
    db_session.add(person1)
    db_session.commit()

    # Try to add another with same identifier
    person2 = Person(
        identifier="unique_test",
        display_name="Person 2",
        modality="person",
        sha256=create_content_hash("person:unique_test_2"),
        size=0,
    )
    db_session.add(person2)

    with pytest.raises(Exception):  # Should raise IntegrityError
        db_session.commit()
