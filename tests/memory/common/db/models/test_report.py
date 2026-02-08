"""Tests for the Report model."""

import pytest
from unittest.mock import patch, MagicMock

from memory.common.db.models import Report


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


def test_report_chunk_contents_pdf(tmp_path):
    report_file = tmp_path / "test_report.pdf"
    report_file.write_bytes(b"fake pdf content")

    mock_chunks = [MagicMock(data=["page 1 text"])]

    with (
        patch("memory.common.settings.REPORT_STORAGE_DIR", tmp_path),
        patch("memory.common.extract.doc_to_images", return_value=mock_chunks) as mock_extract,
    ):
        report = Report(
            modality="doc",
            mime_type="application/pdf",
            report_format="pdf",
            filename="test_report.pdf",
        )
        chunks = report._chunk_contents()
        mock_extract.assert_called_once_with(report_file)
        assert chunks == mock_chunks


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
