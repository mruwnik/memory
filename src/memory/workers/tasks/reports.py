import logging
import pathlib

from bs4 import BeautifulSoup

from memory.common import paths, settings
from memory.common.db.connection import make_session
from memory.common.db.models import Report
from memory.common.celery_app import app, SYNC_REPORT
from memory.common.content_processing import (
    check_content_exists,
    create_content_hash,
    create_task_result,
    process_content_item,
)
from memory.common.jobs import tracked_task
from memory.parsers.html import process_images, convert_to_markdown

logger = logging.getLogger(__name__)


def convert_html_report(file_path: pathlib.Path) -> tuple[str, list[str]]:
    """Convert an HTML report file to markdown text and extract images.

    Returns:
        (markdown_content, image_paths) - the searchable text and extracted image paths.
    """
    html_content = file_path.read_text(errors="replace")
    soup = BeautifulSoup(html_content, "html.parser")

    image_dir = settings.REPORT_STORAGE_DIR / "images"
    image_dir.mkdir(parents=True, exist_ok=True)

    soup, pil_images = process_images(soup, "", image_dir)
    image_list = list(pil_images.keys()) if pil_images else []
    markdown_text = convert_to_markdown(soup, "")

    return markdown_text, image_list


def report_bytes(file_path: str, content: str | None) -> bytes:
    """The report's bytes: the inline content, or the uploaded file on disk."""
    if content is not None:
        return content.encode("utf-8")
    source = pathlib.Path(file_path)
    if not source.exists():
        raise FileNotFoundError(f"Report file not found: {file_path}")
    return source.read_bytes()


def overwrite_target(
    session, filename: str, existing_report_id: int | None
) -> Report | None:
    """The report stored under ``filename``, if the caller may overwrite it.

    The dispatching door checked edit rights on ``existing_report_id`` (None:
    no report had this filename). Any other report found here appeared or
    changed hands since, so the sync is refused.
    """
    report = session.query(Report).filter(Report.filename == filename).one_or_none()
    if report is not None and report.id != existing_report_id:
        raise PermissionError(
            f"Report filename '{filename}' belongs to report {report.id}, "
            f"not the one checked at dispatch ({existing_report_id})"
        )
    return report


@app.task(name=SYNC_REPORT)
@tracked_task
def sync_report(
    file_path: str,
    title: str | None = None,
    tags: list[str] | None = None,
    content: str | None = None,
    report_format: str = "html",
    project_id: int | None = None,
    creator_id: int | None = None,
    existing_report_id: int | None = None,
    allow_scripts: bool = False,
    allowed_connect_urls: list[str] | None = None,
):
    tags = tags or []
    # Reports are flat under REPORT_STORAGE_DIR: `.name` drops any
    # subdirectory in `file_path`. Both callers build it from a content hash
    # or a validated filename, so none is expected.
    basename = pathlib.Path(file_path).name
    # Report.filename is FILE_STORAGE_DIR-relative (matches every other
    # SourceItem subtype). Reports always live directly under reports/.
    filename = paths.to_db_filename(settings.REPORT_STORAGE_DIR / basename)
    logger.info(f"Syncing report {filename} (format={report_format})")

    file_bytes = report_bytes(file_path, content)
    sha256 = create_content_hash(file_bytes)
    mime_type = "text/html" if report_format == "html" else "application/pdf"
    actual_file = settings.REPORT_STORAGE_DIR / basename

    with make_session() as session:
        existing = check_content_exists(session, Report, sha256=sha256)
        if existing:
            logger.info(f"Report already exists: {existing.id}")
            return create_task_result(existing, "already_exists")

        report = overwrite_target(session, filename, existing_report_id)

        if content is not None:
            # MCP path: the file is written only once the overwrite is allowed.
            actual_file.parent.mkdir(parents=True, exist_ok=True)
            actual_file.write_text(content)

        # Convert HTML to markdown for searchable content (like BlogPost does)
        markdown_content = ""
        image_paths: list[str] = []
        if report_format == "html" and actual_file.exists():
            markdown_content, image_paths = convert_html_report(actual_file)

        if not report:
            report = Report(
                modality="doc",
                mime_type=mime_type,
            )
        else:
            logger.info("Updating existing report")

        report.report_title = title  # type: ignore
        report.report_format = report_format  # type: ignore
        report.filename = filename  # type: ignore
        report.embed_status = "RAW"  # type: ignore
        report.sha256 = sha256  # type: ignore
        report.size = len(file_bytes)  # type: ignore
        report.allow_scripts = allow_scripts  # type: ignore
        report.allowed_connect_urls = allowed_connect_urls  # type: ignore

        # Store processed markdown content and images for chunking
        # (same pattern as BlogPost: content = searchable markdown)
        if markdown_content:
            report.content = markdown_content  # type: ignore
        if image_paths:
            report.images = image_paths  # type: ignore

        if tags:
            report.tags = tags  # type: ignore
        if project_id is not None:
            report.project_id = project_id  # type: ignore
        if creator_id is not None and report.creator_id is None:
            report.creator_id = creator_id  # type: ignore

        return process_content_item(report, session)
