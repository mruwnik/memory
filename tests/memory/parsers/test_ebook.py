from unittest.mock import MagicMock, patch

import pytest
import fitz

from memory.parsers.ebook import (
    Peekable,
    extract_epub_metadata,
    get_pages,
    extract_section_pages,
    extract_sections,
    parse_ebook,
    Section,
)


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


@pytest.fixture
def mock_doc():
    doc = MagicMock()
    doc.metadata = {
        "title": "Test Book",
        "author": "Test Author",
        "creator": "Test Creator",
        "producer": "Test Producer",
    }
    doc.page_count = 5

    # Mock pages
    doc.__getitem__.side_effect = lambda i: MagicMock(
        get_text=lambda: f"Content of page {i}"
    )

    # Mock TOC
    doc.get_toc.return_value = [
        [1, "Chapter 1", 0],
        [2, "Section 1.1", 1],
        [2, "Section 1.2", 2],
        [1, "Chapter 2", 3],
        [2, "Section 2.1", 4],
    ]

    return doc


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
    doc = MagicMock()
    doc.metadata = metadata_input
    assert extract_epub_metadata(doc) == expected


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
    ],
)
def test_get_pages(mock_doc, start_page, end_page, expected_content):
    assert get_pages(mock_doc, start_page, end_page) == expected_content


@pytest.fixture
def mock_toc_items():
    items = [
        (1, "Chapter 1", 0),  # Level 1, start at page 0
        (2, "Section 1.1", 1),  # Level 2, start at page 1
        (2, "Section 1.2", 2),  # Level 2, start at page 2
        (1, "Chapter 2", 3),  # Level 1, start at page 3
    ]
    return Peekable(iter(items))


def test_extract_section_pages(mock_doc, mock_toc_items):
    assert extract_section_pages(mock_doc, mock_toc_items) == Section(
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


def test_extract_sections(mock_doc):
    assert extract_sections(mock_doc) == [
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
            end_page=5,
            pages=["Content of page 3", "Content of page 4"],
            children=[
                Section(
                    title="Section 2.1",
                    number=1,
                    start_page=4,
                    end_page=5,
                    pages=["Content of page 4"],
                ),
            ],
        ),
    ]


def test_extract_sections_no_toc(mock_doc):
    mock_doc.get_toc.return_value = []
    mock_doc.get_text.return_value = "Full document content"

    assert extract_sections(mock_doc) == [
        Section(
            title="Content",
            number=1,
            start_page=0,
            end_page=5,
            pages=[f"Content of page {i}" for i in range(5)],
            children=[],
        ),
    ]


@patch("fitz.open")
def test_parse_ebook(mock_open, mock_doc, tmp_path):
    mock_open.return_value = mock_doc

    # Create a test file
    test_file = tmp_path / "test.epub"
    test_file.touch()

    ebook = parse_ebook(test_file)

    assert ebook.title == "Test Book"
    assert ebook.author == "Test Author"
    assert len(ebook.sections) == 2
    assert ebook.file_path == test_file
    assert ebook.file_type == "epub"


@patch("fitz.open")
def test_parse_ebook_file_not_found(mock_open, tmp_path):
    non_existent_file = tmp_path / "does_not_exist.epub"

    with pytest.raises(FileNotFoundError):
        parse_ebook(non_existent_file)


@patch("fitz.open")
def test_parse_ebook_fitz_error(mock_open, tmp_path):
    # Create a test file to avoid FileNotFoundError
    test_file = tmp_path / "test.epub"
    test_file.touch()

    # Mock the fitz.open to raise the FileNotFoundError
    mock_open.side_effect = fitz.FileNotFoundError("File not found by PyMuPDF")

    with pytest.raises(fitz.FileNotFoundError):
        parse_ebook(test_file)


@patch("fitz.open")
def test_parse_ebook_no_metadata(mock_open, mock_doc, tmp_path):
    mock_doc.metadata = {}
    mock_open.return_value = mock_doc

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
@patch("fitz.open")
def test_parse_ebook_file_types(
    mock_open, mock_doc, tmp_path, file_suffix, expected_type
):
    mock_open.return_value = mock_doc

    # Create a test file with the given suffix
    test_file = tmp_path / f"test{file_suffix}"
    test_file.touch()

    ebook = parse_ebook(test_file)
    assert ebook.file_type == expected_type


def test_extract_section_pages_empty():
    """Test with empty TOC."""
    doc = MagicMock()
    doc.page_count = 5

    empty_toc = Peekable(iter([]))
    assert extract_section_pages(doc, empty_toc) is None


def test_extract_section_pages_deeply_nested():
    """Test with deeply nested TOC structure."""
    doc = MagicMock()
    doc.page_count = 10
    doc.__getitem__.side_effect = lambda i: MagicMock(
        get_text=lambda: f"Content of page {i}"
    )

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
    section = extract_section_pages(doc, toc)
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


def test_extract_sections_with_different_toc_formats():
    """Test ability to handle different TOC formats."""
    doc = MagicMock()
    doc.page_count = 5
    doc.__getitem__.side_effect = lambda i: MagicMock(
        get_text=lambda: f"Content of page {i}"
    )

    # Test with tuple format TOC
    doc.get_toc.return_value = [
        (1, "Chapter 1", 0),
        (1, "Chapter 2", 3),
    ]

    sections = extract_sections(doc)
    assert len(sections) == 2
    assert sections[0].title == "Chapter 1"
    assert sections[1].title == "Chapter 2"

    # Test with list format TOC (same representation in PyMuPDF)
    doc.get_toc.return_value = [
        [1, "Chapter 1", 0],
        [1, "Chapter 2", 3],
    ]

    sections = extract_sections(doc)
    assert len(sections) == 2
    assert sections[0].title == "Chapter 1"
    assert sections[1].title == "Chapter 2"


@patch("fitz.open")
def test_parse_ebook_full_content_generation(mock_open, mock_doc, tmp_path):
    """Test full content is correctly concatenated from sections."""
    # Setup mock document with sections
    mock_doc.metadata = {"title": "Test Book", "author": "Test Author"}
    mock_doc.page_count = 3

    # Create sections with specific content
    section1 = MagicMock()
    section1.pages = ["Content of section 1"]
    section2 = MagicMock()
    section2.pages = ["Content of section 2"]

    # Mock extract_sections to return our sections
    with patch("memory.parsers.ebook.extract_sections") as mock_extract:
        mock_extract.return_value = [section1, section2]

        mock_open.return_value = mock_doc
        test_file = tmp_path / "test.epub"
        test_file.touch()

        ebook = parse_ebook(test_file)

        # Check the full content is concatenated correctly
        assert ebook.full_content == "Content of section 1\n\nContent of section 2"
