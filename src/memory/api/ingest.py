"""Generic content ingestion: the /ingest/upload endpoint (token-bound) plus
the shared helper that lands bytes and dispatches the right bucket task.

The helper ``land_and_dispatch`` is reused by the MCP add_content tool so both
paths share the same content-based routing from ``ingest_routing``.
"""

import hashlib
import logging
import pathlib

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from memory.api import ingest_tokens
from memory.api.request_body import read_request_body_with_cap
from memory.common import ingest_routing
from memory.common.db.connection import get_session
from memory.common.db.models import JobType
from memory.common.jobs import dispatch_job

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ingest"])


class IngestResponse(BaseModel):
    status: str
    job_id: int | None = None
    task_id: str | None = None
    filename: str | None = None


def land_and_dispatch(
    db: DBSession,
    *,
    content: bytes,
    intent: ingest_tokens.IngestTokenPayload,
) -> IngestResponse:
    """Route ``content`` to a bucket, write it to that bucket's storage dir, and
    dispatch its Celery task.

    Routing inspects the bytes (``ingest_routing.detect_bucket``) rather than
    trusting the declared type. Raises ValueError if content exceeds the chosen
    bucket's cap; cleans up the written file if task dispatch fails.
    """
    spec = ingest_routing.detect_bucket(intent.type, content)

    if len(content) > spec.max_bytes:
        raise ValueError(f"Content too large (max {spec.max_bytes} bytes)")

    spec.storage_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()[:12]
    safe_name = f"{digest}_{pathlib.Path(intent.filename).name}"
    file_path = spec.storage_dir / safe_name
    file_path.write_bytes(content)

    task_kwargs = build_task_kwargs(spec, file_path, intent)
    try:
        result = dispatch_job(
            session=db,
            job_type=JobType.CONTENT_INGEST,
            task_name=spec.task_name,
            task_kwargs=task_kwargs,
            user_id=intent.user_id,
        )
    except Exception:
        file_path.unlink(missing_ok=True)
        raise

    return IngestResponse(
        status="queued" if result.is_new else result.job.status,
        job_id=result.job.id,
        task_id=result.job.celery_task_id,
        filename=safe_name,
    )


def build_task_kwargs(
    spec: ingest_routing.BucketSpec,
    file_path: pathlib.Path,
    intent: ingest_tokens.IngestTokenPayload,
) -> dict:
    """Build the task-specific kwargs for the chosen bucket. Every bucket
    carries the owner/project so ingested content is visible to its creator,
    not admin-only."""
    common = {
        "file_path": str(file_path),
        "tags": intent.tags,
        "creator_id": intent.user_id,
        "project_id": intent.project_id,
    }
    if spec.name == "image":
        return common
    if spec.name == "book":
        return {
            **common,
            "title": str(intent.doc_metadata.get("title", "")),
            "author": str(intent.doc_metadata.get("author", "")),
        }
    return {
        **common,
        "mime_type": ingest_routing.normalize_mime(intent.type),
        "doc_metadata": intent.doc_metadata,
    }


@router.post("/ingest/upload", response_model=IngestResponse)
async def ingest_upload(
    request: Request,
    token: str,
    db: DBSession = Depends(get_session),
) -> IngestResponse:
    """Token-authenticated upload endpoint.

    The signed token encodes the declared MIME, filename, tags, metadata, and
    the user who minted it. The raw request body is read under a generous cap
    (the bucket and its specific cap are resolved from the bytes in
    ``land_and_dispatch``), then dispatched as a Celery task.
    """
    try:
        intent = ingest_tokens.verify_token(token)
    except ingest_tokens.IngestTokenExpiredError:
        raise HTTPException(status_code=401, detail="Upload link expired")
    except ingest_tokens.IngestTokenError:
        raise HTTPException(status_code=403, detail="Invalid upload token")

    content = await read_request_body_with_cap(request, ingest_routing.max_ingest_bytes())
    try:
        return land_and_dispatch(db, content=content, intent=intent)
    except ValueError as e:
        raise HTTPException(status_code=413, detail=str(e))
