from dataclasses import dataclass, field
import io
import logging
import mimetypes
import pathlib
import tempfile
from contextlib import contextmanager
from typing import Any, Generator, Sequence, cast

from memory.common import chunker, summarizer
from memory.parsers import ebook
import pymupdf  # PyMuPDF
import pypandoc
from PIL import Image

logger = logging.getLogger(__name__)

MulitmodalChunk = Image.Image | str | bytes

TEXT_EXTENSIONS = {
    ".md",
    ".txt",
    ".py",
    ".js",
    ".html",
    ".css",
    ".json",
    ".xml",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
}
IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
}
CUSTOM_EXTENSIONS = {
    ".epub": "application/epub+zip",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def get_mime_type(path: pathlib.Path) -> str:
    mime_type, _ = mimetypes.guess_type(str(path))
    if mime_type:
        return mime_type
    ext = path.suffix.lower()
    return CUSTOM_EXTENSIONS.get(ext, "application/octet-stream")


def is_text_file(path: pathlib.Path) -> bool:
    mime_type = get_mime_type(path)
    text_mimes = {
        "application/json",
        "application/xml",
        "application/javascript",
        "application/x-yaml",
        "application/yaml",
    }
    return (
        mime_type.startswith("text/")
        or mime_type in text_mimes
        or path.suffix.lower() in TEXT_EXTENSIONS
    )


def is_image_file(path: pathlib.Path) -> bool:
    mime_type = get_mime_type(path)
    return mime_type.startswith("image/") or path.suffix.lower() in IMAGE_EXTENSIONS


def merge_metadata(*metadata: dict[str, Any]) -> dict[str, Any]:
    final = {}
    for m in metadata:
        data = m.copy()
        if tags := set(data.pop("tags", []) or []):
            final["tags"] = tags | final.get("tags", set())
        final |= data
    return final


@dataclass
class DataChunk:
    data: Sequence[MulitmodalChunk]
    metadata: dict[str, Any] = field(default_factory=dict)
    mime_type: str = "text/plain"
    modality: str | None = None


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
    image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    image.format = "jpeg"
    return image


def doc_to_images(
    content: bytes | str | pathlib.Path, modality: str = "doc"
) -> list[DataChunk]:
    with as_file(content) as file_path:
        with pymupdf.open(file_path) as pdf:
            return [
                DataChunk(
                    data=[page_to_image(page)],
                    metadata={
                        "page": page.number,
                        "width": page.rect.width,
                        "height": page.rect.height,
                    },
                    mime_type="image/jpeg",
                    modality=modality,
                )
                for page in pdf.pages()
            ]


def docx_to_pdf(
    docx_path: pathlib.Path,
    output_path: pathlib.Path | None = None,
) -> pathlib.Path:
    """Convert DOCX to PDF using pypandoc"""
    if output_path is None:
        output_path = docx_path.with_suffix(".pdf")

    # Now that we have all packages installed, try xelatex first as it has better Unicode support
    try:
        logger.info(f"Converting {docx_path} to PDF using xelatex")
        pypandoc.convert_file(
            str(docx_path),
            format="docx",
            to="pdf",
            outputfile=str(output_path),
            extra_args=[
                "--pdf-engine=xelatex",
                "--variable=geometry:margin=1in",
                "--lua-filter=/app/unnest-table.lua",
            ],
        )
        logger.info(f"Successfully converted {docx_path} to PDF")
        return output_path
    except Exception as e:
        logger.error(f"Error converting document to PDF: {e}")
        raise


def extract_docx(docx_path: pathlib.Path | bytes | str) -> list[DataChunk]:
    """Extract content from DOCX by converting to PDF first, then processing"""
    with as_file(docx_path) as file_path:
        pdf_path = docx_to_pdf(file_path)
        logger.info(f"Extracted PDF from {file_path}")
        return doc_to_images(pdf_path)


def extract_image(content: bytes | str | pathlib.Path) -> list[DataChunk]:
    if isinstance(content, pathlib.Path):
        image = Image.open(content)
    elif isinstance(content, bytes):
        image = Image.open(io.BytesIO(content))
    else:
        raise ValueError(f"Unsupported content type: {type(content)}")
    image_format = image.format or "jpeg"
    return [DataChunk(data=[image], mime_type=f"image/{image_format.lower()}")]


def extract_text(
    content: bytes | str | pathlib.Path,
    chunk_size: int | None = None,
    metadata: dict[str, Any] = {},
    modality: str = "text",
    skip_summary: bool = False,
) -> list[DataChunk]:
    if isinstance(content, pathlib.Path):
        content = content.read_text()
    if isinstance(content, bytes):
        content = content.decode("utf-8")

    content = cast(str, content)
    chunks = [
        DataChunk(data=[c], modality=modality, metadata=metadata)
        for c in chunker.chunk_text(content, chunk_size or chunker.DEFAULT_CHUNK_TOKENS)
    ]
    if not skip_summary and content and len(content) > chunker.DEFAULT_CHUNK_TOKENS * 2:
        summary, tags = summarizer.summarize(content)
        chunks.append(
            DataChunk(
                data=[summary],
                metadata=merge_metadata(metadata, {"tags": tags}),
                modality=modality,
            )
        )
    return chunks


def extract_ebook(file_path: str | pathlib.Path) -> list[DataChunk]:
    book = ebook.parse_ebook(file_path)
    return [
        DataChunk(
            mime_type="text/plain",
            data=[
                page.strip()
                for section in book.sections
                for page in section.pages
                if page.strip()
            ],
        )
    ]


def extract_data_chunks(
    mime_type: str,
    content: bytes | str | pathlib.Path,
    chunk_size: int | None = None,
    skip_summary: bool = False,
) -> list[DataChunk]:
    chunks = []
    logger.info(f"Extracting content from {mime_type}")
    if mime_type == "application/pdf":
        chunks = doc_to_images(content)
    elif mime_type in [
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    ]:
        chunks = extract_docx(content)
    elif mime_type.startswith("text/"):
        chunks = extract_text(content, chunk_size, skip_summary=skip_summary)
    elif mime_type.startswith("image/"):
        chunks = extract_image(content)
    elif mime_type == "application/epub+zip":
        chunks = extract_ebook(cast(pathlib.Path, content))
    return chunks
