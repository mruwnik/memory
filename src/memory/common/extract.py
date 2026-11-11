from dataclasses import dataclass, field, replace
import io
import zipfile
import logging
import math
import mimetypes
import os
import pathlib
import tempfile
from contextlib import contextmanager
from typing import Any, Generator, Sequence, cast

from memory.common import chunker, settings, summarizer
from memory.common.file_magic import PDF_MAGIC_WINDOW, looks_like_pdf
from memory.parsers import ebook
import pymupdf  # PyMuPDF
from PIL import Image
from memory.common.images import open_image
from memory.common.pandoc import PDF_ENGINE_ARGS, UNTRUSTED_INPUT_ARGS, run_pandoc
from memory.common.zip_limits import check_office_zip

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

    PANDOC_PATH: str | None = pypandoc.get_pandoc_path()
except (ImportError, OSError):
    PANDOC_PATH = None
HAS_PANDOC = PANDOC_PATH is not None

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


def summary_chunk(
    summary: str, tags: list[str], metadata: dict[str, Any], modality: str | None
) -> DataChunk:
    """The chunk holding an item's LLM summary, marked by its "summary" type."""
    return DataChunk(
        data=[summary],
        metadata=merge_metadata(metadata, {"type": "summary", "tags": tags}),
        modality=modality,
    )


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


# voyage-multimodal-3 downsamples every image to at most this many pixels
# (multimodal_image_pixels_max in its client_config.json), so more is wasted.
MAX_CHUNK_IMAGE_PIXELS = 2_000_000


def page_zoom(width: float, height: float) -> float:
    """The largest zoom, at most 1, whose render fits MAX_CHUNK_IMAGE_PIXELS.

    PyMuPDF rounds each side up to a whole pixel, so this solves
    (width*zoom + 1) * (height*zoom + 1) = MAX_CHUNK_IMAGE_PIXELS: a render
    that overshot by a pixel would be resampled again for embedding.
    """
    area, perimeter = max(1.0, width * height), width + height
    discriminant = perimeter**2 + 4 * area * (MAX_CHUNK_IMAGE_PIXELS - 1)
    return min(1.0, (math.sqrt(discriminant) - perimeter) / (2 * area))


def page_to_image(page: pymupdf.Page) -> Image.Image:
    # Render oversized pages straight at the size they'd be shrunk to: a page's
    # raster is unbounded (200x200in at 72dpi is 622MB) and no pixel guard sees it.
    zoom = page_zoom(page.rect.width, page.rect.height)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))  # type: ignore
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def page_metadata(page: pymupdf.Page) -> dict[str, Any]:
    return {"page": page.number, "width": page.rect.width, "height": page.rect.height}


def page_image_chunk(page: pymupdf.Page, modality: str = "doc") -> DataChunk:
    """One multimodal image chunk for a PDF page, carrying its page geometry as
    metadata."""
    return DataChunk(
        data=[page_to_image(page)],
        metadata=page_metadata(page),
        mime_type="image/jpeg",
        modality=modality,
    )


def page_text_chunks(page: pymupdf.Page, modality: str) -> list[DataChunk]:
    try:
        text = cast(str, page.get_text()).strip()
    except Exception:
        logger.warning("get_text failed for page %s; ingesting image-only", page.number)
        return []
    if len(text) < MIN_PDF_PAGE_TEXT_CHARS:
        return []
    return extract_text(
        text, metadata=page_metadata(page), modality=modality, skip_summary=True
    )


# A PDF page whose embedded text layer strips to fewer than this many characters
# is treated as image-only (a scan, or a near-blank/figure page): below this a
# get_text() chunk would just be noise (page numbers, stray ligatures, "").
MIN_PDF_PAGE_TEXT_CHARS = 16


def open_pdf(content: bytes | pathlib.Path) -> pymupdf.Document:
    """Open a PDF with MuPDF, refusing anything that isn't one.

    MuPDF picks its handler from the content, whatever the caller asks for, and
    opening a reflowable format (EPUB, MOBI, FB2, HTML) lays out the whole
    document. Only content that starts with the PDF header and carries no
    other format's signature, nor a zip directory, reaches MuPDF.
    """
    if isinstance(content, pathlib.Path):
        with content.open("rb") as file:
            head = file.read(PDF_MAGIC_WINDOW)
        zipped = zipfile.is_zipfile(content)
    else:
        head = content[:PDF_MAGIC_WINDOW]
        zipped = zipfile.is_zipfile(io.BytesIO(content))
    if not looks_like_pdf(head) or zipped:
        raise ValueError("Not a PDF")
    if isinstance(content, pathlib.Path):
        doc = pymupdf.open(content, filetype="pdf")
    else:
        doc = pymupdf.open(stream=content, filetype="pdf")
    if not doc.is_pdf:
        doc.close()
        raise ValueError("Not a PDF")
    return doc


