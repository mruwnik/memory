import gzip
import struct
import zipfile
import zlib
from unittest.mock import patch

import pytest
import pymupdf

from memory.common import settings
from memory.common.mupdf_sandbox import BookContents
from memory.parsers.ebook import (
    Peekable,
    extract_epub_metadata,
    get_pages,
    extract_section_pages,
    extract_sections,
    parse_ebook,
    Section,
    check_parse_size,
)
from tests.memory import book_files


def test_peekable_peek():
    p = Peekable(iter([1, 2, 3]))
    assert p.peek() == 1
    assert p.peek() == 1  # Multiple peeks don't advance


def test_peekable_iteration():
    p = Peekable(iter([1, 2, 3]))
    assert list(p) == [1, 2, 3]


def test_peekable_empty():
    p = Peekable(iter([]))
    assert p.peek() is None
    assert list(p) == []


PAGES = [f"Content of page {i}" for i in range(5)]
TOC = [
    (1, "Chapter 1", 0),
    (2, "Section 1.1", 1),
    (2, "Section 1.2", 2),
    (1, "Chapter 2", 3),
    (2, "Section 2.1", 4),
]
METADATA = {
    "title": "Test Book",
    "author": "Test Author",
    "creator": "Test Creator",
    "producer": "Test Producer",
}


@pytest.fixture
def sandboxed_book():
    """parse_ebook's sandbox call, answering with a five-page book."""
    book = BookContents(metadata=dict(METADATA), toc=list(TOC), pages=list(PAGES))
    with patch("memory.parsers.ebook.mupdf_sandbox.book_contents", return_value=book) as call:
        yield call


@pytest.mark.parametrize(
    "metadata_input,expected",
    [
        ({"title": "Book", "author": "Author"}, {"title": "Book", "author": "Author"}),
        (
            {"title": "", "author": "Author"},
            {"author": "Author"},
        ),  # Empty strings should be filtered
        (
            {"title": None, "author": "Author"},
            {"author": "Author"},
        ),  # None values should be filtered
        ({}, {}),  # Empty dict
    ],
)
def test_extract_epub_metadata(metadata_input, expected):
    assert extract_epub_metadata(metadata_input) == expected


@pytest.mark.parametrize(
    "start_page,end_page,expected_content",
    [
        (0, 2, ["Content of page 0", "Content of page 1", "Content of page 2"]),
        (3, 4, ["Content of page 3", "Content of page 4"]),
        (4, 4, ["Content of page 4"]),
        (
            0,
            10,
            [f"Content of page {i}" for i in range(5)],
        ),  # Out of range
        (5, 10, []),  # Completely out of range
        (3, 2, []),  # Invalid range (start > end)
        (
            -1,
            2,
            [f"Content of page {i}" for i in range(3)],
        ),  # Negative start
        (-(10**12), 10**12, PAGES),  # A table of contents' pages cost nothing to clamp
        (-5, -2, []),  # Wholly before the first page
    ],
)
def test_get_pages(start_page, end_page, expected_content):
    assert get_pages(PAGES, start_page, end_page) == expected_content


@pytest.fixture
def mock_toc_items():
    items = [
        (1, "Chapter 1", 0),  # Level 1, start at page 0
        (2, "Section 1.1", 1),  # Level 2, start at page 1
        (2, "Section 1.2", 2),  # Level 2, start at page 2
        (1, "Chapter 2", 3),  # Level 1, start at page 3
    ]
    return Peekable(iter(items))


def test_extract_section_pages(mock_toc_items):
    assert extract_section_pages(PAGES, mock_toc_items) == Section(
        title="Chapter 1",
        number=1,
        start_page=0,
        end_page=2,
        pages=["Content of page 0", "Content of page 1", "Content of page 2"],
        children=[
            Section(
                title="Section 1.1",
                number=1,
                start_page=1,
                end_page=1,
                pages=["Content of page 1"],
            ),
            Section(
                title="Section 1.2",
                number=2,
                start_page=2,
                end_page=2,
                pages=["Content of page 2"],
            ),
        ],
    )


