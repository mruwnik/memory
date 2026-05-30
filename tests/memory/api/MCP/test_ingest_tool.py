"""Tests for the MCP add_content ingest tool."""

import base64
from unittest.mock import patch

import pytest

from memory.api.MCP.servers.ingest import add_content
from tests.conftest import mcp_auth_context

SECRET = "test-secret-for-ingest-tool-tests"


@pytest.fixture(autouse=True)
def patch_transfer_secret():
    with patch("memory.common.settings.TRANSFER_TOKEN_SECRET", SECRET):
        yield


@pytest.mark.asyncio
async def test_inline_data_dispatches(db_session, admin_session):
    data = base64.b64encode(b"%PDF-1.4 small").decode()
    with mcp_auth_context(admin_session.id):
        with patch("memory.api.MCP.servers.ingest.land_and_dispatch") as land:
            land.return_value.model_dump.return_value = {"status": "queued", "job_id": 1}
            res = await add_content.fn(type="application/pdf", name="a.pdf", data=data)
    assert res["status"] == "queued"
    assert land.called


@pytest.mark.asyncio
async def test_project_id_requires_membership(db_session, user_session):
    """A non-admin cannot stamp content into a project they don't belong to."""
    data = base64.b64encode(b"some content").decode()
    with mcp_auth_context(user_session.id):
        with pytest.raises(PermissionError):
            await add_content.fn(
                type="text/plain", name="n.txt", data=data, project_id=999999
            )


@pytest.mark.asyncio
async def test_admin_bypasses_membership(db_session, admin_session):
    """Admins may assign any project_id (membership check exempts them)."""
    data = base64.b64encode(b"some content").decode()
    with mcp_auth_context(admin_session.id):
        with patch("memory.api.MCP.servers.ingest.land_and_dispatch") as land:
            land.return_value.model_dump.return_value = {"status": "queued"}
            res = await add_content.fn(
                type="text/plain", name="n.txt", data=data, project_id=999999
            )
    assert res["status"] == "queued"
    # The project_id reached the dispatch layer.
    assert land.call_args.kwargs["intent"].project_id == 999999


@pytest.mark.asyncio
async def test_project_id_accepted_for_all_types(db_session, admin_session):
    """Book/image content now carries project_id too (no per-bucket rejection)."""
    data = base64.b64encode(b"fake epub bytes").decode()
    with mcp_auth_context(admin_session.id):
        with patch("memory.api.MCP.servers.ingest.land_and_dispatch") as land:
            land.return_value.model_dump.return_value = {"status": "queued"}
            res = await add_content.fn(
                type="application/epub+zip", name="b.epub", data=data, project_id=7
            )
    assert res["status"] == "queued"
    assert land.call_args.kwargs["intent"].project_id == 7


@pytest.mark.asyncio
async def test_too_big_returns_upload_url(db_session, admin_session, monkeypatch):
    from memory.common import settings
    monkeypatch.setattr(settings, "INGEST_INLINE_MAX_BYTES", 4)
    big = base64.b64encode(b"way more than four bytes").decode()
    with mcp_auth_context(admin_session.id):
        res = await add_content.fn(type="application/pdf", name="a.pdf", data=big)
    assert res["status"] == "awaiting_upload"
    assert "/ingest/upload?token=" in res["upload_url"]


@pytest.mark.asyncio
async def test_no_data_no_url_returns_upload_url(db_session, admin_session):
    with mcp_auth_context(admin_session.id):
        res = await add_content.fn(type="application/pdf", name="a.pdf")
    assert res["status"] == "awaiting_upload"
    assert "/ingest/upload?token=" in res["upload_url"]


@pytest.mark.asyncio
async def test_url_and_data_both_rejected(db_session, admin_session):
    with mcp_auth_context(admin_session.id):
        with pytest.raises(ValueError):
            await add_content.fn(
                type="application/pdf",
                name="a.pdf",
                url="https://example.com/doc.pdf",
                data="abc",
            )


@pytest.mark.asyncio
async def test_bad_type_rejected(db_session, admin_session):
    with mcp_auth_context(admin_session.id):
        with pytest.raises(ValueError):
            await add_content.fn(type="not-a-mime", name="a.pdf", data="YWJj")


@pytest.mark.asyncio
async def test_url_fetches_and_dispatches(db_session, admin_session):
    """URL path: fetch succeeds, content-type matches, dispatches."""
    pdf_bytes = b"%PDF-1.4 content"
    with mcp_auth_context(admin_session.id):
        with patch(
            "memory.api.MCP.servers.ingest.stream_download_to_bytes",
            return_value=("application/pdf", pdf_bytes),
        ):
            with patch("memory.api.MCP.servers.ingest.validate_public_url"):
                with patch("memory.api.MCP.servers.ingest.land_and_dispatch") as land:
                    land.return_value.model_dump.return_value = {
                        "status": "queued",
                        "job_id": 2,
                    }
                    res = await add_content.fn(
                        type="application/pdf",
                        name="doc.pdf",
                        url="https://example.com/doc.pdf",
                    )
    assert res["status"] == "queued"
    assert land.called


@pytest.mark.asyncio
async def test_url_content_type_mismatch_raises(db_session, admin_session):
    """URL path: if fetched content-type doesn't match declared type, raise ValueError."""
    with mcp_auth_context(admin_session.id):
        with patch(
            "memory.api.MCP.servers.ingest.stream_download_to_bytes",
            return_value=("text/html; charset=utf-8", b"<html>"),
        ):
            with patch("memory.api.MCP.servers.ingest.validate_public_url"):
                with pytest.raises(ValueError, match="content-type"):
                    await add_content.fn(
                        type="application/pdf",
                        name="doc.pdf",
                        url="https://example.com/doc.pdf",
                    )


@pytest.mark.asyncio
async def test_url_fetch_failure_raises(db_session, admin_session):
    """URL path: None body (download failure) raises ValueError."""
    with mcp_auth_context(admin_session.id):
        with patch(
            "memory.api.MCP.servers.ingest.stream_download_to_bytes",
            return_value=(None, None),
        ):
            with patch("memory.api.MCP.servers.ingest.validate_public_url"):
                with pytest.raises(ValueError, match="(?i)could not fetch"):
                    await add_content.fn(
                        type="application/pdf",
                        name="doc.pdf",
                        url="https://example.com/doc.pdf",
                    )


@pytest.mark.asyncio
async def test_invalid_base64_raises(db_session, admin_session):
    """Bad base64 in data raises ValueError."""
    with mcp_auth_context(admin_session.id):
        with pytest.raises(ValueError, match="base64"):
            await add_content.fn(
                type="application/pdf",
                name="a.pdf",
                data="!!!not-valid-base64!!!",
            )