def extract_pdf(
    content: bytes | str | pathlib.Path,
    modality: str = "doc",
    max_image_pages: int | None = None,
) -> list[DataChunk]:
    """Chunk a PDF into one image per page, plus a text chunk for every page
    that has a real embedded text layer.

    Born-digital PDFs become text-searchable (text-model embeddings + BM25) for
    free and exactly, while scanned pages — whose ``get_text()`` is empty — fall
    back to image-only multimodal chunks. The text and image chunks for a page
    share its ``page`` metadata. Only the first ``max_image_pages`` pages get an
    image, since every page image is held in memory until the item is embedded.

    Raises ValueError for content that isn't a PDF, or a PDF of more than
    MAX_PDF_PAGES pages.
    """
    chunks: list[DataChunk] = []
    with as_file(content) as file_path:
        with open_pdf(file_path) as pdf:
            if pdf.page_count > settings.MAX_PDF_PAGES:
                raise ValueError(
                    f"PDF has {pdf.page_count} pages (limit {settings.MAX_PDF_PAGES})"
                )
            for page in pdf.pages():
                if max_image_pages is None or page.number < max_image_pages:
                    chunks.append(page_image_chunk(page, modality))
                chunks.extend(page_text_chunks(page, modality))
    return chunks


def docx_to_pdf(
    docx_path: pathlib.Path,
    output_path: pathlib.Path,
) -> pathlib.Path:
    """Convert DOCX to PDF with pandoc and xelatex."""
    if PANDOC_PATH is None:
        raise RuntimeError("pandoc is not installed — cannot convert DOCX to PDF")

    logger.info(f"Converting {docx_path} to PDF using xelatex")
    args = [
        "--from=docx",
        f"--output={output_path}",
        *UNTRUSTED_INPUT_ARGS,
        *PDF_ENGINE_ARGS,
        "--variable=geometry:margin=1in",
    ]
    lua_filter = PANDOC_LUA_FILTER
    if lua_filter.exists():
        args.append(f"--lua-filter={lua_filter}")
    else:
        logger.warning(
            "Pandoc lua filter not found at %s; converting without it",
            lua_filter,
        )
    try:
        run_pandoc(PANDOC_PATH, [*args, "--", str(docx_path)])
    except Exception:
        logger.exception("Error converting document to PDF")
        raise
    logger.info(f"Successfully converted {docx_path} to PDF")
    return output_path


def extract_docx_text(docx_path: pathlib.Path) -> list[DataChunk]:
    """Extract text from DOCX with pandoc (no LaTeX needed)."""
    if PANDOC_PATH is None:
        raise RuntimeError("pandoc is not installed — cannot extract DOCX text")
    args = ["--from=docx", "--to=plain", *UNTRUSTED_INPUT_ARGS, "--", str(docx_path)]
    text = run_pandoc(PANDOC_PATH, args).decode("utf-8", errors="replace")
    if not text.strip():
        return []
    return extract_text(text)


def extract_docx(
    docx_path: pathlib.Path | bytes | str, max_image_pages: int | None = None
) -> list[DataChunk]:
    """Extract content from DOCX by converting to PDF first, then processing.

    Falls back to plain text extraction if PDF conversion fails (e.g. LaTeX errors).
    Raises ValueError for a document over the office file size limits
    (see zip_limits.check_office_zip), before pandoc sees it.
    """
    with as_file(docx_path) as file_path:
        with file_path.open("rb") as file:
            check_office_zip(file)
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                pdf_path = docx_to_pdf(file_path, pathlib.Path(tmp_dir) / "out.pdf")
                logger.debug(f"Extracted PDF from {file_path}")
                return extract_pdf(pdf_path, max_image_pages=max_image_pages)
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
    image = open_image(io.BytesIO(content) if isinstance(content, bytes) else content)
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
        content = content.read_text(encoding="utf-8", errors="replace")
    if isinstance(content, bytes):
        content = content.decode("utf-8")

    content = cast(str, content)
    chunks = [
        DataChunk(data=[c], modality=modality, metadata=metadata)
        for c in chunker.chunk_text(content, chunk_size or chunker.DEFAULT_CHUNK_TOKENS)
    ]
    if skip_summary or not summarizer.needs_summary(content):
        return chunks

    if result := summarizer.summarize_or_defer(content, metadata, modality):
        summary, tags = result
        if not summary:
            return [replace(c, metadata=merge_metadata(c.metadata, {"tags": tags})) for c in chunks]
        chunks.append(summary_chunk(summary, tags, metadata, modality))
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
    max_image_pages: int | None = None,
) -> list[DataChunk]:
    chunks = []
    logger.debug(f"Extracting content from {mime_type}")
    if mime_type == "application/pdf":
        chunks = extract_pdf(content, max_image_pages=max_image_pages)
    elif mime_type in [
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    ]:
        chunks = extract_docx(content, max_image_pages=max_image_pages)
    elif mime_type.startswith("text/"):
        chunks = extract_text(content, chunk_size, skip_summary=skip_summary)
    elif mime_type.startswith("image/"):
        chunks = extract_image(content)
    elif mime_type == "application/epub+zip":
        chunks = extract_ebook(cast(pathlib.Path, content))
    return chunks