def test_extract_sections():
    assert extract_sections(PAGES, TOC) == [
        Section(
            title="Chapter 1",
            number=1,
            start_page=0,
            end_page=2,
            pages=["Content of page 0", "Content of page 1", "Content of page 2"],
            children=[
                Section(
                    title="Section 1.1",
                    number=1,
                    start_page=1,
                    end_page=1,
                    pages=["Content of page 1"],
                ),
                Section(
                    title="Section 1.2",
                    number=2,
                    start_page=2,
                    end_page=2,
                    pages=["Content of page 2"],
                ),
            ],
        ),
        Section(
            title="Chapter 2",
            number=2,
            start_page=3,
            end_page=4,  # page_count - 1, since pages are 0-indexed
            pages=["Content of page 3", "Content of page 4"],
            children=[
                Section(
                    title="Section 2.1",
                    number=1,
                    start_page=4,
                    end_page=4,  # page_count - 1, since pages are 0-indexed
                    pages=["Content of page 4"],
                ),
            ],
        ),
    ]


def test_extract_sections_no_toc():
    assert extract_sections(PAGES, []) == [
        Section(
            title="Content",
            number=1,
            start_page=0,
            end_page=4,  # page_count - 1, since pages are 0-indexed
            pages=[f"Content of page {i}" for i in range(5)],
            children=[],
        ),
    ]


def test_parse_ebook(sandboxed_book, tmp_path):
    test_file = tmp_path / "test.epub"
    test_file.touch()

    ebook = parse_ebook(test_file)

    assert ebook.title == "Test Book"
    assert ebook.author == "Test Author"
    assert len(ebook.sections) == 2
    assert ebook.file_path == test_file
    assert ebook.file_type == "epub"
    assert ebook.n_pages == 5
    assert ebook.full_content == "\n\n".join(PAGES)
    sandboxed_book.assert_called_once_with(test_file)


def test_parse_ebook_file_not_found(sandboxed_book, tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_ebook(tmp_path / "does_not_exist.epub")
    sandboxed_book.assert_not_called()


def test_parse_ebook_no_metadata(sandboxed_book, tmp_path):
    sandboxed_book.return_value.metadata = {}
    test_file = tmp_path / "test.epub"
    test_file.touch()

    ebook = parse_ebook(test_file)

    assert ebook.title == "test"  # Should use file stem
    assert ebook.author == "Unknown"


@pytest.mark.parametrize(
    "file_suffix,expected_type",
    [
        (".epub", "epub"),
        (".pdf", "pdf"),
        (".mobi", "mobi"),
        (".EPUB", "epub"),  # Test case insensitivity
        ("", ""),  # No extension
    ],
)
def test_parse_ebook_file_types(sandboxed_book, tmp_path, file_suffix, expected_type):
    test_file = tmp_path / f"test{file_suffix}"
    test_file.touch()

    assert parse_ebook(test_file).file_type == expected_type


def test_extract_section_pages_empty():
    """Test with empty TOC."""
    assert extract_section_pages(PAGES, Peekable(iter([]))) is None


def test_extract_section_pages_deeply_nested():
    """Test with deeply nested TOC structure."""
    pages = [f"Content of page {i}" for i in range(10)]

    # Create a deeply nested TOC structure
    items = [
        (1, "Chapter 1", 0),
        (2, "Section 1.1", 1),
        (3, "Subsection 1.1.1", 2),
        (4, "Sub-subsection 1.1.1.1", 3),
        (3, "Subsection 1.1.2", 4),
        (2, "Section 1.2", 5),
        (1, "Chapter 2", 6),
    ]
    toc = Peekable(iter(items))

    # Extract the top-level section
    section = extract_section_pages(pages, toc)
    assert section is not None
    assert section.title == "Chapter 1"
    assert section.start_page == 0
    assert section.end_page == 5  # Before Chapter 2
    assert len(section.children) == 2  # Two level-2 sections

    # Check first level-2 section
    section_1_1 = section.children[0]
    assert section_1_1.title == "Section 1.1"
    assert section_1_1.start_page == 1
    assert section_1_1.end_page == 4  # Before Section 1.2
    assert len(section_1_1.children) == 2  # Two level-3 sections

    # Check first level-3 section
    subsection_1_1_1 = section_1_1.children[0]
    assert subsection_1_1_1.title == "Subsection 1.1.1"
    assert subsection_1_1_1.start_page == 2
    assert subsection_1_1_1.end_page == 3  # Before Subsection 1.1.2
    assert len(subsection_1_1_1.children) == 1  # One level-4 section

    # Check level-4 section
    subsubsection = subsection_1_1_1.children[0]
    assert subsubsection.title == "Sub-subsection 1.1.1.1"
    assert subsubsection.start_page == 3
    assert subsubsection.end_page == 3  # Just one page
    assert len(subsubsection.children) == 0  # No children


@pytest.mark.parametrize(
    "toc",
    [[(1, "Chapter 1", 0), (1, "Chapter 2", 3)], [[1, "Chapter 1", 0], [1, "Chapter 2", 3]]],
    ids=["tuples", "lists"],
)
def test_extract_sections_with_different_toc_formats(toc):
    sections = extract_sections(PAGES, toc)

    assert [s.title for s in sections] == ["Chapter 1", "Chapter 2"]


def test_parse_ebook_reads_a_real_book_in_the_sandbox(tmp_path):
    path = tmp_path / "book.epub"
    path.write_bytes(book_files.epub(book_files.markup(3)))

    ebook = parse_ebook(path)

    assert ebook.title == "t"
    assert ebook.full_content.split() == ["a", "a", "a"]


def test_parse_ebook_refuses_a_book_mupdf_cannot_read(tmp_path):
    path = tmp_path / "book.epub"
    path.write_bytes(b"PK\x03\x04" + b"\0" * 100)

    with pytest.raises(ValueError):
        parse_ebook(path)


def write_epub(path, entries: dict[str, bytes]):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", zipfile.ZIP_STORED)
        for name, content in entries.items():
            z.writestr(name, content)


def understate_size(path, real: int, declared: int):
    """Rewrite an entry's uncompressed size in its local and central headers."""
    data = path.read_bytes()
    real_field, declared_field = struct.pack("<I", real), struct.pack("<I", declared)
    assert data.count(real_field) == 2
    path.write_bytes(data.replace(real_field, declared_field))


def lying_epub(path):
    write_epub(path, {"a.xhtml": b"a" * 200_003})
    understate_size(path, 200_003, 1000)


def zip_of_one_entry(name: bytes, data: bytes, crc: int, size: int) -> bytes:
    """A deflated single-entry zip whose headers declare ``crc`` and ``size``."""
    local = struct.pack("<4s5H3L2H", b"PK\x03\x04", 20, 0, 8, 0, 0, crc, len(data), size, len(name), 0)
    central = struct.pack(
        "<4s6H3L5H2L", b"PK\x01\x02", 20, 20, 0, 8, 0, 0, crc, len(data), size, len(name), 0, 0, 0, 0, 0, 0
    )
    entry, directory = local + name + data, central + name
    end = struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 1, 1, len(directory), len(entry), 0)
    return entry + directory + end


