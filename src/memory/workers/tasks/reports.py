import logging
import pathlib

from bs4 import BeautifulSoup

from memory.common import settings
from memory.common.db.connection import make_session
from memory.common.db.models import Report
from memory.common.celery_app import app, SYNC_REPORT
from memory.common.content_processing import (
    check_content_exists,
    create_content_hash,
    create_task_result,
    process_content_item,
    safe_task_execution,
)
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


@app.task(name=SYNC_REPORT)
@safe_task_execution
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
):
    tags = tags or []
    filename = pathlib.Path(file_path).name
    logger.info(f"Syncing report {filename} (format={report_format})")

    if content is not None:
        # MCP path: write content to file
        dest = settings.REPORT_STORAGE_DIR / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
        file_bytes = content.encode("utf-8")
    else:
        # Upload path: file already on disk
        source = pathlib.Path(file_path)
        if not source.exists():
            raise FileNotFoundError(f"Report file not found: {file_path}")
        file_bytes = source.read_bytes()

    sha256 = create_content_hash(file_bytes)
    content_size = len(file_bytes)

    # Convert HTML to markdown for searchable content (like BlogPost does)
    markdown_content = ""
    image_paths: list[str] = []
    actual_file = settings.REPORT_STORAGE_DIR / filename
    if report_format == "html" and actual_file.exists():
        markdown_content, image_paths = convert_html_report(actual_file)

    mime_type = "text/html" if report_format == "html" else "application/pdf"

    with make_session() as session:
        existing = check_content_exists(session, Report, sha256=sha256)
        if existing:
            logger.info(f"Report already exists: {existing.id}")
            return create_task_result(existing, "already_exists")

        # Check for update-in-place by filename
        report = session.query(Report).filter(Report.filename == filename).one_or_none()

        # TOCTOU mitigation: if the caller provided an existing_report_id,
        # verify the report we found is the same one that was checked at
        # dispatch time. If a different report now occupies this filename,
        # refuse the overwrite.
        if report and existing_report_id is not None and report.id != existing_report_id:
            raise PermissionError(
                f"Report filename '{filename}' now belongs to a different report "
                f"(expected id={existing_report_id}, found id={report.id})"
            )

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
        report.size = content_size  # type: ignore
        report.allow_scripts = allow_scripts  # type: ignore

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
        if creator_id is not None:
            report.creator_id = creator_id  # type: ignore

        return process_content_item(report, session)
