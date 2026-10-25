"""Celery task for ingesting arbitrary unattached files as MiscDoc."""

import hashlib
import logging
import pathlib

from memory.common import paths, settings
from memory.common.celery_app import app, SYNC_MISC_DOC
from memory.common.content_processing import (
    find_readable_upload,
    create_task_result,
    process_content_item,
)
from memory.common.db.connection import make_session
from memory.common.db.models import MiscDoc
from memory.common.jobs import tracked_task

logger = logging.getLogger(__name__)


@app.task(name=SYNC_MISC_DOC)
@tracked_task
def sync_misc_doc(
    file_path: str,
    mime_type: str,
    tags: list[str] | None = None,
    doc_metadata: dict | None = None,
    creator_id: int | None = None,
    project_id: int | None = None,
    creator_scopes: list[str] | None = None,
) -> dict:
    """Ingest an arbitrary file from MISC_STORAGE_DIR as a MiscDoc."""
    path = pathlib.Path(file_path)
    if not path.resolve().is_relative_to(settings.MISC_STORAGE_DIR.resolve()):
        raise ValueError(f"{file_path} is not under MISC_STORAGE_DIR")

    content = path.read_bytes()
    sha256 = hashlib.sha256(content).digest()
    db_filename = paths.to_db_filename(path.resolve())

    with make_session() as session:
        existing, sha256 = find_readable_upload(session, MiscDoc, sha256, creator_id, creator_scopes)
        if existing:
            logger.info(f"MiscDoc already exists: {existing.id}")
            return create_task_result(existing, "already_exists")

        doc = MiscDoc(
            modality="doc",
            mime_type=mime_type,
            filename=db_filename,
            sha256=sha256,
            size=len(content),
            tags=tags or [],
            doc_metadata=doc_metadata or {},
            creator_id=creator_id,
            project_id=project_id,
        )
        result = process_content_item(doc, session)
        if result.get("status") == "skipped" or not result.get("chunks_count"):
            logger.warning(
                "MiscDoc %s (%s, mime=%s) produced no chunks; it is stored "
                "but will not appear in semantic search (no extractor for this "
                "MIME type, or extraction/embedding yielded nothing).",
                doc.id,
                doc.filename,
                mime_type,
            )
        return result