def prefix_describing_epub(path):
    """Size and CRC describe the stream's first 1000 bytes; it inflates to 200,003."""
    deflater = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    data = deflater.compress(b"a" * 200_003) + deflater.flush()
    path.write_bytes(zip_of_one_entry(b"a.xhtml", data, zlib.crc32(b"a" * 1000), 1000))


@pytest.mark.parametrize(
    "make_book",
    [
        pytest.param(lambda p: write_epub(p, {"a.xhtml": b"a " * 100_000}), id="markup"),
        pytest.param(lambda p: write_epub(p, {"content.dat": b"a " * 100_000}), id="markup-other-name"),
        pytest.param(lambda p: write_epub(p, {f"{n}.xhtml": b"" for n in range(60)}), id="entry-count"),
        pytest.param(lying_epub, id="understated-size"),
        pytest.param(prefix_describing_epub, id="headers-describe-a-prefix"),
    ],
)
def test_parse_ebook_refuses_book_too_large_to_parse(tmp_path, make_book):
    path = tmp_path / "bomb.epub"
    make_book(path)

    with (
        patch.object(settings, "MAX_EBOOK_INFLATED_BYTES", 100_000),
        patch.object(settings, "MAX_EBOOK_ZIP_ENTRIES", 50),
        patch("memory.parsers.ebook.mupdf_sandbox.book_contents") as mupdf_open,
        pytest.raises(ValueError),
    ):
        parse_ebook(path)
    mupdf_open.assert_not_called()


def test_check_parse_size_accepts_book_under_the_caps(tmp_path):
    path = tmp_path / "book.epub"
    write_epub(path, {"a.xhtml": b"a " * 40_000, "cover.jpg": b"\x00" * 4096})

    with patch.object(settings, "MAX_EBOOK_INFLATED_BYTES", 100_000):
        check_parse_size(path)


def test_zip_of_one_entry_fools_zipfile(tmp_path):
    # The prefix-describing book reads back as its 1000-byte prefix through zipfile.
    path = tmp_path / "book.epub"
    prefix_describing_epub(path)

    with zipfile.ZipFile(path) as archive:
        assert archive.read("a.xhtml") == b"a" * 1000


