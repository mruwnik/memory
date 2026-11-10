"""Tests for MCP reports server."""
# pyright: reportFunctionMemberAccess=false

from unittest.mock import MagicMock, patch

import pytest

from memory.api.MCP.servers.reports import delete, upsert
from memory.common.db import connection as db_connection
from memory.common.db.models import Report
from memory.common.scopes import SCOPE_ADMIN
from tests.conftest import mcp_auth_context


@pytest.fixture(autouse=True)
def reset_db_cache():
    """Reset the cached database engine between tests."""
    db_connection._engine = None
    db_connection._session_factory = None
    yield
    db_connection._engine = None
    db_connection._session_factory = None


def make_mock_user(user_id: int = 1, scopes: list[str] | None = None):
    user = MagicMock()
    user.id = user_id
    user.scopes = scopes or []
    return user


# ====== upsert admin-gating tests ======


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.reports.celery_app")
@patch("memory.api.MCP.servers.reports.make_session")
@patch("memory.api.MCP.servers.reports.get_mcp_current_user")
async def test_upsert_rejects_non_admin_allow_scripts(
    mock_get_user, mock_make_session, mock_celery
):
    """Non-admin caller setting allow_scripts=True must be rejected."""
    mock_get_user.return_value = make_mock_user(scopes=["reports:write"])

    result = await upsert.fn(
        title="Pwn",
        content="<script>alert(1)</script>",
        allow_scripts=True,
    )

    assert "error" in result
    assert "admin" in result["error"].lower()
    # Must not have hit the DB or queued any task.
    mock_make_session.assert_not_called()
    mock_celery.send_task.assert_not_called()


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.reports.celery_app")
@patch("memory.api.MCP.servers.reports.make_session")
@patch("memory.api.MCP.servers.reports.get_mcp_current_user")
async def test_upsert_drops_allowed_connect_urls_for_non_admin(
    mock_get_user, mock_make_session, mock_celery
):
    """Non-admin caller's allowed_connect_urls must be silently dropped, not forwarded."""
    mock_get_user.return_value = make_mock_user(scopes=["reports:write"])

    # No existing report
    mock_session = MagicMock()
    mock_session.query.return_value.filter.return_value.one_or_none.return_value = None
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_task = MagicMock()
    mock_task.id = "task-123"
    mock_celery.send_task.return_value = mock_task

    result = await upsert.fn(
        title="Hello",
        content="<p>safe</p>",
        allow_scripts=False,
        allowed_connect_urls=["https://attacker.example"],
    )

    assert result == {"task_id": "task-123", "status": "queued"}
    forwarded = mock_celery.send_task.call_args.kwargs["kwargs"]
    assert forwarded["allow_scripts"] is False
    # Crucial: the non-admin's URL list must be discarded before dispatch.
    assert forwarded["allowed_connect_urls"] is None


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.reports.celery_app")
@patch("memory.api.MCP.servers.reports.make_session")
@patch("memory.api.MCP.servers.reports.get_mcp_current_user")
async def test_upsert_admin_can_set_allow_scripts(
    mock_get_user, mock_make_session, mock_celery
):
    """Admin caller may set allow_scripts=True and allowed_connect_urls."""
    mock_get_user.return_value = make_mock_user(scopes=[SCOPE_ADMIN])

    mock_session = MagicMock()
    mock_session.query.return_value.filter.return_value.one_or_none.return_value = None
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_task = MagicMock()
    mock_task.id = "task-456"
    mock_celery.send_task.return_value = mock_task

    result = await upsert.fn(
        title="Admin report",
        content="<p>ok</p>",
        allow_scripts=True,
        allowed_connect_urls=["https://api.example.com"],
    )

    assert result == {"task_id": "task-456", "status": "queued"}
    forwarded = mock_celery.send_task.call_args.kwargs["kwargs"]
    assert forwarded["allow_scripts"] is True
    assert forwarded["allowed_connect_urls"] == ["https://api.example.com"]


# ====== overwrite and delete need edit rights ======


def stored_report(db_session, creator, filename: str) -> Report:
    report = Report(
        modality="doc",
        mime_type="text/html",
        sha256=filename.encode().ljust(32, b"\0"),
        filename=f"reports/{filename}",
        report_title="Original",
        report_format="html",
        creator_id=creator.id,
        sensitivity="public",
    )
    db_session.add(report)
    db_session.commit()
    return report


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.reports.celery_app")
async def test_upsert_refuses_to_overwrite_a_readable_report_of_someone_else(
    mock_celery, db_session, admin_user, user_session
):
    report = stored_report(db_session, admin_user, "shared.html")

    with mcp_auth_context(user_session.token):
        result = await upsert.fn(title="Defaced", content="<p>x</p>", filename="shared")

    assert "creator" in result["error"]
    mock_celery.send_task.assert_not_called()
    db_session.refresh(report)
    assert report.report_title == "Original"


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.reports.celery_app")
async def test_upsert_overwrites_own_report(
    mock_celery, db_session, regular_user, user_session
):
    report = stored_report(db_session, regular_user, "mine.html")

    with mcp_auth_context(user_session.token):
        result = await upsert.fn(title="Updated", content="<p>x</p>", filename="mine")

    assert result["status"] == "queued"
    forwarded = mock_celery.send_task.call_args.kwargs["kwargs"]
    assert forwarded["existing_report_id"] == report.id


@pytest.mark.asyncio
async def test_delete_refuses_a_readable_report_of_someone_else(
    db_session, admin_user, user_session
):
    report = stored_report(db_session, admin_user, "keep.html")

    with mcp_auth_context(user_session.token):
        result = await delete.fn(report_id=report.id)

    assert "creator" in result["error"]
    assert db_session.get(Report, report.id) is not None


@pytest.mark.asyncio
async def test_delete_removes_own_report(db_session, regular_user, user_session):
    report_id = stored_report(db_session, regular_user, "gone.html").id

    with mcp_auth_context(user_session.token):
        result = await delete.fn(report_id=report_id)

    assert result == {"status": "deleted", "report_id": report_id}
    db_session.expire_all()
    assert db_session.get(Report, report_id) is None
