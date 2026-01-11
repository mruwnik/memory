"""API endpoints for listing content sources (books, forum posts, photos)."""

import hashlib
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Query, UploadFile, File, Form, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from memory.api.auth import get_current_user
from memory.common.db.connection import get_session
from memory.common.db.models import User, JobType
from memory.common.db.models.source_items import Photo
from memory.common import settings
from memory.common.celery_app import SYNC_BOOK, SYNC_LESSWRONG, SYNC_PHOTO
from memory.common.celery_app import app as celery_app
from memory.common.jobs import dispatch_job

logger = logging.getLogger(__name__)

router = APIRouter(tags=["content-sources"])


# === Photos ===


class PhotoResponse(BaseModel):
    id: int
    filename: str
    file_path: str | None
    exif_taken_at: str | None
    camera: str | None
    tags: list[str]
    mime_type: str | None


@router.get("/photos")
def list_photos(
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> list[PhotoResponse]:
    """List all photos in the knowledge base."""
    photos = (
        db.query(Photo)
        .order_by(Photo.exif_taken_at.desc().nulls_last())
        .limit(limit)
        .all()
    )

    return [
        PhotoResponse(
            id=photo.id,
            # filename field stores the relative path in SourceItem
            filename=Path(photo.filename).name if photo.filename else "unknown",
            file_path=photo.filename,  # The path relative to FILE_STORAGE_DIR
            exif_taken_at=photo.exif_taken_at.isoformat()
            if photo.exif_taken_at
            else None,
            camera=photo.camera,
            tags=photo.tags or [],
            mime_type=photo.mime_type,
        )
        for photo in photos
    ]


# === Upload Endpoints ===


class UploadResponse(BaseModel):
    status: str
    message: str
    job_id: int | None = None
    task_id: str | None = None  # Deprecated: use job_id for status tracking
    filename: str | None = None


ALLOWED_EBOOK_EXTENSIONS = {".epub", ".pdf", ".mobi", ".azw", ".azw3"}
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif"}


@router.post("/books/upload")
async def upload_book(
    file: UploadFile = File(...),
    title: str = Form(default=""),
    author: str = Form(default=""),
    tags: str = Form(default=""),
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> UploadResponse:
    """
    Upload an ebook file for processing.

    Returns a job_id that can be used to track processing status via GET /jobs/{job_id}
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    # Validate file extension
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EBOOK_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed: {', '.join(ALLOWED_EBOOK_EXTENSIONS)}",
        )

    # Ensure storage directory exists
    settings.EBOOK_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    # Generate unique filename to avoid collisions
    content = await file.read()
    content_hash = hashlib.sha256(content).hexdigest()[:12]
    safe_filename = f"{content_hash}_{Path(file.filename).name}"
    file_path = settings.EBOOK_STORAGE_DIR / safe_filename

    # Save the file
    file_path.write_bytes(content)
    logger.info(f"Saved ebook to {file_path}")

    # Parse tags
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    # Dispatch job with tracking - clean up file on failure
    try:
        result = dispatch_job(
            session=db,
            job_type=JobType.CONTENT_INGEST,
            task_name=SYNC_BOOK,
            task_kwargs={
                "file_path": str(file_path),
                "tags": tag_list,
                "title": title,
                "author": author,
            },
            user_id=user.id,
        )
    except Exception:
        # Clean up the uploaded file if job dispatch fails
        # Use missing_ok=True to avoid race condition if another process deleted it
        file_path.unlink(missing_ok=True)
        logger.warning(f"Cleaned up orphaned file after dispatch failure: {file_path}")
        raise

    return UploadResponse(
        status="queued" if result.is_new else result.job.status,
        message=f"Book '{file.filename}' uploaded and queued for processing",
        job_id=result.job.id,
        task_id=result.job.celery_task_id,
        filename=safe_filename,
    )


@router.post("/photos/upload")
async def upload_photo(
    file: UploadFile = File(...),
    tags: str = Form(default=""),
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> UploadResponse:
    """
    Upload a photo for indexing.

    Returns a job_id that can be used to track processing status via GET /jobs/{job_id}
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    # Validate file extension
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed: {', '.join(ALLOWED_IMAGE_EXTENSIONS)}",
        )

    # Ensure storage directory exists
    settings.PHOTO_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    # Generate unique filename to avoid collisions
    content = await file.read()
    content_hash = hashlib.sha256(content).hexdigest()[:12]
    safe_filename = f"{content_hash}_{Path(file.filename).name}"
    file_path = settings.PHOTO_STORAGE_DIR / safe_filename

    # Save the file
    file_path.write_bytes(content)
    logger.info(f"Saved photo to {file_path}")

    # Parse tags
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    # Dispatch job with tracking - clean up file on failure
    try:
        result = dispatch_job(
            session=db,
            job_type=JobType.CONTENT_INGEST,
            task_name=SYNC_PHOTO,
            task_kwargs={
                "file_path": str(file_path),
                "tags": tag_list,
            },
            user_id=user.id,
        )
    except Exception:
        # Clean up the uploaded file if job dispatch fails
        # Use missing_ok=True to avoid race condition if another process deleted it
        file_path.unlink(missing_ok=True)
        logger.warning(f"Cleaned up orphaned file after dispatch failure: {file_path}")
        raise

    return UploadResponse(
        status="queued" if result.is_new else result.job.status,
        message=f"Photo '{file.filename}' uploaded and queued for processing",
        job_id=result.job.id,
        task_id=result.job.celery_task_id,
        filename=safe_filename,
    )


# === Forum Sync ===


class ForumSyncRequest(BaseModel):
    since: str | None = None
    min_karma: int = 10
    limit: int = 50
    max_items: int = 1000
    af: bool = False
    tags: list[str] = []


class ForumSyncResponse(BaseModel):
    status: str
    message: str
    task_id: str


@router.post("/forums/sync")
def trigger_forum_sync(
    request: ForumSyncRequest,
    user: User = Depends(get_current_user),
) -> ForumSyncResponse:
    """Trigger a LessWrong forum sync with the given parameters."""
    task = celery_app.send_task(
        SYNC_LESSWRONG,
        kwargs={
            "since": request.since,
            "min_karma": request.min_karma,
            "limit": request.limit,
            "max_items": request.max_items,
            "af": request.af,
            "tags": request.tags,
        },
    )

    return ForumSyncResponse(
        status="queued",
        message="LessWrong sync started",
        task_id=task.id,
    )
