# pyright: reportAttributeAccessIssue=false
# PyMuPDF's stubs omit Page.get_text, Page.get_pixmap and Document.get_toc.
"""Run one MuPDF job on an untrusted document inside resource limits.

``memory.common.mupdf_sandbox`` starts this file as
``python -I mupdf_child.py <request json>`` and reads the framed records it
writes to the request's output file. It imports nothing from ``memory``, so it
starts quickly in an isolated interpreter with no database or broker
connections. Its limits (address space, CPU seconds, output file size) are set
before the document is opened; breaching one ends this process, not the worker.

Each record is a 4-byte big-endian length, that many bytes of JSON header, then
``header["blob"]`` bytes of raw data (a page image's RGB samples). The last
record is ``{"kind": "done"}`` or ``{"kind": "error", ...}``.
"""

import json
import math
import resource
import struct
import sys
from typing import Any, BinaryIO, cast

import pymupdf

HEADER_LENGTH = struct.Struct(">I")


class Refused(Exception):
    """The document is readable but not acceptable; ``details`` go to the parent."""

    def __init__(self, reason: str, **details: Any):
        super().__init__(reason)
        self.details = {"reason": reason, **details}


def limit(kind: int, value: int) -> None:
    """Lower both the soft and hard ``kind`` limits to ``value``, never above the
    hard limit this process already has."""
    _, hard = resource.getrlimit(kind)
    if hard != resource.RLIM_INFINITY:
        value = min(value, hard)
    resource.setrlimit(kind, (value, value))


def set_limits(request: dict[str, Any]) -> None:
    # macOS neither enforces an address space limit nor accepts one below what
    # the interpreter has already reserved, so there the timeout and CPU limit
    # are the only bounds on memory.
    if sys.platform == "linux":
        limit(resource.RLIMIT_AS, request["memory_bytes"])
    limit(resource.RLIMIT_CPU, request["cpu_seconds"])
    limit(resource.RLIMIT_FSIZE, request["output_bytes"])
    limit(resource.RLIMIT_CORE, 0)


def write_record(out: BinaryIO, header: dict[str, Any], blob: bytes = b"") -> None:
    data = json.dumps({**header, "blob": len(blob)}).encode()
    out.write(HEADER_LENGTH.pack(len(data)))
    out.write(data)
    out.write(blob)


def page_zoom(width: float, height: float, max_pixels: int) -> float:
    """The largest zoom, at most 1, whose render fits ``max_pixels``.

    PyMuPDF rounds each side up to a whole pixel, so this solves
    (width*zoom + 1) * (height*zoom + 1) = max_pixels: a render that overshot
    by a pixel would be resampled again for embedding.
    """
    area, perimeter = max(1.0, width * height), width + height
    discriminant = perimeter**2 + 4 * area * (max_pixels - 1)
    return min(1.0, (math.sqrt(discriminant) - perimeter) / (2 * area))


def render_page(page: pymupdf.Page, max_pixels: int) -> tuple[list[int], bytes]:
    """The page's RGB render, at most ``max_pixels``, as its size and samples.

    A page's raster is otherwise unbounded (200x200in at 72dpi is 622MB), so
    oversized pages render straight at the size they'd be shrunk to.
    """
    zoom = page_zoom(page.rect.width, page.rect.height, max_pixels)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
    return [pix.width, pix.height], bytes(pix.samples)


def page_text(page: pymupdf.Page) -> str | None:
    """The page's text layer, or None when MuPDF can't extract it (the page is
    then ingested as an image only)."""
    try:
        return cast(str, page.get_text())
    except Exception:  # MuPDF raises several error types for a damaged page
        return None


def pdf_pages(request: dict[str, Any], out: BinaryIO) -> None:
    """One record per page: its geometry, its text, and its render for the
    first ``image_pages`` pages (every page when that is None)."""
    with pymupdf.open(request["input"], filetype="pdf") as doc:
        if not doc.is_pdf:
            raise Refused("not_pdf")
        if doc.page_count > request["max_pages"]:
            raise Refused("too_many_pages", pages=doc.page_count)
        image_pages = request["image_pages"]
        for page in doc.pages():
            header: dict[str, Any] = {
                "kind": "page",
                "number": page.number,
                "width": page.rect.width,
                "height": page.rect.height,
                "text": page_text(page),
                "image": None,
            }
            blob = b""
            if image_pages is None or cast(int, page.number) < image_pages:
                header["image"], blob = render_page(page, request["max_image_pixels"])
            write_record(out, header, blob)


def ebook(request: dict[str, Any], out: BinaryIO) -> None:
    """A record with the book's metadata, table of contents and page count,
    then one record with each page's text."""
    with pymupdf.open(request["input"]) as doc:
        write_record(
            out,
            {
                "kind": "book",
                "metadata": doc.metadata or {},
                "toc": doc.get_toc(),
                "page_count": doc.page_count,
            },
        )
        for number in range(doc.page_count):
            write_record(out, {"kind": "text", "text": doc[number].get_text()})


JOBS = {"pdf_pages": pdf_pages, "ebook": ebook}


def run(request: dict[str, Any], out: BinaryIO) -> None:
    try:
        JOBS[request["job"]](request, out)
    except Refused as e:
        write_record(out, {"kind": "error", **e.details})
        return
    except Exception as e:  # MuPDF raises many error types for an unreadable document
        write_record(out, {"kind": "error", "reason": "unreadable", "type": type(e).__name__})
        return
    write_record(out, {"kind": "done"})


def main(argv: list[str]) -> None:
    request = json.loads(argv[1])
    set_limits(request)
    with open(request["output"], "wb") as out:
        run(request, out)


if __name__ == "__main__":
    main(sys.argv)
