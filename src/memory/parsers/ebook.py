import logging
from dataclasses import dataclass, field
from typing import Any, cast
from pathlib import Path

import fitz
from memory.common import settings  # PyMuPDF

logger = logging.getLogger(__name__)


@dataclass
class Section:
    """Represents a chapter or section in an ebook."""

    title: str
    pages: list[str]
    number: int | None = None
    start_page: int | None = None
    end_page: int | None = None
    children: list["Section"] = field(default_factory=list)


@dataclass
class Ebook:
    """Structured representation of an ebook."""

    title: str
    author: str
    file_path: Path
    relative_path: Path
    metadata: dict[str, Any] = field(default_factory=dict)
    sections: list[Section] = field(default_factory=list)
    full_content: str = ""
    file_type: str = ""
    n_pages: int = 0


class Peekable:
    def __init__(self, items):
        self.items = items
        self.done = False
        self._get_next()

    def _get_next(self):
        try:
            self.cached = next(self.items)
        except StopIteration:
            self.done = True

    def peek(self):
        if self.done:
            return None
        return self.cached

    def __iter__(self):
        return self

    def __next__(self):
        if self.done:
            raise StopIteration

        item = self.cached
        self._get_next()
        return item


TOCItem = tuple[int, str, int]


def extract_epub_metadata(doc) -> dict[str, Any]:
    """Extract metadata from a PyMuPDF document (EPUB)."""
    if not doc.metadata:
        return {}

    return {key: value for key, value in doc.metadata.items() if value}


def get_pages(doc, start_page: int, end_page: int) -> list[str]:
    pages = [
        doc[page_num].get_text()
        for page_num in range(start_page, end_page + 1)
        if 0 <= page_num < doc.page_count
    ]
    return pages


def extract_section_pages(doc, toc: Peekable, section_num: int = 1) -> Section | None:
    """Extract all sections from a table of contents."""
    if not toc.peek():
        return None
    item = cast(TOCItem | None, next(toc))
    if not item:
        return None

    level, name, page = item
    next_item = cast(TOCItem | None, toc.peek())
    if not next_item:
        # Last section extends to the final page (page_count - 1 since 0-indexed)
        last_page = doc.page_count - 1
        return Section(
            title=name,
            pages=get_pages(doc, page, last_page),
            number=section_num,
            start_page=page,
            end_page=last_page,
        )

    children = []
    while next_item and next_item[0] > level:
        children.append(extract_section_pages(doc, toc, len(children) + 1))
        next_item = cast(TOCItem | None, toc.peek())

    # When there's no next item, this section extends to the last page (0-indexed)
    last_page = next_item[2] - 1 if next_item else doc.page_count - 1
    return Section(
        title=name,
        pages=get_pages(doc, page, last_page),
        number=section_num,
        start_page=page,
        end_page=last_page,
        children=children,
    )


def extract_sections(doc) -> list[Section]:
    """Extract all sections from a PyMuPDF document."""
    toc = doc.get_toc()
    if not toc:
        # No TOC - treat entire book as one section (page indices are 0-indexed)
        last_page = doc.page_count - 1 if doc.page_count > 0 else 0
        return [
            Section(
                title="Content",
                pages=get_pages(doc, 0, last_page),
                number=1,
                start_page=0,
                end_page=last_page,
            )
        ]

    sections = []
    toc = Peekable(iter(doc.get_toc()))
    while toc.peek():
        section = extract_section_pages(doc, toc, len(sections) + 1)
        if section:
            sections.append(section)
    return sections


def parse_ebook(file_path: str | Path) -> Ebook:
    """
    Parse an ebook file and extract its content and metadata.

    Args:
        file_path: Path to the ebook file

    Returns:
        Structured ebook data
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    try:
        doc = fitz.open(str(path))
    except fitz.FileNotFoundError as e:
        logger.error(f"Error opening ebook {path}: {e}")
        raise

    try:
        metadata = extract_epub_metadata(doc)

        title = metadata.get("title", path.stem)
        author = metadata.get("author", "Unknown")

        sections = extract_sections(doc)
        full_content = ""
        if sections:
            full_content = "\n\n".join(
                page for section in sections for page in section.pages
            )

        return Ebook(
            title=title,
            author=author,
            metadata=metadata,
            sections=sections,
            full_content=full_content,
            file_path=path,
            relative_path=path.relative_to(settings.FILE_STORAGE_DIR),
            file_type=path.suffix.lower()[1:],
            n_pages=doc.page_count,
        )
    finally:
        doc.close()
