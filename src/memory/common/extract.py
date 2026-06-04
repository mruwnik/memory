from dataclasses import dataclass, field
import io
import logging
import mimetypes
import os
import pathlib
import tempfile
from contextlib import contextmanager
from typing import Any, Generator, Sequence, cast

from memory.common import chunker, settings, summarizer
from memory.parsers import ebook
import pymupdf  # PyMuPDF
from PIL import Image

# Backstop for every Image.open in the process. PIL only *warns* (and still
# decodes) between MAX_IMAGE_PIXELS and 2x it — the band a worker OOMs in — and
# raises DecompressionBombError (a real, filter-independent exception) only
# above 2x. Set PIL's knob to half our cap so that hard error fires once an
# image exceeds settings.MAX_IMAGE_PIXELS, at any call site.
#
# This is load-bearing, not belt-and-suspenders: the Photo path chunks via
# Photo._chunk_contents -> bare Image.open (NOT safe_image_open), so for a
# photo this backstop is the *only* thing stopping the OOM. safe_image_open()
# below additionally guards the generic extract() path (image/* docs, email
# attachments) with a clearer message. Don't remove this knob.
Image.MAX_IMAGE_PIXELS = max(1, settings.MAX_IMAGE_PIXELS // 2)

try:
    import pypandoc

    pypandoc.get_pandoc_version()
    HAS_PANDOC = True
except (ImportError, OSError):
    pypandoc = None  # type: ignore[assignment]
    HAS_PANDOC = False

logger = logging.getLogger(__name__)


def _default_pandoc_lua_filter() -> str:
    """Locate the pandoc table-unnesting lua filter for docx -> pdf.

    The worker container installs it at ``/app/unnest-table.lua``; a source
    checkout keeps it under ``docker/workers/``. Prefer whichever exists so
    conversion works in both places without configuration. Override with the
    ``PANDOC_LUA_FILTER`` env var.
    """
    candidates = (
        pathlib.Path("/app/unnest-table.lua"),
        pathlib.Path(__file__).resolve().parents[3]
        / "docker"
        / "workers"
        / "unnest-table.lua",
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return str(candidates[0])


PANDOC_LUA_FILTER = pathlib.Path(
    os.getenv("PANDOC_LUA_FILTER", _default_pandoc_lua_filter())
)

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


def page_image_chunk(page: pymupdf.Page, modality: str = "doc") -> DataChunk:
    """One multimodal image chunk for a PDF page, carrying its page geometry as
    metadata. Shared by ``doc_to_images`` and ``extract_pdf`` so the two paths
    can't drift."""
    return DataChunk(
        data=[page_to_image(page)],
        metadata={
            "page": page.number,
            "width": page.rect.width,
            "height": page.rect.height,
        },
        mime_type="image/jpeg",
        modality=modality,
    )


def doc_to_images(
    content: bytes | str | pathlib.Path, modality: str = "doc"
) -> list[DataChunk]:
    with as_file(content) as file_path:
        with pymupdf.open(file_path) as pdf:
            return [page_image_chunk(page, modality) for page in pdf.pages()]


# A PDF page whose embedded text layer strips to fewer than this many characters
# is treated as image-only (a scan, or a near-blank/figure page): below this a
# get_text() chunk would just be noise (page numbers, stray ligatures, "").
MIN_PDF_PAGE_TEXT_CHARS = 16


def extract_pdf(
    content: bytes | str | pathlib.Path, modality: str = "doc"
) -> list[DataChunk]:
    """Chunk a PDF into one image per page, plus a text chunk for every page
    that has a real embedded text layer.

    Born-digital PDFs become text-searchable (text-model embeddings + BM25) for
    free and exactly, while scanned pages — whose ``get_text()`` is empty — fall
    back to image-only multimodal chunks, preserving the previous behaviour.
    The text and image chunks for a page share its ``page`` metadata.
    """
    chunks: list[DataChunk] = []
    with as_file(content) as file_path:
        with pymupdf.open(file_path) as pdf:
            for page in pdf.pages():
                image_chunk = page_image_chunk(page, modality)
                chunks.append(image_chunk)
                try:
                    text = page.get_text().strip()
                except Exception:
                    logger.warning(
                        "get_text failed for page %s; ingesting image-only",
                        page.number,
                    )
                    text = ""
                if len(text) >= MIN_PDF_PAGE_TEXT_CHARS:
                    chunks.extend(
                        extract_text(
                            text,
                            metadata=image_chunk.metadata,
                            modality=modality,
                            skip_summary=True,
                        )
                    )
    return chunks


def docx_to_pdf(
    docx_path: pathlib.Path,
    output_path: pathlib.Path | None = None,
) -> pathlib.Path:
    """Convert DOCX to PDF using pypandoc"""
    if not HAS_PANDOC or pypandoc is None:
        raise RuntimeError("pandoc is not installed — cannot convert DOCX to PDF")
    if output_path is None:
        output_path = docx_path.with_suffix(".pdf")

    # Now that we have all packages installed, try xelatex first as it has better Unicode support
    try:
        logger.info(f"Converting {docx_path} to PDF using xelatex")
        extra_args = [
            "--pdf-engine=xelatex",
            "--variable=geometry:margin=1in",
        ]
        lua_filter = PANDOC_LUA_FILTER
        if lua_filter.exists():
            extra_args.append(f"--lua-filter={lua_filter}")
        else:
            logger.warning(
                "Pandoc lua filter not found at %s; converting without it",
                lua_filter,
            )
        pypandoc.convert_file(
            str(docx_path),
            format="docx",
            to="pdf",
            outputfile=str(output_path),
            extra_args=extra_args,
        )
        logger.info(f"Successfully converted {docx_path} to PDF")
        return output_path
    except Exception as e:
        logger.error(f"Error converting document to PDF: {e}")
        raise


def extract_docx_text(docx_path: pathlib.Path) -> list[DataChunk]:
    """Extract text from DOCX using pypandoc (no LaTeX needed)."""
    if not HAS_PANDOC or pypandoc is None:
        raise RuntimeError("pandoc is not installed — cannot extract DOCX text")
    text = pypandoc.convert_file(str(docx_path), format="docx", to="plain")
    if not text or not text.strip():
        return []
    return extract_text(text)


def extract_docx(docx_path: pathlib.Path | bytes | str) -> list[DataChunk]:
    """Extract content from DOCX by converting to PDF first, then processing.

    Falls back to plain text extraction if PDF conversion fails (e.g. LaTeX errors).
    """
    with as_file(docx_path) as file_path:
        try:
            pdf_path = docx_to_pdf(file_path)
            logger.info(f"Extracted PDF from {file_path}")
            return doc_to_images(pdf_path)
        except Exception as e:
            logger.warning(f"PDF conversion failed for {file_path}, falling back to text extraction: {e}")
            return extract_docx_text(file_path)


def safe_image_open(content: bytes | pathlib.Path) -> Image.Image:
    """Open an image, rejecting decompression bombs before decoding pixels.

    PIL fills in ``.size`` from the header at open time, so the pixel-count
    check happens before any raster is decoded into memory. An oversized image
    therefore raises here instead of being fully decoded — which at a worker's
    memory cap would SIGKILL the process and, with acks_late, redeliver the
    poison item forever. The raised error is catchable, so the embed path can
    record it as FAILED.
    """
    image = Image.open(io.BytesIO(content) if isinstance(content, bytes) else content)
    pixels = image.width * image.height
    if pixels > settings.MAX_IMAGE_PIXELS:
        image.close()
        raise Image.DecompressionBombError(
            f"Image has {pixels} pixels, over the {settings.MAX_IMAGE_PIXELS} limit"
        )
    return image


def extract_image(content: bytes | str | pathlib.Path) -> list[DataChunk]:
    if not isinstance(content, (bytes, pathlib.Path)):
        raise ValueError(f"Unsupported content type: {type(content)}")
    image = safe_image_open(content)
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
        chunks = extract_pdf(content)
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
