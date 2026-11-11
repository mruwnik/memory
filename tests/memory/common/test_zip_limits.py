import io

import pytest

from memory.common import settings
from memory.common.zip_limits import check_office_zip
from tests.data.contents import DATA_DIR
from tests.memory.office_files import (
    bomb_docx,
    docx_bytes,
    paragraphs_xml,
    set_local_header_field,
    understate_sizes,
    with_trailing_deflate_data,
)

MIB = 1024 * 1024


@pytest.fixture
def small_limits(monkeypatch):
    monkeypatch.setattr(settings, "MAX_OFFICE_MARKUP_BYTES", MIB)
    monkeypatch.setattr(settings, "MAX_OFFICE_INFLATED_BYTES", 2 * MIB)
    monkeypatch.setattr(settings, "MAX_OFFICE_ZIP_ENTRIES", 5)


@pytest.mark.parametrize(
    "data",
    [
        docx_bytes(paragraphs_xml("hello")),
        docx_bytes(paragraphs_xml("hello"), **{"word/media/a.png": b"\0" * (MIB + 1)}),
    ],
    ids=["text", "media-over-markup-limit"],
)
def test_documents_within_limits_pass(small_limits, data):
    check_office_zip(io.BytesIO(data))


def test_sample_docx_passes():
    with (DATA_DIR / "sample.docx").open("rb") as file:
        check_office_zip(file)


@pytest.mark.parametrize(
    "data",
    [
        bomb_docx(MIB + 1024),
        docx_bytes(paragraphs_xml("x"), **{"word/styles.xml": b" " * MIB}),
        docx_bytes(paragraphs_xml("x"), **{"word/media/a.png": b"\0" * (2 * MIB)}),
        docx_bytes(paragraphs_xml("x"), **{f"e{n}": b"" for n in range(5)}),
        understate_sizes(bomb_docx(MIB + 1024), declared=100),
        understate_sizes(docx_bytes(b"<w:document/>" * 10), declared=10),
    ],
    ids=["markup", "markup-total", "inflated-total", "entries", "lying-bomb", "lying-small"],
)
def test_documents_over_limits_are_refused(small_limits, data):
    with pytest.raises(ValueError):
        check_office_zip(io.BytesIO(data))


DOC = docx_bytes(paragraphs_xml("hello"))


@pytest.mark.parametrize(
    "data",
    [
        b"\xd0\xcf\x11\xe0 a legacy .doc is not a zip",
        b"",
        b"MZ" + bytes(200) + DOC,
        DOC + b"trailing bytes",
        DOC + DOC,
        set_local_header_field(DOC, 8, "<H", 0),
        set_local_header_field(DOC, 18, "<L", 1),
        with_trailing_deflate_data(paragraphs_xml("x"), b"hidden"),
    ],
    ids=[
        "not-zip",
        "empty",
        "prepended",
        "appended",
        "two-archives",
        "local-method",
        "local-compressed-size",
        "data-after-deflate-stream",
    ],
)
def test_documents_readers_could_disagree_on_are_refused(data):
    with pytest.raises(ValueError):
        check_office_zip(io.BytesIO(data))


def test_complete_deflate_stream_passes():
    check_office_zip(io.BytesIO(with_trailing_deflate_data(paragraphs_xml("x"), b"")))
