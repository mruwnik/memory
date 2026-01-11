import logging
import pathlib
from datetime import datetime
from typing import Any, Iterable, TypedDict, cast

from sqlalchemy.orm import Session


class BookProcessingResult(TypedDict, total=False):
    """Result of book processing operations."""

    status: str
    book_id: int
    title: str
    author: str | None
    total_sections: int
    sections_embedded: int
    sections_processed: int
    error: str

import memory.common.settings as settings
from memory.common.celery_app import SYNC_BOOK, REPROCESS_BOOK, app
from memory.common.db.connection import make_session
from memory.common.db.models import Book, BookSection
from memory.common import jobs as job_utils
from memory.parsers.ebook import Ebook, Section, parse_ebook
from memory.common.content_processing import (
    check_content_exists,
    clear_item_chunks,
    create_content_hash,
    embed_source_item,
    push_to_qdrant,
    safe_task_execution,
)

logger = logging.getLogger(__name__)


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
        file_path=ebook.file_path.relative_to(settings.FILE_STORAGE_DIR).as_posix(),
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
                book=book,
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


def prepare_book_for_reingest(session: Session, item_id: int) -> Book | None:
    """
    Fetch an existing book and clear its sections/chunks for reprocessing.

    Returns the book if found, None otherwise.
    """
    book = session.get(Book, item_id)
    if not book:
        return None

    # Clear existing sections (they'll be regenerated)
    sections = session.query(BookSection).filter(BookSection.book_id == item_id).all()
    for section in sections:
        clear_item_chunks(section, session)
        session.delete(section)

    session.flush()
    logger.info(f"Prepared book {item_id} for reingest: cleared {len(sections)} sections")
    return book


def execute_book_processing(
    session: Session,
    book: Book,
    ebook: Ebook,
    title: str = "",
    author: str = "",
    publisher: str = "",
    published: str = "",
    language: str = "",
    edition: str = "",
    series: str | dict[str, Any] = "",
    series_number: int | None = None,
    job_id: int | None = None,
) -> BookProcessingResult:
    """
    Run the full processing pipeline on a book.

    This is the shared processing step for both ingest and reingest:
    1. Create sections from parsed ebook
    2. Apply metadata overrides
    3. Generate embeddings
    4. Push to Qdrant

    Args:
        session: Database session
        book: Book record (new or existing with sections cleared)
        ebook: Parsed ebook data
        title/author/etc: Optional metadata overrides
        job_id: Optional job ID for status tracking

    Returns:
        Dict with processing results
    """
    # Capture ID before try block to avoid DetachedInstanceError after rollback
    book_id = book.id

    try:
        # Create all sections
        all_sections, section_map = create_all_sections(ebook.sections, book)
        session.add_all(all_sections)
        session.flush()

        for book_section, parent_key in section_map.values():
            if parent_key and parent_key in section_map:
                parent_section = section_map[parent_key][0]
                book_section.parent_section_id = cast(int, parent_section.id)

        logger.debug(f"Created {len(all_sections)} sections")

        # Apply metadata overrides
        if title:
            book.title = title  # type: ignore
        if author:
            book.author = author  # type: ignore
        if publisher:
            book.publisher = publisher  # type: ignore
        if published:
            book.published = datetime.fromisoformat(published)  # type: ignore
        if isinstance(book.published, str):
            book.published = datetime.fromisoformat(book.published)  # type: ignore
        if language:
            book.language = language  # type: ignore
        if edition:
            book.edition = edition  # type: ignore
        if isinstance(series, dict):
            series = series.get("name")
        if series:
            book.series = series  # type: ignore
        if series_number:
            book.series_number = series_number  # type: ignore

        # Embed sections
        logger.info("Embedding sections")
        embedded_count = sum(embed_source_item(section) for section in all_sections)
        session.flush()

        logger.info("Pushing to Qdrant")
        push_to_qdrant(all_sections)

        # Mark job complete
        if job_id:
            job_utils.complete_job(session, job_id, result_id=book.id, result_type="Book")

        session.commit()

        logger.info(
            f"Successfully processed book: {book.title} "
            f"({embedded_count}/{len(all_sections)} sections embedded)"
        )

        return {
            "status": "processed",
            "book_id": book.id,
            "title": book.title,
            "author": book.author,
            "total_sections": len(all_sections),
            "sections_embedded": embedded_count,
        }

    except Exception as e:
        logger.exception(f"Failed to process book {book_id}: {e}")
        # Rollback partial work (sections, embeddings) to avoid persisting incomplete state
        session.rollback()
        # Now mark the job as failed in a clean transaction
        if job_id:
            job_utils.fail_job(session, job_id, str(e))
            session.commit()
        return {"status": "error", "error": str(e), "book_id": book_id}


