import gzip
import pathlib
import resource
import subprocess
import sys
import time
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from memory.common import mupdf_child, mupdf_sandbox, settings
from memory.common.extract import extract_pdf
from memory.parsers.ebook import check_parse_size, parse_ebook
from memory.parsers.google_drive import GoogleCredentials, GoogleDriveClient
from tests.memory import book_files

REGULAMIN = pathlib.Path(__file__).parent.parent.parent / "data" / "regulamin.pdf"
# About 160 s of MuPDF CPU and more memory than the default cap, from ~2 KiB.
ENDLESS_PDF = book_files.nested_form_pdf(8)
# About 16 s and 1.1 GB, from ~2 KiB.
HUNGRY_PDF = book_files.nested_form_pdf(7)
# 8 MiB of markup in ~12 KiB; MuPDF inflates it and lays out ~48,000 pages.
GZIP_BOOK = gzip.compress(book_files.markup(1024 * 1024), 9)
# 10**8 SVG <use> draws in ~2 KiB, under every size cap.
SVG_DEFS = ['<g id="l0"><text x="1" y="1">a</text></g>'] + [
    f'<g id="l{k}">' + f'<use xlink:href="#l{k - 1}"/>' * 10 + "</g>" for k in range(1, 9)
]
ENDLESS_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
    ' width="10" height="10"><defs>' + "".join(SVG_DEFS) + '</defs><use xlink:href="#l8"/></svg>'
).encode()
# Wall-clock bound on handling one of these, with room for a loaded machine:
# unbounded, each runs for minutes.
REFUSED_WITHIN_SECONDS = 60
# Growth of this process's peak RSS handling one may cause; the documents above
# cost hundreds of MiB to GBs when parsed in-process.
PARENT_GROWTH_MIB = 100
# The sandbox's memory cap is applied on Linux only (see mupdf_child.set_limits).
LINUX_ONLY = pytest.mark.skipif(sys.platform != "linux", reason="memory limits are Linux-only")


def peak_rss_mib() -> int:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024


@contextmanager
def bounded_cost():
    before, start = peak_rss_mib(), time.monotonic()
    yield
    assert time.monotonic() - start < REFUSED_WITHIN_SECONDS
    assert peak_rss_mib() - before < PARENT_GROWTH_MIB


@pytest.mark.parametrize(
    "data, limits",
    [
        (ENDLESS_PDF, {"MUPDF_TIMEOUT_SECONDS": 2}),
        (ENDLESS_PDF, {"MUPDF_CPU_SECONDS": 1}),
        pytest.param(HUNGRY_PDF, {"MUPDF_MEMORY_BYTES": 256 * 1024 * 1024}, marks=LINUX_ONLY),
    ],
    ids=["wall clock", "cpu", "memory"],
)
def test_extract_pdf_refuses_a_pdf_whose_parse_breaches_a_limit(data, limits):
    with patch.multiple(settings, **limits), bounded_cost(), pytest.raises(ValueError):
        extract_pdf(data)


def test_drive_pdf_text_skips_a_pdf_whose_parse_breaches_a_limit():
    client = GoogleDriveClient(GoogleCredentials("token", None, None, []))
    with patch.object(settings, "MUPDF_TIMEOUT_SECONDS", 2), bounded_cost():
        assert client._extract_pdf_text(ENDLESS_PDF) is None


def skip_size_check(_path) -> None:
    pass


@pytest.mark.parametrize(
    "name, data, size_check",
    [
        ("use.epub", ENDLESS_SVG, check_parse_size),
        ("book.epub", GZIP_BOOK, skip_size_check),
    ],
    ids=["svg under the size cap", "gzip past the size check"],
)
def test_parse_ebook_refuses_a_book_whose_parse_breaches_a_limit(tmp_path, name, data, size_check):
    path = tmp_path / name
    path.write_bytes(data)
    with (
        patch.object(settings, "MUPDF_TIMEOUT_SECONDS", 2),
        patch("memory.parsers.ebook.check_parse_size", size_check),
        bounded_cost(),
        pytest.raises(ValueError),
    ):
        parse_ebook(path)


REAL_POPEN = subprocess.Popen


def test_a_timed_out_child_is_killed():
    children: list[subprocess.Popen] = []

    def spawn(*args, **kwargs):
        children.append(REAL_POPEN(*args, **kwargs))
        return children[-1]

    with (
        patch.object(settings, "MUPDF_TIMEOUT_SECONDS", 1),
        patch("memory.common.mupdf_sandbox.subprocess.Popen", side_effect=spawn),
        pytest.raises(ValueError, match="took more than 1 s"),
    ):
        extract_pdf(ENDLESS_PDF)
    assert [child.returncode for child in children] == [-9]


def test_results_over_the_output_limit_are_refused():
    with patch.object(settings, "MUPDF_OUTPUT_BYTES", 10_000), pytest.raises(ValueError):
        extract_pdf(REGULAMIN)


def test_the_child_starts_isolated_with_an_empty_environment():
    with patch("memory.common.mupdf_sandbox.subprocess.Popen", wraps=REAL_POPEN) as popen:
        extract_pdf(REGULAMIN)
    [command], kwargs = popen.call_args
    assert command[:3] == [mupdf_sandbox.sys.executable, "-I", str(mupdf_sandbox.CHILD_SCRIPT)]
    assert kwargs["env"] == {}


def write_records(path, *records: tuple[dict, bytes]) -> None:
    with path.open("wb") as out:
        for header, blob in records:
            mupdf_child.write_record(out, header, blob)


PAGE = {"kind": "page", "number": 0, "width": 1, "height": 1, "text": "x", "image": None}


@pytest.mark.parametrize(
    "records, match",
    [
        ([(PAGE, b"")], "without finishing"),
        ([(PAGE, b""), ({"kind": "error", "reason": "unreadable", "type": "FzErrorFormat"}, b"")], "FzErrorFormat"),
        ([({"kind": "error", "reason": "not_pdf"}, b"")], "Not a PDF"),
        ([({"kind": "error", "reason": "too_many_pages", "pages": 9}, b"")], "9 pages"),
    ],
    ids=["no terminating record", "unreadable", "not a pdf", "too many pages"],
)
def test_read_records_refuses_unfinished_or_failed_results(tmp_path, records, match):
    path = tmp_path / "results"
    write_records(path, *records)

    with pytest.raises(ValueError, match=match):
        list(mupdf_sandbox.read_records(path))


def test_read_records_refuses_a_truncated_blob(tmp_path):
    path = tmp_path / "results"
    write_records(path, (PAGE, b"abcd"))
    path.write_bytes(path.read_bytes()[:-2])

    with pytest.raises(ValueError, match="without finishing"):
        list(mupdf_sandbox.read_records(path))


def test_read_records_yields_records_up_to_done(tmp_path):
    path = tmp_path / "results"
    write_records(path, (PAGE, b"abcd"), ({"kind": "done"}, b""))

    assert list(mupdf_sandbox.read_records(path)) == [({**PAGE, "blob": 4}, b"abcd")]
