"""MCP subserver for ebook access."""

import logging

from fastmcp import FastMCP
from sqlalchemy import Text
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import joinedload

from memory.api.MCP.visibility import has_items, require_scopes, visible_when

from memory.common.db.connection import make_session
from memory.common.db.models import Book, BookSection, BookSectionPayload

logger = logging.getLogger(__name__)

books_mcp = FastMCP("memory-books")


@books_mcp.tool()
@visible_when(require_scopes("read"), has_items(Book))
async def list_books(
    sections: bool = False,
    title: str | None = None,
    author: str | None = None,
    tags: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """
    List books in the database with optional filters.

    Args:
        sections: Whether to include sections in the response. Defaults to False.
        title: Filter by title (case-insensitive substring match).
        author: Filter by author (case-insensitive substring match).
        tags: Filter by tags (books matching any of the provided tags).
        limit: Maximum number of books to return (default 50, max 200).
        offset: Number of books to skip for pagination (default 0).

    Returns:
        List of books matching the filters.
    """
    limit = min(limit, 200)
    options = []
    if sections:
        options = [joinedload(Book.sections)]

    with make_session() as session:
        query = session.query(Book).options(*options)

        if title:
            query = query.filter(Book.title.ilike(f"%{title}%"))

        if author:
            query = query.filter(Book.author.ilike(f"%{author}%"))

        if tags:
            query = query.filter(Book.tags.op("&&")(sql_cast(tags, ARRAY(Text))))

        query = query.order_by(Book.title).offset(offset).limit(limit)
        books = query.all()
        return [book.as_payload(sections=sections) for book in books]


@books_mcp.tool()
@visible_when(require_scopes("read"), has_items(Book))
def read_book(book_id: int, sections: list[int] = []) -> list[BookSectionPayload]:
    """
    Read a book from the database.

    If sections is provided, only the sections with the given IDs will be returned.

    Args:
        book_id: The ID of the book to read.
        sections: The IDs of the sections to read. Defaults to all sections.

    Returns:
        List of sections in the book, with contents. In the case of nested sections, only the top-level sections are returned.
    """
    with make_session() as session:
        book_sections = session.query(BookSection).filter(
            BookSection.book_id == book_id
        )
        if sections:
            book_sections = book_sections.filter(BookSection.id.in_(sections))

        all_sections = book_sections.all()
        parents = [section.parent_section_id for section in all_sections]
        return [
            section.as_payload()
            for section in all_sections
            if section.id not in parents
        ]
