import logging
import pathlib
from typing import Iterable, cast

import memory.common.settings as settings
from memory.parsers.ebook import Ebook, parse_ebook, Section
from memory.common.db.models import Book, BookSection
from memory.common.db.connection import make_session
from memory.workers.celery_app import app, EBOOK_ROOT
from memory.workers.tasks.content_processing import (
    check_content_exists,
    create_content_hash,
    embed_source_item,
    push_to_qdrant,
    safe_task_execution,
)

logger = logging.getLogger(__name__)

SYNC_BOOK = f"{EBOOK_ROOT}.sync_book"

# Minimum section length to embed (avoid noise from very short sections)
MIN_SECTION_LENGTH = 100


def create_book_from_ebook(ebook, tags: Iterable[str] = []) -> Book:
    """Create a Book model from parsed ebook data."""
    return Book(
        title=ebook.title,
        author=ebook.author,
        publisher=ebook.metadata.get("creator"),
        language=ebook.metadata.get("language"),
        total_pages=ebook.n_pages,
        file_path=ebook.file_path.as_posix(),
        book_metadata=ebook.metadata,
        tags=tags,
    )


def section_processor(
    book: Book,
    all_sections: list[BookSection],
    section_map: dict[
        tuple[int, int | None], tuple[BookSection, tuple[int, int | None] | None]
    ],
):
    def process_section(
        section: Section,
        level: int = 1,
        parent_key: tuple[int, int | None] | None = None,
    ):
        content = "\n\n".join(section.pages).strip()
        if len(content) >= MIN_SECTION_LENGTH:
            book_section = BookSection(
                book_id=book.id,
                section_title=section.title,
                section_number=section.number,
                section_level=level,
                start_page=section.start_page,
                end_page=section.end_page,
                parent_section_id=None,  # Will be set after flush
                content=content,
                filename=book.file_path,
                size=len(content),
                mime_type="text/plain",
                sha256=create_content_hash(
                    f"{book.id}:{section.title}:{section.start_page}"
                ),
                modality="book",
                tags=book.tags,
                pages=section.pages,
            )

            all_sections.append(book_section)
            section_key = (level, section.number)
            section_map[section_key] = (book_section, parent_key)

            # Process children
            for child in section.children:
                process_section(child, level + 1, section_key)

    return process_section


def create_all_sections(
    ebook_sections: list[Section], book: Book
) -> tuple[list[BookSection], dict]:
    """Create all sections iteratively to handle parent-child relationships properly."""
    all_sections = []
    section_map = {}  # Maps (level, number) to section for parent lookup

    process_section = section_processor(book, all_sections, section_map)
    for section in ebook_sections:
        process_section(section)

    return all_sections, section_map


def validate_and_parse_book(file_path: str) -> Ebook:
    """Validate file exists and parse the ebook."""
    logger.info(f"Validating and parsing book from {file_path}")
    path = pathlib.Path(file_path)
    if not path.is_absolute():
        path = settings.EBOOK_STORAGE_DIR / path

    logger.info(f"Resolved path: {path}")

    if not path.exists():
        raise FileNotFoundError(f"Book file not found: {path}")

    try:
        return parse_ebook(path)
    except Exception as e:
        logger.error(f"Failed to parse ebook {path}: {e}")
        raise


def create_book_and_sections(
    ebook, session, tags: Iterable[str] = []
) -> tuple[Book, list[BookSection]]:
    """Create book and all its sections with proper relationships."""
    # Create book
    book = create_book_from_ebook(ebook, tags)
    session.add(book)
    session.flush()  # Get the book ID

    # Create all sections
    all_sections, section_map = create_all_sections(ebook.sections, book)
    session.add_all(all_sections)
    session.flush()

    for book_section, parent_key in section_map.values():
        if parent_key and parent_key in section_map:
            parent_section = section_map[parent_key][0]
            book_section.parent_section_id = cast(int, parent_section.id)

    return book, all_sections


def embed_sections(all_sections: list[BookSection]) -> int:
    """Embed all sections and return count of successfully embedded sections."""
    return sum(embed_source_item(section) for section in all_sections)


@app.task(name=SYNC_BOOK)
@safe_task_execution
def sync_book(file_path: str, tags: Iterable[str] = []) -> dict:
    """
    Synchronize a book from a file path.

    Args:
        file_path: Path to the ebook file

    Returns:
        dict: Summary of what was processed
    """
    ebook = validate_and_parse_book(file_path)
    logger.info(f"Ebook parsed: {ebook.title}")

    with make_session() as session:
        # Check for existing book
        existing_book = check_content_exists(
            session, Book, file_path=ebook.file_path.as_posix()
        )
        if existing_book:
            logger.info(f"Book already exists: {existing_book.title}")
            return {
                "book_id": existing_book.id,
                "title": existing_book.title,
                "author": existing_book.author,
                "status": "already_exists",
                "sections_processed": 0,
            }

        logger.info("Creating book and sections with relationships")
        # Create book and sections with relationships
        book, all_sections = create_book_and_sections(ebook, session, tags)

        # Embed sections
        logger.info("Embedding sections")
        embedded_count = sum(embed_source_item(section) for section in all_sections)
        session.flush()

        for section in all_sections:
            logger.info(
                f"Embedded section: {section.section_title} - {section.content[:100]}"
            )
        logger.info("Pushing to Qdrant")
        push_to_qdrant(all_sections)
        logger.info("Committing session")

        session.commit()

        logger.info(
            f"Successfully processed book: {book.title} "
            f"({embedded_count}/{len(all_sections)} sections embedded)"
        )

        return {
            "book_id": book.id,
            "title": book.title,
            "author": book.author,
            "status": "processed",
            "total_sections": len(all_sections),
            "sections_embedded": embedded_count,
        }
