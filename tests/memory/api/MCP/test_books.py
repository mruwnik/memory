"""Tests for MCP books server."""
# pyright: reportFunctionMemberAccess=false

import pytest
from unittest.mock import MagicMock, patch

from memory.api.MCP.servers.books import fetch, list_books
from memory.common.content_processing import create_content_hash
from memory.common.db import connection as db_connection
from memory.common.db.models import Book, BookSection, JournalEntry
from tests.conftest import mcp_auth_context


def get_fn(tool):
    """Extract underlying function from FunctionTool if wrapped."""
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


# ====== list_books tests ======


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.books.make_session")
async def test_list_books_returns_all_books(mock_make_session):
    """List books returns all books without filters."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_book1 = MagicMock()
    mock_book1.as_payload.return_value = {
        "id": 1,
        "title": "Python Programming",
        "author": "John Doe",
    }
    mock_book2 = MagicMock()
    mock_book2.as_payload.return_value = {
        "id": 2,
        "title": "JavaScript Basics",
        "author": "Jane Smith",
    }

    query_mock = mock_session.query.return_value
    query_mock.options.return_value = query_mock
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = [mock_book1, mock_book2]

    result = await list_books.fn()

    assert len(result) == 2
    assert result[0]["title"] == "Python Programming"
    assert result[1]["title"] == "JavaScript Basics"


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.books.make_session")
async def test_list_books_filters_by_title(mock_make_session):
    """List books filters by title substring."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    query_mock = mock_session.query.return_value
    query_mock.options.return_value = query_mock
    query_mock.filter.return_value = query_mock
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = []

    await list_books.fn(title="Python")

    # Verify filter was called with ilike
    query_mock.filter.assert_called_once()


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.books.make_session")
async def test_list_books_filters_by_author(mock_make_session):
    """List books filters by author substring."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    query_mock = mock_session.query.return_value
    query_mock.options.return_value = query_mock
    query_mock.filter.return_value = query_mock
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = []

    await list_books.fn(author="John")

    # Verify filter was called
    query_mock.filter.assert_called_once()


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.books.make_session")
async def test_list_books_filters_by_tags(mock_make_session):
    """List books filters by tags using array overlap."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    query_mock = mock_session.query.return_value
    query_mock.options.return_value = query_mock
    query_mock.filter.return_value = query_mock
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = []

    await list_books.fn(tags=["programming", "python"])

    # Verify filter was called for tags
    query_mock.filter.assert_called_once()


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.books.make_session")
async def test_list_books_with_sections(mock_make_session):
    """List books includes sections when requested."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_book = MagicMock()
    mock_book.as_payload.return_value = {
        "id": 1,
        "title": "Python Book",
        "sections": [{"id": 1, "title": "Chapter 1"}],
    }

    query_mock = mock_session.query.return_value
    query_mock.options.return_value = query_mock
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = [mock_book]

    result = await list_books.fn(sections=True)

    # Verify as_payload called with sections=True
    mock_book.as_payload.assert_called_once_with(sections=True)
    assert len(result) == 1


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.books.make_session")
async def test_list_books_without_sections(mock_make_session):
    """List books excludes sections by default."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_book = MagicMock()
    mock_book.as_payload.return_value = {"id": 1, "title": "Python Book"}

    query_mock = mock_session.query.return_value
    query_mock.options.return_value = query_mock
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = [mock_book]

    await list_books.fn(sections=False)

    # Verify as_payload called with sections=False
    mock_book.as_payload.assert_called_once_with(sections=False)


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.books.make_session")
async def test_list_books_pagination(mock_make_session):
    """List books supports pagination with limit and offset."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    query_mock = mock_session.query.return_value
    query_mock.options.return_value = query_mock
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = []

    await list_books.fn(limit=10, offset=20)

    query_mock.offset.assert_called_once_with(20)
    query_mock.limit.assert_called_once_with(10)


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.books.make_session")
async def test_list_books_enforces_max_limit(mock_make_session):
    """List books enforces max limit of 200."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    query_mock = mock_session.query.return_value
    query_mock.options.return_value = query_mock
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = []

    await list_books.fn(limit=500)

    # Should cap at 200
    query_mock.limit.assert_called_once_with(200)


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.books.make_session")
async def test_list_books_orders_by_title(mock_make_session):
    """List books orders results by title."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    query_mock = mock_session.query.return_value
    query_mock.options.return_value = query_mock
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = []

    await list_books.fn()

    # Verify order_by was called
    query_mock.order_by.assert_called_once()


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.books.make_session")
async def test_list_books_combines_filters(mock_make_session):
    """List books can combine multiple filters."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    query_mock = mock_session.query.return_value
    query_mock.options.return_value = query_mock
    query_mock.filter.return_value = query_mock
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = []

    await list_books.fn(title="Python", author="John", tags=["programming"])

    # Should have multiple filter calls
    assert query_mock.filter.call_count == 3


# ====== fetch tests ======


