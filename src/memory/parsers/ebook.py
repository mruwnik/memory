import logging
import struct
import zipfile
import zlib
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Iterator, cast
from pathlib import Path

import pymupdf
from memory.common import paths, settings
from memory.common.file_magic import (
    PALMDB_BOOK_SIGNATURES,
    PDF_MAGIC_WINDOW,
    has_pdf_magic,
    palmdb_signature,
)

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
# A PalmDB header is 78 bytes, ending in the record count; the record table follows.
PALMDB_HEADER_SIZE = 78
# Record 0 of a PalmDoc/MOBI book starts with compression, unused, text length,
# text record count, record size, encryption and unknown.
PALMDOC_HEADER = struct.Struct(">HHIHHHH")
# Uncompressed and PalmDoc: MuPDF reads no other compression.
PALMDOC_COMPRESSIONS = (1, 2)
PALMDOC_RECORD_SIZE = 4096


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


def check_zip_size(path: Path) -> None:
    """Raise ValueError for a zipped book with more than MAX_EBOOK_ZIP_ENTRIES
    entries, or whose entries inflate past MAX_EBOOK_INFLATED_BYTES.

    Every entry counts, whatever its name: MuPDF reads any entry the package
    file points at, and lays out its markup in memory. Entries are measured by
    inflating their data, since their declared sizes and CRCs are the
    uploader's to choose; one that inflates to other than its declared size is
    refused.
    """
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


def palmdb_records(data: bytes) -> list[bytes]:
    """The records of a PalmDB file, refusing a damaged record table."""
    if len(data) < PALMDB_HEADER_SIZE:
        raise ValueError("Book has a damaged PalmDB header")
    (count,) = struct.unpack_from(">H", data, PALMDB_HEADER_SIZE - 2)
    table_end = PALMDB_HEADER_SIZE + 8 * count
    if table_end > len(data):
        raise ValueError("Book has a damaged PalmDB header")
    offsets = [
        struct.unpack_from(">I", data, PALMDB_HEADER_SIZE + 8 * index)[0]
        for index in range(count)
    ] + [len(data)]
    if offsets[0] < table_end or offsets != sorted(offsets):
        raise ValueError("Book has a damaged PalmDB record table")
    return [data[start:end] for start, end in zip(offsets, offsets[1:])]


def check_palmdb_size(path: Path) -> None:
    """Raise ValueError for a MOBI/PalmDoc book whose text is more than
    MAX_EBOOK_INFLATED_BYTES, or that MuPDF couldn't read.

    MuPDF lays out the text of the first ``text_count`` records (record 0's
    count), at most one record size each however the record decompresses, so
    that bounds the text it reads. Images live in other records.
    """
    limit = settings.MAX_EBOOK_INFLATED_BYTES
    records = palmdb_records(path.read_bytes())
    if len(records) < 2 or len(records[0]) < PALMDOC_HEADER.size:
        raise ValueError("Book has no text records")
    compression, _, _, text_count, record_size, *_ = PALMDOC_HEADER.unpack_from(records[0])
    if compression not in PALMDOC_COMPRESSIONS:
        raise ValueError("Book uses an unsupported MOBI compression")
    if record_size != PALMDOC_RECORD_SIZE:
        raise ValueError("Book has an unsupported text record size")
    if min(text_count, len(records) - 1) * PALMDOC_RECORD_SIZE > limit:
        raise ValueError(f"Book text is more than {limit} bytes")


def check_parse_size(path: Path) -> None:
    """Raise ValueError for a book whose markup is too large for MuPDF to lay out.

    A zip (EPUB and other packages) is measured by inflating its entries, a
    MOBI/PalmDoc file by its text records, and any other format but PDF,
    which MuPDF doesn't lay out, by its size. MuPDF picks the format from the
    content, so the file name and declared type don't matter.
    """
    if zipfile.is_zipfile(path):
        check_zip_size(path)
        return
    with path.open("rb") as file:
        head = file.read(PDF_MAGIC_WINDOW)
    if has_pdf_magic(head):
        return
    if palmdb_signature(head) in PALMDB_BOOK_SIGNATURES:
        check_palmdb_size(path)
        return
    limit = settings.MAX_EBOOK_INFLATED_BYTES
    if path.stat().st_size > limit:
        raise ValueError(f"Book is more than {limit} bytes")


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