LARGE_TEXT = book_files.markup(25_000)  # 200,025 bytes


@pytest.mark.parametrize(
    "data",
    [
        book_files.mobi(LARGE_TEXT),
        book_files.mobi(LARGE_TEXT, signature=b"TEXtREAd"),
        book_files.palmdoc_repeats(30),
        book_files.palmdb([LARGE_TEXT[:4096]], compression=17480),
        book_files.palmdb([LARGE_TEXT[:4096]] * 2, offsets=[2000, 1000, 100]),
        book_files.fb2(25_000),
        LARGE_TEXT,
        book_files.mobi(LARGE_TEXT) + book_files.epub(b"<html/>"),
        book_files.mobi(LARGE_TEXT, name=b"%PDF-1.7"),
        b"x" + book_files.epub(b"<html/>"),
        b"junk %PDF-1.7\n" + LARGE_TEXT,
        b"%PDF-1.7\n" + book_files.fb2(25_000),
    ],
    ids=[
        "mobi", "palmdoc", "compressed mobi", "huff mobi", "damaged record table", "fb2", "html",
        "mobi with a zip appended", "mobi named a pdf", "zip after junk", "pdf header after junk",
        "pdf header then fb2",
    ],
)
def test_parse_ebook_refuses_unzipped_book_too_large_to_parse(tmp_path, data):
    path = tmp_path / "book.pdf"
    path.write_bytes(data)

    with (
        patch.object(settings, "MAX_EBOOK_INFLATED_BYTES", 100_000),
        patch("memory.parsers.ebook.mupdf_sandbox.book_contents") as mupdf_open,
        pytest.raises(ValueError),
    ):
        parse_ebook(path)
    mupdf_open.assert_not_called()


@pytest.mark.parametrize(
    "data",
    [
        book_files.mobi(book_files.markup(10_000)),
        book_files.palmdoc_repeats(2),
        book_files.fb2(10_000),
        b"%PDF-1.7\n" + b"0" * 200_000,
    ],
    ids=["mobi", "compressed mobi", "fb2", "pdf"],
)
def test_check_parse_size_accepts_unzipped_book_under_the_cap(tmp_path, data):
    path = tmp_path / "book.mobi"
    path.write_bytes(data)

    with patch.object(settings, "MAX_EBOOK_INFLATED_BYTES", 100_000):
        check_parse_size(path)


def mupdf_text_bytes(data: bytes) -> int:
    with pymupdf.open(stream=data) as doc:
        return "".join(page.get_text() for page in doc.pages()).count("a") * len(b"<p>a</p>")


@pytest.mark.parametrize(
    "records, compression",
    [
        ([book_files.markup(12_800)] * 3, 1),
        ([book_files.palmdoc_record()] * 3, 2),
        ([book_files.palmdoc_record() * 20] * 3, 2),
    ],
    ids=["large uncompressed records", "compressed records", "large compressed records"],
)
def test_mupdf_reads_at_most_one_record_size_per_text_record(records, compression):
    """check_palmdb_size's bound depends on this."""
    assert mupdf_text_bytes(book_files.palmdb(records, compression=compression)) <= 3 * 4096


@pytest.mark.parametrize("records, refused", [(24, False), (25, True)])
def test_check_parse_size_counts_mobi_text_records(tmp_path, records, refused):
    path = tmp_path / "book.mobi"
    path.write_bytes(book_files.palmdoc_repeats(records))

    with patch.object(settings, "MAX_EBOOK_INFLATED_BYTES", 100_000):
        assert check_size_refuses(path) == refused


def check_size_refuses(path) -> bool:
    try:
        check_parse_size(path)
    except ValueError:
        return True
    return False


CHAPTER = book_files.markup(3_750)  # 30,025 bytes


def chapter_book(name: str, href: str, spine: list[str], **kwargs) -> bytes:
    """An EPUB of CHAPTER stored as `name`, manifest item "c" pointing at `href`."""
    return book_files.epub_package({name: CHAPTER}, [("c", href)], spine, **kwargs)


SHARED_FILE = book_files.epub_package(
    {"c.xhtml": CHAPTER}, [(f"i{n}", "c.xhtml") for n in range(4)], [f"i{n}" for n in range(4)]
)
TOO_MANY = "chapters add up"


