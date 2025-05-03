from contextlib import contextmanager
import io
import pathlib
import tempfile
import pymupdf  # PyMuPDF
from PIL import Image
from typing import Any, TypedDict, Generator



MulitmodalChunk = Image.Image | str
class Page(TypedDict):
    contents: list[MulitmodalChunk]
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
    pix = page.get_pixmap()
    return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)


def doc_to_images(content: bytes | str | pathlib.Path) -> list[Page]:
    with as_file(content) as file_path:
        with pymupdf.open(file_path) as pdf:
            return [
                {
                    "contents": page_to_image(page),
                    "metadata": {
                        "page": page.number,
                        "width": page.rect.width,
                        "height": page.rect.height,
                    }
                } for page in pdf.pages()
            ]


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
    if mime_type.startswith("text/"):
        return extract_text(content)
    if mime_type.startswith("image/"):
        return extract_image(content)
    
    # Return empty list for unknown mime types
    return []
