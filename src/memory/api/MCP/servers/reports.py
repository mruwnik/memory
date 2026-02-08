"""
MCP subserver for report management.
"""

import hashlib
import logging

from fastmcp import FastMCP

from memory.api.MCP.access import get_mcp_current_user, get_project_roles_by_user_id
from memory.api.MCP.visibility import require_scopes, visible_when
from memory.common import settings
from memory.common.access_control import has_admin_scope, user_can_access
from memory.common.celery_app import SYNC_REPORT
from memory.common.celery_app import app as celery_app
from memory.common.content_processing import clear_item_chunks
from memory.common.db.connection import make_session
from memory.common.db.models import Report
from memory.common.scopes import SCOPE_REPORTS_WRITE

logger = logging.getLogger(__name__)

reports_mcp = FastMCP("memory-reports")


@reports_mcp.tool()
@visible_when(require_scopes(SCOPE_REPORTS_WRITE))
async def upsert(
    title: str,
    content: str,
    tags: list[str] | None = None,
    filename: str | None = None,
) -> dict:
    """
    Create or update a report from HTML content.
    Use for rich formatted content with tables, graphs, or custom styling.
    PDF reports should be uploaded via the REST upload endpoint.

    Args:
        title: Title of the report
        content: Report content (HTML string)
        tags: Organization tags for filtering and discovery
        filename: Stable filename for upsert (e.g. "my_report.html").
                  If omitted, one is generated from the content hash + title.
    """
    tags = tags or []
    logger.info("MCP: upserting report: %s", title)

    # Require authenticated user
    user = get_mcp_current_user()
    user_id: int | None = getattr(user, "id", None) if user else None
    if not user or user_id is None:
        return {"error": "Authentication required to create reports"}

    # Use caller-supplied filename or generate from content hash + title
    if not filename:
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:12]
        safe_title = "".join(c if c.isalnum() or c in "-_ " else "" for c in title).strip()[:50]
        filename = f"{content_hash}_{safe_title}.html"
    elif not filename.endswith(".html"):
        filename = f"{filename}.html"

    # Access check on existing report with same filename, and capture its ID
    # for the worker to re-verify ownership (mitigates TOCTOU race).
    existing_report_id: int | None = None
    with make_session() as session:
        existing = session.query(Report).filter(Report.filename == filename).one_or_none()
        if existing:
            project_roles = get_project_roles_by_user_id(user_id)
            if not user_can_access(user, existing, project_roles):
                return {"error": "Cannot overwrite this report - access denied"}
            existing_report_id = existing.id

    file_path = settings.REPORT_STORAGE_DIR / filename

    task = celery_app.send_task(
        SYNC_REPORT,
        queue=f"{settings.CELERY_QUEUE_PREFIX}-reports",
        kwargs={
            "file_path": str(file_path),
            "title": title,
            "content": content,
            "report_format": "html",
            "tags": tags,
            "creator_id": user_id,
            "existing_report_id": existing_report_id,
        },
    )

    return {
        "task_id": task.id,
        "status": "queued",
    }


@reports_mcp.tool()
@visible_when(require_scopes(SCOPE_REPORTS_WRITE))
async def delete(report_id: int) -> dict:
    """
    Delete a report and its associated data (chunks, vectors, file).

    Args:
        report_id: ID of the report to delete
    """
    user = get_mcp_current_user()
    user_id: int | None = getattr(user, "id", None) if user else None
    if not user or user_id is None:
        return {"error": "Authentication required"}

    with make_session() as session:
        report = session.get(Report, report_id)
        if not report:
            return {"error": "Report not found"}

        project_roles = (
            get_project_roles_by_user_id(user_id)
            if not has_admin_scope(user)
            else {}
        )
        if not user_can_access(user, report, project_roles):
            return {"error": "Report not found"}

        try:
            clear_item_chunks(report, session)
        except Exception as e:
            logger.error("Error clearing chunks for report %d: %s", report_id, e)

        if report.filename:
            file_path = settings.REPORT_STORAGE_DIR / report.filename
            if file_path.exists():
                try:
                    file_path.unlink()
                    logger.info("Deleted report file: %s", file_path)
                except OSError as e:
                    logger.error("Error deleting report file %s: %s", file_path, e)

        session.delete(report)
        session.commit()

    return {"status": "deleted", "report_id": report_id}
