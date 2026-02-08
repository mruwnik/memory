import pathlib
import pytest
import pymupdf
from PIL import Image
import io
import shutil
from unittest.mock import patch
from memory.common.extract import (
    as_file,
    extract_text,
    extract_docx,
    extract_docx_text,
    doc_to_images,
    extract_image,
    docx_to_pdf,
    merge_metadata,
    DataChunk,
)


REGULAMIN = pathlib.Path(__file__).parent.parent.parent / "data" / "regulamin.pdf"
SAMPLE_DOCX = pathlib.Path(__file__).parent.parent.parent / "data" / "sample.docx"


# Helper to check if pdflatex is available
def is_pdflatex_available():
    return shutil.which("pdflatex") is not None


def test_as_file_with_path(tmp_path):
    test_path = tmp_path / "test.txt"
    test_path.write_text("test content")

    with as_file(test_path) as path:
        assert path == test_path
        assert path.read_text() == "test content"


def test_as_file_with_bytes():
    content = b"test content"
    with as_file(content) as path:
        assert pathlib.Path(path).read_bytes() == content


def test_as_file_with_str():
    content = "test content"
    with as_file(content) as path:
        assert pathlib.Path(path).read_text() == content


@pytest.mark.parametrize(
    "input_content,expected",
    [
        (
            "simple text",
            [DataChunk(data=["simple text"], metadata={}, modality="text")],
        ),
        (b"bytes text", [DataChunk(data=["bytes text"], metadata={}, modality="text")]),
    ],
)
def test_extract_text(input_content, expected):
    assert extract_text(input_content) == expected


def test_extract_text_with_path(tmp_path):
    test_file = tmp_path / "test.txt"
    test_file.write_text("file text content")

    assert extract_text(test_file) == [
        DataChunk(data=["file text content"], metadata={}, modality="text")
    ]


def test_doc_to_images():
    result = doc_to_images(REGULAMIN)

    assert len(result) == 2
    with pymupdf.open(REGULAMIN) as pdf:
        for page, pdf_page in zip(result, pdf.pages()):
            pix = pdf_page.get_pixmap()
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            assert page.data == [img]
            assert page.metadata == {
                "page": pdf_page.number,
                "width": pdf_page.rect.width,
                "height": pdf_page.rect.height,
            }


def test_extract_image_with_path(tmp_path):
    img = Image.new("RGB", (100, 100), color="red")
    img_path = tmp_path / "test.png"
    img.save(img_path)

    (page,) = extract_image(img_path)
    assert page.data[0].tobytes() == img.convert("RGB").tobytes()  # type: ignore
    assert page.metadata == {}


def test_extract_image_with_bytes():
    img = Image.new("RGB", (100, 100), color="blue")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    img_bytes = buffer.getvalue()

    (page,) = extract_image(img_bytes)
    assert page.data[0].tobytes() == img.convert("RGB").tobytes()  # type: ignore
    assert page.metadata == {}


def test_extract_image_with_str():
    with pytest.raises(ValueError):
        extract_image("test")


@pytest.mark.skipif(not is_pdflatex_available(), reason="pdflatex not installed")
def test_docx_to_pdf(tmp_path):
    output_path = tmp_path / "output.pdf"
    result_path = docx_to_pdf(SAMPLE_DOCX, output_path)

    assert result_path == output_path
    assert result_path.exists()
    assert result_path.suffix == ".pdf"

    # Verify the PDF is valid by opening it
    with pymupdf.open(result_path) as pdf:
        assert pdf.page_count > 0


@pytest.mark.skipif(not is_pdflatex_available(), reason="pdflatex not installed")
def test_docx_to_pdf_default_output():
    # Test with default output path
    result_path = docx_to_pdf(SAMPLE_DOCX)

    assert result_path == SAMPLE_DOCX.with_suffix(".pdf")
    assert result_path.exists()


