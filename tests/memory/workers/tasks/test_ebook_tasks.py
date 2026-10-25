import pytest
from unittest.mock import patch, Mock

from memory.common.db.models import Book, BookSection, PendingJob
from memory.common import settings
from memory.parsers.ebook import Ebook, Section
from memory.workers.tasks import ebook


@pytest.fixture
def mock_ebook():
    """Mock ebook data for testing."""
    return Ebook(
        relative_path="test/book.epub",
        title="Test Book",
        author="Test Author",
        metadata={"language": "en", "creator": "Test Publisher"},
        sections=[
            Section(
                title="Chapter 1",
                pages=["This is the content of chapter 1. " * 20],
                number=1,
                start_page=1,
                end_page=10,
                children=[
                    Section(
                        title="Section 1.1",
                        pages=["This is section 1.1 content. " * 15],
                        number=1,
                        start_page=1,
                        end_page=5,
                    ),
                    Section(
                        title="Section 1.2",
                        pages=["This is section 1.2 content. " * 15],
                        number=2,
                        start_page=6,
                        end_page=10,
                    ),
                ],
            ),
            Section(
                title="Chapter 2",
                pages=["This is the content of chapter 2. " * 20],
                number=2,
                start_page=11,
                end_page=20,
            ),
        ],
        file_path=settings.FILE_STORAGE_DIR / "test/book.epub",
        n_pages=20,
    )


@pytest.fixture
def mock_qdrant():
    """Mock Qdrant operations."""
    with (
        patch("memory.workers.tasks.ebook.qdrant.upsert_vectors") as mock_upsert,
        patch("memory.workers.tasks.ebook.qdrant.get_qdrant_client") as mock_client,
    ):
        mock_client.return_value = Mock()
        yield mock_upsert


def test_create_book_from_ebook(mock_ebook):
    """Test creating a Book model from ebook data."""
    book = ebook.create_book_from_ebook(mock_ebook)

    assert book.title == "Test Book"  # type: ignore
    assert book.author == "Test Author"  # type: ignore
    assert book.publisher == "Test Publisher"  # type: ignore
    assert book.language == "en"  # type: ignore
    assert book.file_path == "test/book.epub"  # type: ignore
    assert book.total_pages == 20  # type: ignore
    assert book.book_metadata == {  # type: ignore
        "language": "en",
        "creator": "Test Publisher",
    }


def test_validate_and_parse_book_success(mock_ebook, tmp_path):
    """Test successful book validation and parsing."""
    book_file = tmp_path / "test.epub"
    book_file.write_text("dummy content")

    with patch("memory.workers.tasks.ebook.parse_ebook", return_value=mock_ebook):
        assert ebook.validate_and_parse_book(str(book_file)) == mock_ebook


def test_validate_and_parse_book_file_not_found():
    """Test handling of missing files."""
    with pytest.raises(FileNotFoundError):
        ebook.validate_and_parse_book("/nonexistent/file.epub")


def test_validate_and_parse_book_parse_error(tmp_path):
    """Test handling of parsing errors."""
    book_file = tmp_path / "corrupted.epub"
    book_file.write_text("corrupted data")

    with patch(
        "memory.workers.tasks.ebook.parse_ebook", side_effect=Exception("Parse error")
    ):
        with pytest.raises(Exception, match="Parse error"):
            ebook.validate_and_parse_book(str(book_file))


@patch("memory.workers.tasks.ebook.parse_ebook")
def test_sync_book_success(mock_parse, mock_ebook, db_session, tmp_path, qdrant):
    """Test successful book synchronization."""
    book_file = tmp_path / "test.epub"
    book_file.write_text("dummy content")

    mock_ebook.file_path = book_file
    mock_parse.return_value = mock_ebook

    result = ebook.sync_book(str(book_file), {"source", "test"})

    book = db_session.query(Book).filter(Book.title == "Test Book").first()
    assert book is not None
    assert result == {
        "book_id": book.id,
        "title": "Test Book",
        "author": "Test Author",
        "status": "processed",
        "total_sections": 4,
        "sections_embedded": 4,
    }

    assert book is not None
    assert book.author == "Test Author"
    assert set(book.tags) == {"source", "test"}

    sections = {
        s.section_title: s
        for s in db_session.query(BookSection).filter(BookSection.book_id == book.id)
    }
    assert set(sections) == {"Chapter 1", "Section 1.1", "Section 1.2", "Chapter 2"}
    chapter1 = sections["Chapter 1"]
    assert chapter1.parent_section_id is None
    assert sections["Section 1.1"].parent_section_id == chapter1.id
    assert sections["Section 1.2"].parent_section_id == chapter1.id
    assert sections["Chapter 2"].parent_section_id is None