@patch("memory.api.MCP.servers.books.make_session")
def test_fetch_returns_all_sections(mock_make_session):
    """Read book returns all sections when no specific sections requested."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_section1 = MagicMock()
    mock_section1.id = 1
    mock_section1.parent_section_id = None
    mock_section1.as_payload.return_value = {
        "id": 1,
        "title": "Chapter 1",
        "content": "Content 1",
    }

    mock_section2 = MagicMock()
    mock_section2.id = 2
    mock_section2.parent_section_id = None
    mock_section2.as_payload.return_value = {
        "id": 2,
        "title": "Chapter 2",
        "content": "Content 2",
    }

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.all.return_value = [mock_section1, mock_section2]

    result = fetch.fn(book_id=1)

    assert len(result) == 2
    assert result[0]["title"] == "Chapter 1"
    assert result[1]["title"] == "Chapter 2"


@patch("memory.api.MCP.servers.books.make_session")
def test_fetch_filters_by_section_ids(mock_make_session):
    """Read book filters by specific section IDs."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_section = MagicMock()
    mock_section.id = 1
    mock_section.parent_section_id = None
    mock_section.as_payload.return_value = {"id": 1, "title": "Chapter 1"}

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.all.return_value = [mock_section]

    result = fetch.fn(book_id=1, sections=[1, 2])

    # Should have two filter calls (book_id and section IDs)
    assert query_mock.filter.call_count == 2
    assert len(result) == 1


@patch("memory.api.MCP.servers.books.make_session")
def test_fetch_returns_leaf_sections(mock_make_session):
    """Read book returns only leaf sections (sections without children)."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_parent = MagicMock()
    mock_parent.id = 1
    mock_parent.parent_section_id = None
    mock_parent.as_payload.return_value = {"id": 1, "title": "Chapter 1"}

    mock_child = MagicMock()
    mock_child.id = 2
    mock_child.parent_section_id = 1  # Child of section 1
    mock_child.as_payload.return_value = {"id": 2, "title": "Section 1.1"}

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.all.return_value = [mock_parent, mock_child]

    result = fetch.fn(book_id=1)

    # Should only return child/leaf section (id=2)
    # Parent section (id=1) is excluded because it has children
    assert len(result) == 1
    assert result[0]["id"] == 2


@patch("memory.api.MCP.servers.books.make_session")
def test_fetch_empty_when_no_sections(mock_make_session):
    """Read book returns empty list when book has no sections."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.all.return_value = []

    result = fetch.fn(book_id=999)

    assert result == []


@patch("memory.api.MCP.servers.books.make_session")
def test_fetch_with_nested_structure(mock_make_session):
    """Read book correctly handles nested section structures."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    # Create a nested structure: 1 -> 2 -> 3
    mock_sec1 = MagicMock()
    mock_sec1.id = 1
    mock_sec1.parent_section_id = None
    mock_sec1.as_payload.return_value = {"id": 1, "title": "Part 1"}

    mock_sec2 = MagicMock()
    mock_sec2.id = 2
    mock_sec2.parent_section_id = 1
    mock_sec2.as_payload.return_value = {"id": 2, "title": "Chapter 1"}

    mock_sec3 = MagicMock()
    mock_sec3.id = 3
    mock_sec3.parent_section_id = 2
    mock_sec3.as_payload.return_value = {"id": 3, "title": "Section 1.1"}

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.all.return_value = [mock_sec1, mock_sec2, mock_sec3]

    result = fetch.fn(book_id=1)

    # Should only return the deepest leaf section (id=3)
    # Sections 1 and 2 have children so are excluded
    assert len(result) == 1
    assert result[0]["id"] == 3


@patch("memory.api.MCP.servers.books.make_session")
def test_fetch_multiple_leaf_sections(mock_make_session):
    """Read book returns multiple leaf sections."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_sec1 = MagicMock()
    mock_sec1.id = 1
    mock_sec1.parent_section_id = None
    mock_sec1.as_payload.return_value = {"id": 1, "title": "Part 1"}

    mock_sec2 = MagicMock()
    mock_sec2.id = 2
    mock_sec2.parent_section_id = None
    mock_sec2.as_payload.return_value = {"id": 2, "title": "Part 2"}

    mock_child = MagicMock()
    mock_child.id = 3
    mock_child.parent_section_id = 1
    mock_child.as_payload.return_value = {"id": 3, "title": "Chapter 1.1"}

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.all.return_value = [mock_sec1, mock_sec2, mock_child]

    result = fetch.fn(book_id=1)

    # Should return leaf sections (2 and 3)
    # Section 1 has a child (3), so it's excluded
    # Section 2 has no children, so it's a leaf
    # Section 3 is a child of 1, and has no children, so it's a leaf
    assert len(result) == 2
    assert {r["id"] for r in result} == {2, 3}


def test_fetch_with_include_journal(db_session, admin_user, admin_session):
    """Fetch with include_journal=True returns sections and journal entries."""
    # Create a book
    book = Book(title="Test Book", author="Test Author")
    db_session.add(book)
    db_session.flush()

    # Create a section for the book
    section = BookSection(
        book_id=book.id,
        section_title="Chapter 1",
        section_number=1,
        section_level=1,
        modality="text",
        sha256=create_content_hash("chapter 1 content"),
        content="Chapter 1 content",
    )
    db_session.add(section)
    db_session.flush()

    # Create a journal entry for the book section
    journal_entry = JournalEntry(
        target_type="source_item",
        target_id=section.id,
        creator_id=admin_user.id,
        content="Note about this book",
    )
    db_session.add(journal_entry)
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = get_fn(fetch)(book_id=book.id, include_journal=True)

    # Should return dict with sections and journal_entries
    assert isinstance(result, dict)
    assert "sections" in result
    assert "journal_entries" in result
    assert len(result["sections"]) == 1
    assert result["sections"][0]["section_title"] == "Chapter 1"
    assert len(result["journal_entries"]) == 1
    assert result["journal_entries"][0]["content"] == "Note about this book"
