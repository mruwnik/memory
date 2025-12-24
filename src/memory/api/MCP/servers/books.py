"""MCP subserver for ebook access."""

import logging

from fastmcp import FastMCP
from sqlalchemy.orm import joinedload

from memory.common.db.connection import make_session
from memory.common.db.models import Book, BookSection, BookSectionPayload

logger = logging.getLogger(__name__)

books_mcp = FastMCP("memory-books")


@books_mcp.tool()
async def all_books(sections: bool = False) -> list[dict]:
    """
    Get all books in the database.

    If sections is True, the response will include the sections for each book.

    Args:
        sections: Whether to include sections in the response. Defaults to False.

    Returns:
        List of books in the database.
    """
    options = []
    if sections:
        options = [joinedload(Book.sections)]

    with make_session() as session:
        books = session.query(Book).options(*options).all()
        return [book.as_payload(sections=sections) for book in books]


@books_mcp.tool()
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
