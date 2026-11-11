"""Run MuPDF on untrusted documents in a resource-limited child process.

MuPDF's cost is not bounded by the file it reads: a few KiB of valid PDF can
draw billions of objects, and MuPDF transparently inflates gzip. So every parse
or render of a PDF or book runs in ``mupdf_child.py``, a fresh interpreter (no
fork of the worker's database or broker connections) that sets an address
space, CPU and output size limit before opening the document, and that the
parent kills after ``MUPDF_TIMEOUT_SECONDS``. A breach raises ValueError,
which callers already treat as an unparseable document.

The child's results come back as framed JSON records with raw image samples,
never pickles, so a compromised child can't run code in the worker.
"""

import json
import pathlib
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Iterator

from PIL import Image

from memory.common import settings

CHILD_SCRIPT = pathlib.Path(__file__).with_name("mupdf_child.py")
HEADER_LENGTH = struct.Struct(">I")
Record = tuple[dict[str, Any], bytes]


@dataclass(frozen=True)
class PdfPage:
    number: int
    width: float
    height: float
    # None when MuPDF couldn't extract the page's text layer.
    text: str | None
    image: Image.Image | None


@dataclass
class BookContents:
    metadata: dict[str, Any]
    toc: list[tuple[int, str, int]]
    pages: list[str] = field(default_factory=list)


def child_command(request: dict[str, Any]) -> list[str]:
    return [sys.executable, "-I", str(CHILD_SCRIPT), json.dumps(request)]


def run_child(request: dict[str, Any], cwd: str) -> None:
    """Run the child to completion, raising ValueError if it ran out of time or
    was killed for breaching a limit."""
    timeout = settings.MUPDF_TIMEOUT_SECONDS
    process = subprocess.Popen(
        child_command(request),
        cwd=cwd,
        env={},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ValueError(f"Document took more than {timeout} s to parse") from None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    if returncode != 0:
        raise ValueError(
            f"Document parse breached its memory, CPU or output limit (exit status {returncode})"
        )


def read_exactly(file: BinaryIO, size: int) -> bytes:
    data = file.read(size)
    if len(data) != size:
        raise ValueError("Document parse ended without finishing its results")
    return data


def read_records(path: pathlib.Path) -> Iterator[Record]:
    """The child's records up to its terminating one, which is not yielded:
    an error record raises ValueError, and so does output that stops short."""
    if path.stat().st_size > settings.MUPDF_OUTPUT_BYTES:
        raise ValueError("Document parse returned more than its output limit")
    with path.open("rb") as file:
        while True:
            (length,) = HEADER_LENGTH.unpack(read_exactly(file, HEADER_LENGTH.size))
            header = json.loads(read_exactly(file, length))
            blob = read_exactly(file, header["blob"])
            if header["kind"] == "done":
                return
            if header["kind"] == "error":
                raise ValueError(error_message(header))
            yield header, blob


def error_message(header: dict[str, Any]) -> str:
    reason = header["reason"]
    if reason == "not_pdf":
        return "Not a PDF"
    if reason == "too_many_pages":
        return f"PDF has {header['pages']} pages (limit {settings.MAX_PDF_PAGES})"
    return f"MuPDF could not read the document ({header.get('type', reason)})"


def run_job(job: str, path: pathlib.Path, **params: Any) -> Iterator[Record]:
    """Run ``job`` on the file at ``path`` in the child and yield its records."""
    with tempfile.TemporaryDirectory() as tmp:
        output = pathlib.Path(tmp) / "results"
        request = {
            "job": job,
            "input": str(path.resolve()),
            "output": str(output),
            "memory_bytes": settings.MUPDF_MEMORY_BYTES,
            "cpu_seconds": settings.MUPDF_CPU_SECONDS,
            "output_bytes": settings.MUPDF_OUTPUT_BYTES,
            **params,
        }
        run_child(request, tmp)
        yield from read_records(output)


def pdf_page(header: dict[str, Any], blob: bytes) -> PdfPage:
    image = None
    if header["image"]:
        width, height = header["image"]
        image = Image.frombytes("RGB", (width, height), blob)
    return PdfPage(
        number=header["number"],
        width=header["width"],
        height=header["height"],
        text=header["text"],
        image=image,
    )


def pdf_pages(
    path: pathlib.Path, image_pages: int | None, max_image_pixels: int
) -> list[PdfPage]:
    """Every page of the PDF at ``path`` with its text, rendered to at most
    ``max_image_pixels`` for the first ``image_pages`` pages (all when None).

    Raises ValueError for a file MuPDF doesn't read as a PDF, one of more than
    MAX_PDF_PAGES pages, or one whose parse breaches the child's limits.
    """
    records = run_job(
        "pdf_pages",
        path,
        max_pages=settings.MAX_PDF_PAGES,
        image_pages=image_pages,
        max_image_pixels=max_image_pixels,
    )
    return [pdf_page(header, blob) for header, blob in records]


def book_contents(path: pathlib.Path) -> BookContents:
    """The metadata, table of contents and text of every page of the book at
    ``path``, in whatever format MuPDF recognises it as.

    Raises ValueError for a book MuPDF can't read or whose parse breaches the
    child's limits.
    """
    records = run_job("ebook", path)
    first = next(records, None)
    if first is None or first[0]["kind"] != "book":
        raise ValueError("Document parse returned no book")
    header, _ = first
    book = BookContents(
        metadata=header["metadata"],
        toc=[(level, title, page) for level, title, page in header["toc"]],
    )
    book.pages = [text["text"] for text, _ in records]
    return book
