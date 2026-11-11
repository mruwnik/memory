"""Tests for the Report model."""
# pyright: reportAttributeAccessIssue=false
# PyMuPDF's stubs omit Document.new_page; it exists at runtime.

from unittest.mock import patch

import pymupdf
import pytest
from PIL import Image

from memory.common import settings
from memory.common.db.models import MiscDoc, Report


def test_report_as_payload(db_session):
    report = Report(
        modality="doc",
        mime_type="text/html",
        report_title="Test Report",
        report_format="html",
        filename="abc123_test.html",
        sha256=b"\x00" * 32,
    )
    db_session.add(report)
    db_session.flush()

    payload = report.as_payload()
    assert payload["report_title"] == "Test Report"
    assert payload["report_format"] == "html"


def test_report_title_property(db_session):
    report = Report(
        modality="doc",
        mime_type="text/html",
        report_title="My Report Title",
        report_format="html",
        sha256=b"\x00" * 32,
    )
    db_session.add(report)
    db_session.flush()

    assert report.title == "My Report Title"


def test_report_title_none(db_session):
    report = Report(
        modality="doc",
        mime_type="text/html",
        report_title=None,
        report_format="html",
        sha256=b"\x00" * 32,
    )
    db_session.add(report)
    db_session.flush()

    assert report.title is None


def test_report_get_collections():
    assert Report.get_collections() == ["report"]


def test_report_polymorphic_identity(db_session):
    report = Report(
        modality="doc",
        mime_type="text/html",
        report_title="Poly Test",
        report_format="html",
        sha256=b"\x00" * 32,
    )
    db_session.add(report)
    db_session.flush()

    assert report.type == "report"


def test_report_chunk_contents_no_filename():
    report = Report(
        modality="doc",
        mime_type="text/html",
        report_format="html",
    )
    assert report._chunk_contents() == []


def test_report_chunk_contents_missing_file(tmp_path):
    with patch("memory.common.settings.REPORT_STORAGE_DIR", tmp_path):
        report = Report(
            modality="doc",
            mime_type="text/html",
            report_format="html",
            filename="nonexistent.html",
        )
        assert report._chunk_contents() == []


def test_report_chunk_contents_html(tmp_path):
    # Worker converts HTML to markdown and stores in content field;
    # _chunk_contents just calls chunk_mixed(content, images)
    with (
        patch("memory.common.settings.FILE_STORAGE_DIR", tmp_path),
        patch("memory.common.summarizer.summarize", return_value=("summary", ["tag"])),
    ):
        report = Report(
            modality="doc",
            mime_type="text/html",
            report_format="html",
            filename="test_report.html",
            content="# Test\n\nHello world",
        )
        chunks = report._chunk_contents()
        assert len(chunks) > 0
        assert any(
            isinstance(item, str) and item.strip()
            for chunk in chunks
            for item in chunk.data
        )


def blank_pdf(pages: int) -> bytes:
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    return doc.tobytes()


def image_chunks(chunks) -> list:
    return [c for c in chunks if any(isinstance(d, Image.Image) for d in c.data)]


@pytest.mark.parametrize(
    "item",
    [
        Report(modality="doc", mime_type="application/pdf", report_format="pdf", filename="r.pdf"),
        MiscDoc(modality="doc", mime_type="application/pdf", filename="r.pdf"),
    ],
)
def test_pdf_page_images_are_capped(item):
    (settings.FILE_STORAGE_DIR / "r.pdf").write_bytes(blank_pdf(5))

    with patch.object(settings, "MAX_DOC_EMBED_PAGES", 2):
        chunks = item._chunk_contents()

    assert [c.metadata["page"] for c in image_chunks(chunks)] == [0, 1]


def test_report_save_to_file_text(tmp_path):
    with patch("memory.common.settings.REPORT_STORAGE_DIR", tmp_path):
        report = Report(
            modality="doc",
            mime_type="text/html",
            report_format="html",
            filename="saved.html",
        )
        report.save_to_file("<html><body>content</body></html>")
        assert (tmp_path / "saved.html").read_text() == "<html><body>content</body></html>"


def test_report_save_to_file_bytes(tmp_path):
    with patch("memory.common.settings.REPORT_STORAGE_DIR", tmp_path):
        report = Report(
            modality="doc",
            mime_type="application/pdf",
            report_format="pdf",
            filename="saved.pdf",
        )
        report.save_to_file(b"pdf bytes")
        assert (tmp_path / "saved.pdf").read_bytes() == b"pdf bytes"


def test_report_save_to_file_from_content(tmp_path):
    with patch("memory.common.settings.REPORT_STORAGE_DIR", tmp_path):
        report = Report(
            modality="doc",
            mime_type="text/html",
            report_format="html",
            filename="content.html",
            content="<p>from content field</p>",
        )
        report.save_to_file()
        assert (tmp_path / "content.html").read_text() == "<p>from content field</p>"


def test_report_save_to_file_no_filename():
    report = Report(
        modality="doc",
        mime_type="text/html",
        report_format="html",
        filename=None,
    )
    # Should not raise - just a no-op
    report.save_to_file("content")
