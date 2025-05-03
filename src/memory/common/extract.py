from contextlib import contextmanager
import io
import pathlib
import tempfile
import pypandoc
import pymupdf  # PyMuPDF
from PIL import Image
from typing import Any, TypedDict, Generator, Sequence


MulitmodalChunk = Image.Image | str


class Page(TypedDict):
    contents: Sequence[MulitmodalChunk]
    metadata: dict[str, Any]


@contextmanager
def as_file(content: bytes | str | pathlib.Path) -> Generator[pathlib.Path, None, None]:
    if isinstance(content, pathlib.Path):
        yield content
    else:
        mode = "w" if isinstance(content, str) else "wb"
        with tempfile.NamedTemporaryFile(mode=mode) as f:
            f.write(content)
            f.flush()
            yield pathlib.Path(f.name)


def page_to_image(page: pymupdf.Page) -> Image.Image:
    pix = page.get_pixmap()  # type: ignore
    return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)


def doc_to_images(content: bytes | str | pathlib.Path) -> list[Page]:
    with as_file(content) as file_path:
        with pymupdf.open(file_path) as pdf:
            return [
                {
                    "contents": [page_to_image(page)],
                    "metadata": {
                        "page": page.number,
                        "width": page.rect.width,
                        "height": page.rect.height,
                    },
                }
                for page in pdf.pages()
            ]


def docx_to_pdf(
    docx_path: pathlib.Path,
    output_path: pathlib.Path | None = None,
) -> pathlib.Path:
    """Convert DOCX to PDF using pypandoc"""
    if output_path is None:
        output_path = docx_path.with_suffix(".pdf")

    pypandoc.convert_file(str(docx_path), "pdf", outputfile=str(output_path))

    return output_path


def extract_docx(docx_path: pathlib.Path) -> list[Page]:
    """Extract content from DOCX by converting to PDF first, then processing"""
    with as_file(docx_path) as file_path:
        pdf_path = docx_to_pdf(file_path)
        return doc_to_images(pdf_path)


def extract_image(content: bytes | str | pathlib.Path) -> list[Page]:
    if isinstance(content, pathlib.Path):
        image = Image.open(content)
    elif isinstance(content, bytes):
        image = Image.open(io.BytesIO(content))
    else:
        raise ValueError(f"Unsupported content type: {type(content)}")
    return [{"contents": [image], "metadata": {}}]


def extract_text(content: bytes | str | pathlib.Path) -> list[Page]:
    if isinstance(content, pathlib.Path):
        content = content.read_text()
    if isinstance(content, bytes):
        content = content.decode("utf-8")

    return [{"contents": [content], "metadata": {}}]


def extract_content(mime_type: str, content: bytes | str | pathlib.Path) -> list[Page]:
    if mime_type == "application/pdf":
        return doc_to_images(content)
    if isinstance(content, pathlib.Path) and mime_type in [
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    ]:
        return extract_docx(content)
    if mime_type.startswith("text/"):
        return extract_text(content)
    if mime_type.startswith("image/"):
        return extract_image(content)

    # Return empty list for unknown mime types
    return []
