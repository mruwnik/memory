"""Tests for the report sync task."""

import uuid
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from memory.common import settings
from memory.common.db.models import Report
from memory.common.db.models.source_item import Chunk
from memory.workers.tasks import reports


def _make_mock_chunk(source_id: int) -> Chunk:
    return Chunk(
        id=str(uuid.uuid4()),
        content="test chunk content",
        embedding_model="test-model",
        vector=[0.1] * 1024,
        item_metadata={"source_id": source_id, "tags": ["test"]},
        collection_name="report",
    )


@pytest.fixture
def mock_make_session(db_session):
    @contextmanager
    def _mock_session():
        yield db_session

    with patch("memory.workers.tasks.reports.make_session", _mock_session):
        with patch(
            "memory.common.embedding.embed_source_item",
            side_effect=lambda item: [_make_mock_chunk(item.id or 1)],
        ):
            with patch("memory.common.content_processing.push_to_qdrant"):
                yield db_session


def test_sync_report_html_content(mock_make_session, tmp_path):
    """Test sync_report with inline HTML content (MCP path)."""
    with patch.object(settings, "REPORT_STORAGE_DIR", tmp_path):
        result = reports.sync_report(
            file_path=str(tmp_path / "test_report.html"),
            title="Test Report",
            content="<h1>Hello</h1><p>World</p>",
            report_format="html",
            tags=["test"],
        )

    mock_make_session.commit()

    report = mock_make_session.query(Report).filter_by(report_title="Test Report").first()
    assert report is not None
    assert report.report_format == "html"
    assert report.mime_type == "text/html"
    assert report.tags == ["test"]
    assert result["status"] == "processed"


def test_sync_report_file_upload(mock_make_session, tmp_path):
    """Test sync_report with a pre-existing file on disk (upload path)."""
    report_file = tmp_path / "uploaded.html"
    report_file.write_text("<h1>Uploaded</h1>")

    with patch.object(settings, "REPORT_STORAGE_DIR", tmp_path):
        result = reports.sync_report(
            file_path=str(report_file),
            title="Uploaded Report",
            report_format="html",
        )

    mock_make_session.commit()

    report = mock_make_session.query(Report).filter_by(report_title="Uploaded Report").first()
    assert report is not None
    assert report.filename == "uploaded.html"
    assert result["status"] == "processed"


def test_sync_report_pdf(mock_make_session, tmp_path):
    """Test sync_report with PDF format."""
    pdf_file = tmp_path / "report.pdf"
    pdf_file.write_bytes(b"fake pdf content")

    with patch.object(settings, "REPORT_STORAGE_DIR", tmp_path):
        reports.sync_report(
            file_path=str(pdf_file),
            title="PDF Report",
            report_format="pdf",
        )

    mock_make_session.commit()

    report = mock_make_session.query(Report).filter_by(report_title="PDF Report").first()
    assert report is not None
    assert report.report_format == "pdf"
    assert report.mime_type == "application/pdf"


def test_sync_report_dedup(mock_make_session, tmp_path):
    """Test that duplicate content returns already_exists."""
    html_content = "<h1>Same Content</h1>"

    with patch.object(settings, "REPORT_STORAGE_DIR", tmp_path):
        result1 = reports.sync_report(
            file_path=str(tmp_path / "first.html"),
            title="First",
            content=html_content,
            report_format="html",
        )
        mock_make_session.commit()

        result2 = reports.sync_report(
            file_path=str(tmp_path / "second.html"),
            title="Second",
            content=html_content,
            report_format="html",
        )

    assert result1["status"] == "processed"
    assert result2["status"] == "already_exists"


def test_sync_report_update_in_place(mock_make_session, tmp_path):
    """Test that a report with same filename gets updated."""
    with patch.object(settings, "REPORT_STORAGE_DIR", tmp_path):
        reports.sync_report(
            file_path=str(tmp_path / "update_me.html"),
            title="Version 1",
            content="<h1>V1</h1>",
            report_format="html",
        )
        mock_make_session.commit()

        reports.sync_report(
            file_path=str(tmp_path / "update_me.html"),
            title="Version 2",
            content="<h1>V2</h1>",
            report_format="html",
        )
        mock_make_session.commit()

    all_reports = mock_make_session.query(Report).filter_by(filename="update_me.html").all()
    assert len(all_reports) == 1
    assert all_reports[0].report_title == "Version 2"


def test_sync_report_sets_creator(mock_make_session, admin_user, tmp_path):
    """Test that creator_id is set when provided."""
    with patch.object(settings, "REPORT_STORAGE_DIR", tmp_path):
        reports.sync_report(
            file_path=str(tmp_path / "owned.html"),
            title="Owned Report",
            content="<p>content</p>",
            report_format="html",
            creator_id=admin_user.id,
        )
    mock_make_session.commit()

    report = mock_make_session.query(Report).filter_by(report_title="Owned Report").first()
    assert report is not None
    assert report.creator_id == admin_user.id


def test_sync_report_file_not_found(mock_make_session):
    """Test that missing file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        reports.sync_report(
            file_path="/nonexistent/path/report.html",
            title="Missing",
            report_format="html",
        )
