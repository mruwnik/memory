"""Tests for report serving endpoint and upload endpoint."""

from io import BytesIO
from unittest.mock import patch

from fastapi.testclient import TestClient

from memory.common import settings
from memory.common.db.models import Report


# === Serving endpoint tests ===


def test_serve_html_report_has_csp_headers(client: TestClient, user, db_session):
    """HTML reports must include CSP sandbox headers to prevent XSS."""
    html_file = settings.REPORT_STORAGE_DIR / "test.html"
    html_file.write_text("<h1>Test</h1>")

    report = Report(
        modality="doc",
        mime_type="text/html",
        report_title="Test",
        report_format="html",
        filename="reports/test.html",
        sha256=b"\x00" * 32,
    )
    db_session.add(report)
    db_session.commit()

    response = client.get("/reports/test.html")

    assert response.status_code == 200, response.text
    assert "sandbox" in response.headers.get("content-security-policy", "")
    assert response.headers.get("x-content-type-options") == "nosniff"


def test_serve_pdf_report_no_csp_headers(client: TestClient, user, db_session):
    """PDF reports should not include CSP headers."""
    pdf_file = settings.REPORT_STORAGE_DIR / "test.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 fake pdf")

    report = Report(
        modality="doc",
        mime_type="application/pdf",
        report_title="PDF",
        report_format="pdf",
        filename="reports/test.pdf",
        sha256=b"\x01" * 32,
    )
    db_session.add(report)
    db_session.commit()

    response = client.get("/reports/test.pdf")

    assert response.status_code == 200
    assert "content-security-policy" not in response.headers


def test_serve_report_404_not_found(client: TestClient, user):
    """Nonexistent report file returns 404."""
    response = client.get("/reports/nonexistent.html")

    assert response.status_code == 404


def test_serve_report_access_denied(client: TestClient, db_session):
    """Report with restricted access returns 403 for unauthorized user."""
    html_file = settings.REPORT_STORAGE_DIR / "restricted.html"
    html_file.write_text("<h1>Secret</h1>")

    report = Report(
        modality="doc",
        mime_type="text/html",
        report_title="Restricted",
        report_format="html",
        filename="reports/restricted.html",
        sha256=b"\x02" * 32,
        sensitivity="confidential",
    )
    db_session.add(report)
    db_session.commit()

    with (
        patch("memory.api.app.has_admin_scope", return_value=False),
        patch("memory.api.app.user_can_access", return_value=False),
        patch("memory.api.app.get_user_project_roles", return_value={}),
    ):
        response = client.get("/reports/restricted.html")

    assert response.status_code == 403


# === Upload endpoint tests ===


def test_upload_report_success(client: TestClient, user):
    """Upload a valid HTML report."""
    with patch("memory.api.content_sources.dispatch_job") as mock_dispatch:
        mock_job = type("MockJob", (), {
            "id": 100,
            "status": "pending",
            "celery_task_id": "celery-task-123",
        })()
        mock_dispatch.return_value = type("DispatchResult", (), {
            "job": mock_job,
            "is_new": True,
        })()

        response = client.post(
            "/reports/upload",
            files={"file": ("report.html", BytesIO(b"<h1>Test</h1>"), "text/html")},
            data={"title": "Test Report", "tags": "test,report"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "queued"
    assert data["job_id"] == 100

    mock_dispatch.assert_called_once()
    call_kwargs = mock_dispatch.call_args.kwargs
    assert call_kwargs["task_kwargs"]["report_format"] == "html"
    assert call_kwargs["task_kwargs"]["title"] == "Test Report"


def test_upload_report_pdf(client: TestClient, user):
    """Upload a valid PDF report."""
    with patch("memory.api.content_sources.dispatch_job") as mock_dispatch:
        mock_job = type("MockJob", (), {
            "id": 101,
            "status": "pending",
            "celery_task_id": "celery-task-456",
        })()
        mock_dispatch.return_value = type("DispatchResult", (), {
            "job": mock_job,
            "is_new": True,
        })()

        response = client.post(
            "/reports/upload",
            files={"file": ("report.pdf", BytesIO(b"%PDF-1.4"), "application/pdf")},
            data={"title": "PDF Report"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "queued"

    call_kwargs = mock_dispatch.call_args.kwargs
    assert call_kwargs["task_kwargs"]["report_format"] == "pdf"


def test_upload_report_invalid_extension(client: TestClient, user):
    """Upload with invalid extension is rejected."""
    response = client.post(
        "/reports/upload",
        files={"file": ("report.docx", BytesIO(b"content"), "application/vnd.openxmlformats")},
        data={"title": "Bad Format"},
    )

    assert response.status_code == 400
    assert "Invalid file type" in response.json()["detail"]


def test_upload_report_existing_access_denied(client: TestClient, db_session, user):
    """Upload with existing filename and no access returns 403."""
    # Pre-create a report with a known filename
    content = b"<h1>Original</h1>"
    import hashlib
    content_hash = hashlib.sha256(content).hexdigest()[:12]
    filename = f"reports/{content_hash}_existing.html"

    report = Report(
        modality="doc",
        mime_type="text/html",
        report_title="Existing",
        report_format="html",
        filename=filename,
        sha256=b"\x03" * 32,
    )
    db_session.add(report)
    db_session.commit()

    with patch("memory.api.content_sources.user_can_access", return_value=False):
        response = client.post(
            "/reports/upload",
            files={"file": ("existing.html", BytesIO(content), "text/html")},
            data={"title": "Overwrite Attempt"},
        )

    assert response.status_code == 403
