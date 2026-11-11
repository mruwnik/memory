import logging
import posixpath
import struct
import zipfile
import zlib
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Iterator, cast
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree

import defusedxml.ElementTree as DefusedElementTree
from defusedxml import DefusedXmlException
from memory.common import mupdf_sandbox, paths, settings
from memory.common.file_magic import (
    PALMDB_BOOK_SIGNATURES,
    PDF_MAGIC_WINDOW,
    looks_like_pdf,
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


def extract_epub_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """The book's metadata fields that have a value."""
    return {key: value for key, value in metadata.items() if value}


def get_pages(pages: list[str], start_page: int, end_page: int) -> list[str]:
    """The text of pages ``start_page`` to ``end_page``, inclusive, that exist."""
    return pages[max(start_page, 0) : max(end_page + 1, 0)]


def extract_section_pages(
    pages: list[str], toc: Peekable, section_num: int = 1
) -> Section | None:
    """Extract all sections from a table of contents."""
    if not toc.peek():
        return None
    item = cast(TOCItem | None, next(toc))
    if not item:
        return None

    level, name, page = item
    next_item = cast(TOCItem | None, toc.peek())
    if not next_item:
        # Last section extends to the final page (0-indexed)
        last_page = len(pages) - 1
        return Section(
            title=name,
            pages=get_pages(pages, page, last_page),
            number=section_num,
            start_page=page,
            end_page=last_page,
        )

    children = []
    while next_item and next_item[0] > level:
        children.append(extract_section_pages(pages, toc, len(children) + 1))
        next_item = cast(TOCItem | None, toc.peek())

    # When there's no next item, this section extends to the last page (0-indexed)
    last_page = next_item[2] - 1 if next_item else len(pages) - 1
    return Section(
        title=name,
        pages=get_pages(pages, page, last_page),
        number=section_num,
        start_page=page,
        end_page=last_page,
        children=children,
    )


def extract_sections(pages: list[str], toc: list[TOCItem]) -> list[Section]:
    """Split a book's page texts into sections by its table of contents."""
    if not toc:
        # No TOC - treat entire book as one section (page indices are 0-indexed)
        last_page = max(len(pages) - 1, 0)
        return [
            Section(
                title="Content",
                pages=get_pages(pages, 0, last_page),
                number=1,
                start_page=0,
                end_page=last_page,
            )
        ]

    sections = []
    items = Peekable(iter(toc))
    while items.peek():
        section = extract_section_pages(pages, items, len(sections) + 1)
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


def head_is_zip(file: BinaryIO) -> bool:
    file.seek(0)
    return file.read(len(LOCAL_HEADER_SIGNATURE)) == LOCAL_HEADER_SIGNATURE


def check_zip_size(path: Path) -> None:
    """Raise ValueError for a zipped book with more than MAX_EBOOK_ZIP_ENTRIES
    entries, or whose entries inflate past MAX_EBOOK_INFLATED_BYTES.

    Every entry counts, whatever its name: MuPDF reads any entry the package
    file points at, and lays out its markup in memory. Entries are measured by
    inflating their data, since their declared sizes and CRCs are the
    uploader's to choose; one that inflates to other than its declared size is
    refused, and so is an archive that doesn't start the file (MuPDF and
    zipfile could read different entries). An EPUB's spine is then charged for each chapter it lists.
    """
    limit = settings.MAX_EBOOK_INFLATED_BYTES
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise ValueError("Book is not a valid zip archive") from e
    with archive, path.open("rb") as file:
        entries = archive.infolist()
        if not head_is_zip(file) or all(info.header_offset for info in entries):
            raise ValueError("Book has data before its zip archive")
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
        check_epub_spine(archive)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def local_attribute(element: ElementTree.Element, name: str) -> str | None:
    return next(
        (value for key, value in element.attrib.items() if local_name(key) == name), None
    )


def elements_named(root: ElementTree.Element, name: str, cap: int) -> Iterator[ElementTree.Element]:
    """The elements called ``name`` (any namespace), raising once there are more than ``cap``."""
    count = 0
    for element in root.iter():
        if local_name(element.tag) != name:
            continue
        count += 1
        if count > cap:
            raise ValueError(f"Book's package lists more than {cap} {name} elements")
        yield element


def parse_package_xml(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> ElementTree.Element:
    try:
        return DefusedElementTree.fromstring(archive.read(info))
    except (ElementTree.ParseError, DefusedXmlException) as e:
        raise ValueError("Book has an unreadable package file") from e


def check_entry_names(entries: list[zipfile.ZipInfo]) -> None:
    """Refuse entry names whose lookup could differ between MuPDF and this check:
    a leading /, or an empty, . or .. segment (a folder's trailing / aside)."""
    for info in entries:
        name = info.filename[:-1] if info.is_dir() else info.filename
        if name.startswith("/") or {"", ".", ".."} & set(name.split("/")):
            raise ValueError("Book has a zip entry with an unusual name")


def href_cost(base: str, href: str, sizes: dict[str, int], largest: int) -> int:
    """The inflated size of the entry MuPDF reads for ``href`` in the package at ``base``.

    MuPDF appends the href to the package's folder, percent-decodes it and
    matches entry names ignoring case. An href that isn't already in normal
    form, or that names no entry, costs the largest entry.
    """
    path = f"{base}/{href}" if base else href
    candidates = {path, unquote(path)}
    if any(posixpath.normpath(candidate) != candidate for candidate in candidates):
        return largest
    found = [sizes[name.lower()] for name in candidates if name.lower() in sizes]
    return max(found, default=largest)


def manifest_costs(
    package: ElementTree.Element, base: str, sizes: dict[str, int], largest: int
) -> dict[str, int]:
    """Each manifest item's id and the size of the file it names; ids must be unique."""
    costs: dict[str, int] = {}
    for item in elements_named(package, "item", settings.MAX_EBOOK_ZIP_ENTRIES):
        item_id = local_attribute(item, "id")
        if item_id is None:
            continue
        if item_id in costs:
            raise ValueError("Book's package repeats a manifest id")
        costs[item_id] = href_cost(base, local_attribute(item, "href") or "", sizes, largest)
    return costs


EPUB_CONTAINER = "meta-inf/container.xml"
MAX_EPUB_ROOTFILES = 8


def check_epub_spine(archive: zipfile.ZipFile) -> None:
    """Raise ValueError for an EPUB whose spine makes MuPDF lay out more than
    MAX_EBOOK_INFLATED_BYTES, or that lists more than MAX_EBOOK_ZIP_ENTRIES
    chapters or manifest items or more than MAX_EPUB_ROOTFILES package files.

    A spine may list one file many times, and MuPDF lays it out each time, so
    each itemref is charged its file's size. Every package file the container
    names is read once. Entry sizes must already have been checked against
    their inflated data.
    """
    entries = archive.infolist()
    check_entry_names(entries)
    sizes: dict[str, int] = {}
    for info in entries:
        sizes[info.filename.lower()] = max(sizes.get(info.filename.lower(), 0), info.file_size)
    if EPUB_CONTAINER not in sizes:
        return
    largest = max(sizes.values())
    container_info = next(i for i in entries if i.filename.lower() == EPUB_CONTAINER)
    container = parse_package_xml(archive, container_info)
    package_names = {
        (local_attribute(rootfile, "full-path") or "").lower()
        for rootfile in elements_named(container, "rootfile", MAX_EPUB_ROOTFILES)
    }
    limit, chapters, total = settings.MAX_EBOOK_INFLATED_BYTES, 0, 0
    for name in sorted(package_names):
        info = next((i for i in entries if i.filename.lower() == name), None)
        if info is None:
            raise ValueError("Book's container names a missing package file")
        package = parse_package_xml(archive, info)
        costs = manifest_costs(package, posixpath.dirname(info.filename), sizes, largest)
        cap = settings.MAX_EBOOK_ZIP_ENTRIES - chapters
        for itemref in elements_named(package, "itemref", cap):
            chapters += 1
            total += costs.get(local_attribute(itemref, "idref") or "", largest)
            if total > limit:
                raise ValueError(f"Book's chapters add up to more than {limit} bytes")


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

    Every check that applies runs: a zip directory anywhere is measured by
    inflating its entries (and refused unless the archive starts the file), a
    MOBI/PalmDoc signature by the text records, and anything that is neither,
    nor a PDF, by its size. MuPDF picks the format from the content, so the
    file name and declared type don't matter.
    """
    with path.open("rb") as file:
        head = file.read(PDF_MAGIC_WINDOW)
    zipped = zipfile.is_zipfile(path)
    palmdb = palmdb_signature(head) in PALMDB_BOOK_SIGNATURES
    if zipped:
        check_zip_size(path)
    if palmdb:
        check_palmdb_size(path)
    if zipped or palmdb or looks_like_pdf(head):
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
        ValueError: if the book is too large to parse, or MuPDF can't read it
            within its sandbox's limits.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    check_parse_size(path)

    book = mupdf_sandbox.book_contents(path)
    metadata = extract_epub_metadata(book.metadata)
    sections = extract_sections(book.pages, book.toc)
    full_content = "\n\n".join(page for section in sections for page in section.pages)
    return Ebook(
        title=metadata.get("title", path.stem),
        author=metadata.get("author", "Unknown"),
        metadata=metadata,
        sections=sections,
        full_content=full_content,
        file_path=path,
        relative_path=paths.to_db_filename(path),
        file_type=path.suffix.lower()[1:],
        n_pages=len(book.pages),
    )
