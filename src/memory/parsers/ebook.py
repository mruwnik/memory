import logging
import struct
import zipfile
import zlib
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Iterator, cast
from pathlib import Path

import pymupdf
from memory.common import paths, settings

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
    relative_path: str
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


# Bytes read per step while measuring a zipped book.
INFLATE_CHUNK = 64 * 1024
LOCAL_HEADER = struct.Struct("<4s5H3L2H")
LOCAL_HEADER_SIGNATURE = b"PK\x03\x04"


def stored_chunks(file: BinaryIO, info: zipfile.ZipInfo) -> Iterator[bytes]:
    """The compressed bytes of ``info``, read from after its local header."""
    file.seek(info.header_offset)
    header = file.read(LOCAL_HEADER.size)
    if len(header) < LOCAL_HEADER.size or header[:4] != LOCAL_HEADER_SIGNATURE:
        raise ValueError("Book has a damaged zip entry")
    *_, name_length, extra_length = LOCAL_HEADER.unpack(header)
    file.seek(name_length + extra_length, 1)
    left = info.compress_size
    while left > 0:
        chunk = file.read(min(INFLATE_CHUNK, left))
        if not chunk:
            raise ValueError("Book has a truncated zip entry")
        left -= len(chunk)
        yield chunk


def inflated_size(chunks: Iterator[bytes], method: int, budget: int) -> int:
    """The bytes ``chunks`` really inflate to, counting no further than just past
    ``budget``. Only stored and deflated entries are measured."""
    if method == zipfile.ZIP_STORED:
        return sum(len(chunk) for chunk in chunks)
    if method != zipfile.ZIP_DEFLATED:
        raise ValueError("Book has a zip entry in an unsupported compression method")
    inflater = zlib.decompressobj(-zlib.MAX_WBITS)
    size = 0
    for data in chunks:
        while data and size <= budget:
            size += len(inflater.decompress(data, budget - size + 1))
            data = inflater.unconsumed_tail
        if size > budget or inflater.eof:
            return size
    return size + len(inflater.flush())


def check_parse_size(path: Path) -> None:
    """Raise ValueError for a zipped book with more than MAX_EBOOK_ZIP_ENTRIES
    entries, or whose entries inflate past MAX_EBOOK_INFLATED_BYTES.

    Every entry counts, whatever its name: MuPDF reads any entry the package
    file points at, and lays out its markup in memory. Entries are measured by
    inflating their data, since their declared sizes and CRCs are the
    uploader's to choose; one that inflates to other than its declared size is
    refused.
    """
    if not zipfile.is_zipfile(path):
        return
    limit = settings.MAX_EBOOK_INFLATED_BYTES
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise ValueError("Book is not a valid zip archive") from e
    with archive, path.open("rb") as file:
        entries = archive.infolist()
        if len(entries) > settings.MAX_EBOOK_ZIP_ENTRIES:
            raise ValueError(f"Book has more than {settings.MAX_EBOOK_ZIP_ENTRIES} entries")
        total = 0
        for info in entries:
            try:
                size = inflated_size(stored_chunks(file, info), info.compress_type, limit - total)
            except zlib.error as e:
                raise ValueError("Book has a zip entry that does not inflate") from e
            total += size
            if total > limit:
                raise ValueError(f"Book inflates to more than {limit} bytes")
            if size != info.file_size:
                raise ValueError("Book has a zip entry whose size does not match its header")


def parse_ebook(file_path: str | Path) -> Ebook:
    """
    Parse an ebook file and extract its content and metadata.

    Args:
        file_path: Path to the ebook file

    Returns:
        Structured ebook data

    Raises:
        ValueError: if the book's text is too large to parse.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    check_parse_size(path)

    try:
        doc = pymupdf.open(str(path))
    except pymupdf.FileNotFoundError:
        logger.exception("Error opening ebook")
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
            relative_path=paths.to_db_filename(path),
            file_type=path.suffix.lower()[1:],
            n_pages=doc.page_count,
        )
    finally:
        doc.close()
