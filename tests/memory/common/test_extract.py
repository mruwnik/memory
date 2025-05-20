import pathlib
import pytest
import pymupdf
from PIL import Image
import io
import shutil
from memory.common.extract import (
    as_file,
    extract_text,
    extract_content,
    doc_to_images,
    extract_image,
    docx_to_pdf,
    extract_docx,
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
        ("simple text", [{"contents": ["simple text"], "metadata": {}}]),
        (b"bytes text", [{"contents": ["bytes text"], "metadata": {}}]),
    ],
)
def test_extract_text(input_content, expected):
    assert extract_text(input_content) == expected


def test_extract_text_with_path(tmp_path):
    test_file = tmp_path / "test.txt"
    test_file.write_text("file text content")

    assert extract_text(test_file) == [
        {"contents": ["file text content"], "metadata": {}}
    ]


def test_doc_to_images():
    result = doc_to_images(REGULAMIN)

    assert len(result) == 2
    with pymupdf.open(REGULAMIN) as pdf:
        for page, pdf_page in zip(result, pdf.pages()):
            pix = pdf_page.get_pixmap()
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            assert page["contents"] == [img]
            assert page["metadata"] == {
                "page": pdf_page.number,
                "width": pdf_page.rect.width,
                "height": pdf_page.rect.height,
            }


def test_extract_image_with_path(tmp_path):
    img = Image.new("RGB", (100, 100), color="red")
    img_path = tmp_path / "test.png"
    img.save(img_path)

    (page,) = extract_image(img_path)
    assert page["contents"][0].tobytes() == img.convert("RGB").tobytes()
    assert page["metadata"] == {}


def test_extract_image_with_bytes():
    img = Image.new("RGB", (100, 100), color="blue")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    img_bytes = buffer.getvalue()

    (page,) = extract_image(img_bytes)
    assert page["contents"][0].tobytes() == img.convert("RGB").tobytes()
    assert page["metadata"] == {}


def test_extract_image_with_str():
    with pytest.raises(ValueError):
        extract_image("test")


@pytest.mark.parametrize(
    "mime_type,content",
    [
        ("text/plain", "Text content"),
        ("text/html", "<html>content</html>"),
        ("text/markdown", "# Heading"),
        ("text/csv", "a,b,c"),
    ],
)
def test_extract_content_different_text_types(mime_type, content):
    assert extract_content(mime_type, content) == [
        {"contents": [content], "metadata": {}}
    ]


def test_extract_content_pdf():
    result = extract_content("application/pdf", REGULAMIN)

    assert len(result) == 2
    assert all(
        isinstance(page["contents"], list)
        and all(isinstance(c, Image.Image) for c in page["contents"])
        for page in result
    )
    assert all("page" in page["metadata"] for page in result)
    assert all("width" in page["metadata"] for page in result)
    assert all("height" in page["metadata"] for page in result)


def test_extract_content_image(tmp_path):
    # Create a test image
    img = Image.new("RGB", (100, 100), color="red")
    img_path = tmp_path / "test_img.png"
    img.save(img_path)

    (result,) = extract_content("image/png", img_path)

    assert isinstance(result["contents"][0], Image.Image)
    assert result["contents"][0].size == (100, 100)
    assert result["metadata"] == {}


def test_extract_content_unsupported_type():
    assert extract_content("unsupported/type", "content") == []


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


@pytest.mark.skipif(not is_pdflatex_available(), reason="pdflatex not installed")
def test_extract_docx():
    pages = extract_docx(SAMPLE_DOCX)

    assert len(pages) > 0
    assert all(isinstance(page, dict) for page in pages)
    assert all("contents" in page for page in pages)
    assert all("metadata" in page for page in pages)
    assert all(isinstance(page["contents"][0], Image.Image) for page in pages)