def add_existing_book(db_session, *creators):
    book = Book(title="Existing Book", author="Author", file_path="test/book.epub")
    db_session.add(book)
    db_session.flush()
    db_session.add_all(
        BookSection(
            book_id=book.id,
            section_title="Chapter",
            modality="book",
            sha256=f"existing-section-{creator.id}".encode(),
            content="Chapter content",
            creator_id=creator.id,
        )
        for creator in creators
    )
    db_session.commit()
    return book


@pytest.fixture
def parsed_book(mock_ebook, tmp_path):
    book_file = tmp_path / "test.epub"
    book_file.write_text("dummy content")
    mock_ebook.file_path = book_file
    with patch("memory.workers.tasks.ebook.parse_ebook", return_value=mock_ebook):
        yield book_file


@pytest.mark.parametrize(
    "existing_creator, uploader, scopes, deduped",
    [
        ("admin", "admin", ["read"], True),
        ("admin", None, None, True),  # an operator may see everything
        ("admin", "regular", ["read"], False),
        ("regular", "admin", ["*"], True),
        ("regular", "admin", ["read"], False),  # an admin's narrowed key
    ],
)
def test_sync_book_dedups_only_onto_a_book_the_uploader_can_read(
    parsed_book, db_session, qdrant, admin_user, regular_user,
    existing_creator, uploader, scopes, deduped,
):
    users = {"admin": admin_user, "regular": regular_user}
    existing_book = add_existing_book(db_session, users[existing_creator])
    creator_id = users.get(uploader) and users[uploader].id

    result = ebook.sync_book(str(parsed_book), creator_id=creator_id, creator_scopes=scopes)

    assert (result.get("status") == "already_exists") == deduped
    assert (result.get("book_id") == existing_book.id) == deduped


def test_sync_book_does_not_dedup_onto_a_book_without_sections(
    parsed_book, db_session, qdrant, admin_user
):
    existing_book = add_existing_book(db_session)

    result = ebook.sync_book(str(parsed_book), creator_id=admin_user.id, creator_scopes=["*"])

    assert result.get("status") == "processed"
    assert result.get("book_id") != existing_book.id


@patch("memory.workers.tasks.ebook.parse_ebook")
def test_sync_book_without_long_enough_sections_fails_the_job(
    mock_parse, mock_ebook, db_session, tmp_path
):
    book_file = tmp_path / "test.epub"
    book_file.write_text("dummy content")
    mock_ebook.file_path = book_file
    mock_ebook.sections = [Section(title="Cover", pages=["short"], number=1)]
    mock_parse.return_value = mock_ebook
    job = PendingJob(job_type="content_ingest")
    db_session.add(job)
    db_session.commit()

    result = ebook.sync_book(str(book_file), job_id=job.id)

    db_session.refresh(job)
    assert result == {"status": "error", "error": job.error_message}
    assert job.status == "failed"
    assert "no section with at least 100 characters" in (job.error_message or "")
    assert db_session.query(Book).count() == 0


@patch("memory.workers.tasks.ebook.parse_ebook")
@patch("memory.common.embedding.embed_source_item")
def test_sync_book_embedding_failure(
    mock_embedding, mock_parse, mock_ebook, db_session, tmp_path
):
    """Test handling of embedding failures."""
    book_file = tmp_path / "test.epub"
    book_file.write_text("dummy content")

    mock_ebook.file_path = book_file
    mock_parse.return_value = mock_ebook

    mock_embedding.side_effect = IOError("Embedding failed")
    result = ebook.sync_book(str(book_file))
    book = db_session.query(Book).filter(Book.title == "Test Book").first()
    assert book is not None
    assert result == {
        "book_id": book.id,
        "title": "Test Book",
        "author": "Test Author",
        "status": "processed",
        "sections_embedded": 0,
        "total_sections": 4,
    }

    sections = db_session.query(BookSection).all()
    for section in sections:
        assert section.embed_status == "FAILED"


@patch("memory.workers.tasks.ebook.parse_ebook")
def test_sync_book_qdrant_failure(mock_parse, mock_ebook, db_session, tmp_path):
    """Test handling of Qdrant failures."""
    book_file = tmp_path / "test.epub"
    book_file.write_text("dummy content")

    mock_ebook.file_path = book_file
    mock_parse.return_value = mock_ebook

    # Since embedding is already failing, this test will complete without hitting Qdrant
    # So let's just verify that the function completes without raising an exception
    with patch.object(ebook, "push_to_qdrant", side_effect=Exception("Qdrant failed")):
        result = ebook.sync_book(str(book_file))
        assert result.get("status") == "error"
        assert result.get("error") == "Qdrant failed"


def test_sync_book_file_not_found():
    """Test handling of missing files - should raise FileNotFoundError."""
    with pytest.raises(FileNotFoundError, match="Book file not found"):
        ebook.sync_book("/nonexistent/file.epub")
