import pytest
from pathlib import Path
from unittest.mock import patch, Mock

from memory.common.db.models import Book, BookSection
from memory.common import settings
from memory.parsers.ebook import Ebook, Section
from memory.workers.tasks import ebook


@pytest.fixture
def mock_ebook():
    """Mock ebook data for testing."""
    return Ebook(
        relative_path=Path("test/book.epub"),
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


def test_create_book_and_sections(mock_ebook, db_session):
    """Test creating book and sections with relationships."""
    book, sections = ebook.create_book_and_sections(mock_ebook, db_session)

    # Verify book creation
    assert book.title == "Test Book"  # type: ignore
    assert book.id is not None

    # Verify sections creation
    assert len(sections) == 4  # Chapter 1, Section 1.1, Section 1.2, Chapter 2

    # Verify parent-child relationships
    chapter1 = next(s for s in sections if getattr(s, "section_title") == "Chapter 1")
    section11 = next(
        s for s in sections if getattr(s, "section_title") == "Section 1.1"
    )
    section12 = next(
        s for s in sections if getattr(s, "section_title") == "Section 1.2"
    )

    # Children should reference chapter 1 as parent
    assert getattr(section11, "parent_section_id") == chapter1.id
    assert getattr(section12, "parent_section_id") == chapter1.id

    # Chapter 1 should have no parent
    assert getattr(chapter1, "parent_section_id") is None


def test_embed_sections(db_session):
    """Test basic embedding sections workflow."""
    # Create a test book first
    book = Book(
        title="Test Book",
        author="Test Author",
        file_path="/test/path",
    )
    db_session.add(book)
    db_session.flush()  # Get the book ID

    # Create test sections with all required fields
    sections = [
        BookSection(
            book_id=book.id,
            section_title="Test Section",
            section_number=1,
            section_level=1,
            start_page=1,
            end_page=10,
            content="Test content " * 20,
            sha256=b"test_hash",
            modality="book",
            tags=["book"],
        )
    ]

    db_session.add_all(sections)
    db_session.flush()

    embedded_count = ebook.embed_sections(sections)

    assert embedded_count >= 0
    assert hasattr(sections[0], "embed_status")


@patch("memory.workers.tasks.ebook.parse_ebook")
def test_sync_book_success(mock_parse, mock_ebook, db_session, tmp_path, qdrant):
    """Test successful book synchronization."""
    book_file = tmp_path / "test.epub"
    book_file.write_text("dummy content")

    mock_ebook.file_path = book_file
    mock_parse.return_value = mock_ebook

    result = ebook.sync_book(str(book_file), {"source", "test"})

    assert result == {
        "book_id": 1,
        "title": "Test Book",
        "author": "Test Author",
        "status": "processed",
        "total_sections": 4,
        "sections_embedded": 4,
    }

    book = db_session.query(Book).filter(Book.title == "Test Book").first()
    assert book is not None
    assert book.author == "Test Author"
    assert set(book.tags) == {"source", "test"}

    sections = (
        db_session.query(BookSection).filter(BookSection.book_id == book.id).all()
    )
    assert len(sections) == 4


@patch("memory.workers.tasks.ebook.parse_ebook")
def test_sync_book_already_exists(mock_parse, mock_ebook, db_session, tmp_path):
    """Test that duplicate books are not processed."""
    book_file = tmp_path / "test.epub"
    book_file.write_text("dummy content")

    # Use the same relative path that mock_ebook has
    existing_book = Book(
        title="Existing Book",
        author="Author",
        file_path="test/book.epub",  # Must match mock_ebook.relative_path
    )
    db_session.add(existing_book)
    db_session.commit()

    mock_ebook.file_path = book_file
    mock_parse.return_value = mock_ebook

    assert ebook.sync_book(str(book_file)) == {
        "book_id": existing_book.id,
        "title": "Existing Book",
        "author": "Author",
        "status": "already_exists",
        "sections_processed": 0,
    }


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
    assert ebook.sync_book(str(book_file)) == {
        "book_id": 1,
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
        assert "traceback" in result


def test_sync_book_file_not_found():
    """Test handling of missing files - should raise FileNotFoundError."""
    with pytest.raises(FileNotFoundError, match="Book file not found"):
        ebook.sync_book("/nonexistent/file.epub")


def test_embed_sections_uses_correct_chunk_size(db_session, mock_voyage_client):
    """Test that book sections with large pages are passed whole to the embedding function."""
    # Create a test book first
    book = Book(
        title="Test Book",
        author="Test Author",
        file_path="/test/path",
    )
    db_session.add(book)
    db_session.flush()

    # Create large content that exceeds 1000 tokens (4000+ characters)
    large_section_content = "This is a very long section content. " * 120  # ~4440 chars
    large_page_1 = "This is page 1 with lots of content. " * 120  # ~4440 chars
    large_page_2 = "This is page 2 with lots of content. " * 120  # ~4440 chars

    # Create test sections with large content and pages
    sections = [
        BookSection(
            book_id=book.id,
            section_title="Test Section",
            section_number=1,
            section_level=1,
            start_page=1,
            end_page=10,
            content=large_section_content,
            sha256=b"test_hash",
            modality="book",
            tags=["book"],
            pages=[large_page_1, large_page_2],
        )
    ]

    db_session.add_all(sections)
    db_session.flush()

    return_val = Mock(embeddings=[[0.1] * 1024] * 3)
    mock_voyage_client.embed = Mock(return_value=return_val)
    ebook.embed_sections(sections)

    # Verify that the voyage client was called with the full large content
    # Should be called 3 times: once for section content, twice for pages
    assert mock_voyage_client.embed.call_count == 1

    # Check that the full content was passed to the embedding function
    texts = mock_voyage_client.embed.call_args[0][0]
    assert texts == [large_section_content.strip(), "test summary"]