@pytest.mark.parametrize(
    "dicts,expected",
    [
        # Empty input
        ([], {}),
        # Single dict without tags
        ([{"key": "value"}], {"key": "value"}),
        # Single dict with tags as list
        (
            [{"key": "value", "tags": ["tag1", "tag2"]}],
            {"key": "value", "tags": {"tag1", "tag2"}},
        ),
        # Single dict with tags as set
        (
            [{"key": "value", "tags": {"tag1", "tag2"}}],
            {"key": "value", "tags": {"tag1", "tag2"}},
        ),
        # Multiple dicts without tags
        (
            [{"key1": "value1"}, {"key2": "value2"}],
            {"key1": "value1", "key2": "value2"},
        ),
        # Multiple dicts with non-overlapping tags
        (
            [
                {"key1": "value1", "tags": ["tag1"]},
                {"key2": "value2", "tags": ["tag2"]},
            ],
            {"key1": "value1", "key2": "value2", "tags": {"tag1", "tag2"}},
        ),
        # Multiple dicts with overlapping tags
        (
            [
                {"key1": "value1", "tags": ["tag1", "tag2"]},
                {"key2": "value2", "tags": ["tag2", "tag3"]},
            ],
            {"key1": "value1", "key2": "value2", "tags": {"tag1", "tag2", "tag3"}},
        ),
        # Overlapping keys - later dict wins
        (
            [
                {"key": "value1", "other": "data1"},
                {"key": "value2", "another": "data2"},
            ],
            {"key": "value2", "other": "data1", "another": "data2"},
        ),
        # Mixed tags types (list and set)
        (
            [
                {"key1": "value1", "tags": ["tag1", "tag2"]},
                {"key2": "value2", "tags": {"tag3", "tag4"}},
            ],
            {
                "key1": "value1",
                "key2": "value2",
                "tags": {"tag1", "tag2", "tag3", "tag4"},
            },
        ),
        # Empty tags
        (
            [{"key": "value", "tags": []}, {"key2": "value2", "tags": []}],
            {"key": "value", "key2": "value2"},
        ),
        # None values
        (
            [{"key1": None, "key2": "value"}, {"key3": None}],
            {"key1": None, "key2": "value", "key3": None},
        ),
        # Complex nested structures
        (
            [
                {"nested": {"inner": "value1"}, "list": [1, 2, 3], "tags": ["tag1"]},
                {"nested": {"inner": "value2"}, "list": [4, 5], "tags": ["tag2"]},
            ],
            {"nested": {"inner": "value2"}, "list": [4, 5], "tags": {"tag1", "tag2"}},
        ),
        # Boolean and numeric values
        (
            [
                {"bool": True, "int": 42, "float": 3.14, "tags": ["numeric"]},
                {"bool": False, "int": 100},
            ],
            {"bool": False, "int": 100, "float": 3.14, "tags": {"numeric"}},
        ),
        # Three or more dicts
        (
            [
                {"a": 1, "tags": ["t1"]},
                {"b": 2, "tags": ["t2", "t3"]},
                {"c": 3, "a": 10, "tags": ["t3", "t4"]},
            ],
            {"a": 10, "b": 2, "c": 3, "tags": {"t1", "t2", "t3", "t4"}},
        ),
        # Dict with only tags
        ([{"tags": ["tag1", "tag2"]}], {"tags": {"tag1", "tag2"}}),
        # Empty dicts
        ([{}, {}], {}),
        # Mix of empty and non-empty dicts
        (
            [{}, {"key": "value", "tags": ["tag"]}, {}],
            {"key": "value", "tags": {"tag"}},
        ),
    ],
)
def test_merge_metadata(dicts, expected):
    assert merge_metadata(*dicts) == expected


def test_extract_docx_text():
    """extract_docx_text extracts plain text from a DOCX without needing LaTeX."""
    chunks = extract_docx_text(SAMPLE_DOCX)
    assert len(chunks) >= 1
    assert all(isinstance(c, DataChunk) for c in chunks)
    # Should contain actual text content
    text = " ".join(str(d) for c in chunks for d in c.data)
    assert len(text) > 0


def test_extract_docx_falls_back_on_pdf_failure():
    """When PDF conversion fails, extract_docx falls back to text extraction."""
    with patch(
        "memory.common.extract.docx_to_pdf",
        side_effect=RuntimeError("Pandoc died with exitcode 43"),
    ):
        chunks = extract_docx(SAMPLE_DOCX)

    assert len(chunks) >= 1
    assert all(isinstance(c, DataChunk) for c in chunks)
    text = " ".join(str(d) for c in chunks for d in c.data)
    assert len(text) > 0


@pytest.mark.skipif(not is_pdflatex_available(), reason="pdflatex not installed")
def test_extract_docx_uses_pdf_when_available():
    """When PDF conversion succeeds, extract_docx returns image chunks (from PDF)."""
    chunks = extract_docx(SAMPLE_DOCX)
    assert len(chunks) >= 1
    # PDF path produces image chunks; each chunk's data contains PIL Images
    assert any(
        isinstance(d, Image.Image) for c in chunks for d in c.data
    )