@app.task(name=SYNC_BOOK)
@safe_task_execution
def sync_book(
    file_path: str,
    tags: Iterable[str] = [],
    title: str = "",
    author: str = "",
    publisher: str = "",
    published: str = "",
    language: str = "",
    edition: str = "",
    series: str | dict[str, Any] = "",
    series_number: int | None = None,
    job_id: int | None = None,
) -> BookProcessingResult:
    """
    Synchronize a new book from a file path.

    Creates book record, parses ebook, creates sections, and generates embeddings.

    Args:
        file_path: Path to the ebook file
        tags: Optional tags for the book
        title/author/etc: Optional metadata overrides
        job_id: Optional job ID for status tracking

    Returns:
        dict: Summary of what was processed
    """
    logger.info(f"Processing new book from {file_path} (job_id={job_id})")

    ebook = validate_and_parse_book(file_path)
    logger.info(f"Ebook parsed: {ebook.title}, {ebook.file_path.as_posix()}")

    with make_session() as session:
        if job_id:
            job_utils.start_job(session, job_id)
            session.commit()

        # Check for existing book (idempotency)
        logger.info(f"Checking for existing book: {ebook.relative_path.as_posix()}")
        existing_book = check_content_exists(
            session, Book, file_path=ebook.relative_path.as_posix()
        )
        if existing_book:
            logger.info(f"Book already exists: {existing_book.title}")
            if job_id:
                job_utils.complete_job(
                    session, job_id, result_id=existing_book.id, result_type="Book"
                )
                session.commit()
            return {
                "status": "already_exists",
                "book_id": existing_book.id,
                "title": existing_book.title,
                "author": existing_book.author,
                "sections_processed": 0,
            }

        # Create new book record
        logger.info("Creating book record")
        book = create_book_from_ebook(ebook, tags)
        session.add(book)
        session.flush()

        return execute_book_processing(
            session,
            book,
            ebook,
            title=title,
            author=author,
            publisher=publisher,
            published=published,
            language=language,
            edition=edition,
            series=series,
            series_number=series_number,
            job_id=job_id,
        )


@app.task(name=REPROCESS_BOOK)
@safe_task_execution
def reprocess_book(
    item_id: int,
    title: str = "",
    author: str = "",
    publisher: str = "",
    published: str = "",
    language: str = "",
    edition: str = "",
    series: str | dict[str, Any] = "",
    series_number: int | None = None,
    job_id: int | None = None,
) -> BookProcessingResult:
    """
    Reprocess an existing book.

    Fetches the book, clears existing sections, re-parses the file,
    and re-runs the full processing pipeline.

    Args:
        item_id: ID of the book to reprocess
        title/author/etc: Optional metadata overrides
        job_id: Optional job ID for status tracking
    """
    logger.info(f"Reprocessing book {item_id} (job_id={job_id})")

    with make_session() as session:
        if job_id:
            job_utils.start_job(session, job_id)
            session.commit()

        book = prepare_book_for_reingest(session, item_id)
        if not book:
            error = f"Book {item_id} not found"
            if job_id:
                job_utils.fail_job(session, job_id, error)
                session.commit()
            return {"status": "error", "error": error}

        # Re-parse the ebook from the original file
        ebook = validate_and_parse_book(book.file_path)

        return execute_book_processing(
            session,
            book,
            ebook,
            title=title,
            author=author,
            publisher=publisher,
            published=published,
            language=language,
            edition=edition,
            series=series,
            series_number=series_number,
            job_id=job_id,
        )