@pytest.mark.parametrize(
    "data, match",
    [
        (book_files.epub(CHAPTER, repeats=4), TOO_MANY),
        (SHARED_FILE, TOO_MANY),
        (chapter_book("C.XHTML", "c.xhtml", ["c"] * 4), TOO_MANY),
        (chapter_book("c d.xhtml", "c%20d.xhtml", ["c"] * 4), TOO_MANY),
        (chapter_book("OEBPS/c.xhtml", "/c.xhtml", ["c"] * 4, opf_path="OEBPS/content.opf"), TOO_MANY),
        (chapter_book("c.xhtml", "c.xhtml", ["unknown"] * 4), TOO_MANY),
        (book_files.epub_package({"c.xhtml": b""}, [("c", "c.xhtml")], ["c"] * 51), "more than 50 itemref"),
        (chapter_book("c.xhtml", "c.xhtml", ["c"], container_path="gone.opf"), "missing package"),
        (chapter_book("c.xhtml", "./c.xhtml", ["c"] * 4), TOO_MANY),
        (chapter_book("x/../c.xhtml", "c.xhtml", ["c"]), "unusual name"),
        (chapter_book("x//c.xhtml", "c.xhtml", ["c"]), "unusual name"),
        (book_files.epub_package({"c.xhtml": b""}, [("c", "c.xhtml"), ("c", "c.xhtml")], ["c"]), "repeats a manifest id"),
        (book_files.epub_package({"c.xhtml": b""}, [(f"i{n}", "c.xhtml") for n in range(51)], []), "more than 50 item"),
    ],
    ids=[
        "repeated idref", "ids sharing a file", "entry name case", "percent-encoded href",
        "rooted href", "unresolved idref", "spine too long", "missing package", "dotted href",
        "dot-dot entry", "empty segment entry", "repeated manifest id", "manifest too long",
    ],
)
def test_check_parse_size_charges_each_spine_chapter(tmp_path, data, match):
    path = tmp_path / "book.epub"
    path.write_bytes(data)

    with (
        patch.object(settings, "MAX_EBOOK_INFLATED_BYTES", 100_000),
        patch.object(settings, "MAX_EBOOK_ZIP_ENTRIES", 50),
        pytest.raises(ValueError, match=match),
    ):
        check_parse_size(path)


def unreadable_package(path):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml", book_files.CONTAINER.format("content.opf"))
        archive.writestr("content.opf", "<package><spine>")


def test_check_parse_size_refuses_an_unreadable_package(tmp_path):
    path = tmp_path / "book.epub"
    unreadable_package(path)

    with pytest.raises(ValueError, match="unreadable package"):
        check_parse_size(path)


@pytest.mark.parametrize(
    "data",
    [
        book_files.epub(CHAPTER, repeats=3),
        book_files.epub_package(
            {"a.xhtml": CHAPTER, "b.xhtml": CHAPTER}, [("a", "a.xhtml"), ("b", "b.xhtml")], ["a", "b"]
        ),
    ],
    ids=["three repeats", "two chapters"],
)
def test_check_parse_size_accepts_a_spine_under_the_cap(tmp_path, data):
    path = tmp_path / "book.epub"
    path.write_bytes(data)

    with patch.object(settings, "MAX_EBOOK_INFLATED_BYTES", 100_000):
        check_parse_size(path)


def test_check_parse_size_ignores_a_zip_without_a_container(tmp_path):
    path = tmp_path / "comic.cbz"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("1.png", b"\0" * 1000)

    check_parse_size(path)


def test_check_parse_size_refuses_too_many_package_files(tmp_path):
    rootfiles = "".join(f"<rootfile full-path='p{n}.opf'/>" for n in range(9))
    path = tmp_path / "book.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml", f"<container><rootfiles>{rootfiles}</rootfiles></container>")

    with pytest.raises(ValueError, match="more than 8 rootfile"):
        check_parse_size(path)


@pytest.mark.parametrize(
    "data",
    [
        gzip.compress(book_files.markup(1024 * 1024), 9),
        gzip.compress(book_files.epub(book_files.markup(10))),
        gzip.compress(book_files.fb2(10)),
        gzip.compress(book_files.nested_form_pdf(2)),
    ],
    ids=["html", "epub", "fb2", "pdf"],
)
def test_check_parse_size_refuses_a_gzip_compressed_book(tmp_path, data):
    path = tmp_path / "book.epub"
    path.write_bytes(data)

    with pytest.raises(ValueError, match="gzip"):
        check_parse_size(path)
