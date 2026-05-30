"""Content-based routing for the add_content tool.

The destination bucket is decided by inspecting the actual bytes, not by
trusting the caller's declared MIME type — a file mislabelled as an ebook (or
a PDF mislabelled as epub) would otherwise be ingested as the wrong kind of
item. Routing therefore happens once the bytes are in hand (in
``land_and_dispatch``); the upload-URL mint path, which has no bytes yet, just
uses ``max_ingest_bytes()`` as the read cap and routes when the bytes arrive.

Rules, in order:
  1. image (PIL can decode it, or declared image/*)      -> Photo
  2. PDF (``%PDF`` magic / fitz reports a PDF format)     -> MiscDoc
  3. fitz opens it as a paginated document (epub/mobi/…)  -> Book
  4. anything else                                        -> MiscDoc
"""

import io
import pathlib
from dataclasses import dataclass

import fitz  # PyMuPDF — same parser the ebook task uses
from PIL import Image

from memory.common import settings
from memory.common.celery_app import SYNC_BOOK, SYNC_MISC_DOC, SYNC_PHOTO


@dataclass(frozen=True)
class BucketSpec:
    name: str  # "book" | "image" | "misc"
    task_name: str
    storage_dir: pathlib.Path
    max_bytes: int
    dedupe_field: str  # "file_path" | "sha256"


def book_spec() -> BucketSpec:
    return BucketSpec(
        "book", SYNC_BOOK, settings.EBOOK_STORAGE_DIR, settings.MAX_BOOK_UPLOAD_BYTES, "file_path"
    )


def image_spec() -> BucketSpec:
    return BucketSpec(
        "image", SYNC_PHOTO, settings.PHOTO_STORAGE_DIR, settings.MAX_PHOTO_UPLOAD_BYTES, "sha256"
    )


def misc_spec() -> BucketSpec:
    return BucketSpec(
        "misc", SYNC_MISC_DOC, settings.MISC_STORAGE_DIR, settings.MAX_MISC_UPLOAD_BYTES, "sha256"
    )


def normalize_mime(mime: str) -> str:
    """Lowercase and strip any ``; charset=...`` parameter."""
    return (mime or "").split(";", 1)[0].strip().lower()


def is_image_bytes(content: bytes) -> bool:
    """True if PIL can decode the bytes as an image."""
    try:
        with Image.open(io.BytesIO(content)) as im:
            im.verify()
        return True
    except Exception:
        return False


def is_ebook_bytes(content: bytes) -> bool:
    """True if fitz opens the bytes as a non-PDF paginated document.

    PDFs open fine but are routed to MiscDoc, so they're excluded by both the
    magic prefix and the reported format. (fitz also opens SVG/XPS/CBZ, which
    are niche false-positives here; the common text/office/archive types fail
    to open from a stream and correctly fall through to MiscDoc.)
    """
    if content[:5].startswith(b"%PDF"):
        return False
    try:
        doc = fitz.open(stream=content)
    except Exception:
        return False
    try:
        fmt = (doc.metadata or {}).get("format") or ""
        pages = doc.page_count
    finally:
        doc.close()
    if fmt.upper().startswith("PDF"):
        return False
    return pages > 0


def detect_bucket(declared_type: str, content: bytes) -> BucketSpec:
    """Pick the ingestion bucket by inspecting ``content``.

    ``declared_type`` only fast-paths the image case (and is later stored as
    the MiscDoc mime); it never forces book/misc routing.
    """
    if normalize_mime(declared_type).startswith("image/") or is_image_bytes(content):
        return image_spec()
    if is_ebook_bytes(content):
        return book_spec()
    return misc_spec()


def max_ingest_bytes() -> int:
    """Upper bound for reading bytes before the bucket (and its specific cap)
    is known — the largest of the per-bucket caps."""
    return max(
        settings.MAX_BOOK_UPLOAD_BYTES,
        settings.MAX_PHOTO_UPLOAD_BYTES,
        settings.MAX_MISC_UPLOAD_BYTES,
    )
