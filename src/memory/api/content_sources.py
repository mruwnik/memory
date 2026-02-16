"""API endpoints for listing content sources (books, forum posts, photos)."""

import hashlib
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Query, UploadFile, File, Form, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from memory.api.auth import get_current_user
from memory.common.access_control import get_user_project_roles, has_admin_scope, user_can_access
from memory.common.db.connection import get_session
from memory.common.db.models import User, JobType
from memory.common.db.models.source_items import Photo
from memory.common import settings
from memory.common.celery_app import SYNC_BOOK, SYNC_LESSWRONG, SYNC_PHOTO, SYNC_REPORT
from memory.common.celery_app import app as celery_app
from memory.common.jobs import dispatch_job
from memory.common.content_processing import clear_item_chunks
from memory.common.db.models.source_items import Report

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
    """List photos the user has access to.

    Note: Uses overfetch strategy to avoid loading all photos. If user has access
    to fewer than `limit` photos in the first batch, fewer results are returned.
    For guaranteed pagination, use cursor-based pagination (not yet implemented).
    """
    project_roles = get_user_project_roles(db, user)

    # Overfetch to account for filtering, then filter by access.
    # This avoids loading ALL photos into memory while still ensuring we get enough
    # accessible ones. The multiplier accounts for typical access patterns.
    # Limitation: may return fewer than `limit` results if user has sparse access.
    overfetch_multiplier = 3
    photos = (
        db.query(Photo)
        .order_by(Photo.exif_taken_at.desc().nulls_last())
        .limit(limit * overfetch_multiplier)
        .all()
    )

    accessible_photos = [
        photo for photo in photos if user_can_access(user, photo, project_roles)
    ][:limit]

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
        for photo in accessible_photos
    ]


class DeleteResponse(BaseModel):
    status: str


@router.delete("/photos/{photo_id}")
def delete_photo(
    photo_id: int,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> DeleteResponse:
    """Delete a photo and its associated data."""
    photo = db.get(Photo, photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    # Check user has access to this photo
    project_roles = get_user_project_roles(db, user)
    if not user_can_access(user, photo, project_roles):
        raise HTTPException(status_code=404, detail="Photo not found")

    # Delete chunks from Qdrant and PostgreSQL
    try:
        clear_item_chunks(photo, db)
    except Exception as e:
        logger.error(f"Error clearing chunks for photo {photo_id}: {e}")

    # Delete the physical file if it exists
    if photo.filename:
        file_path = settings.FILE_STORAGE_DIR / photo.filename
        if file_path.exists():
            try:
                file_path.unlink()
                logger.info(f"Deleted file: {file_path}")
            except OSError as e:
                logger.error(f"Error deleting file {file_path}: {e}")

    # Delete the photo record
    db.delete(photo)
    db.commit()

    return DeleteResponse(status="deleted")


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


# === Reports ===

ALLOWED_REPORT_EXTENSIONS = {".pdf", ".html", ".htm"}


@router.post("/reports/upload")
async def upload_report(
    file: UploadFile = File(...),
    title: str = Form(default=""),
    tags: str = Form(default=""),
    project_id: int | None = Form(default=None),
    allow_scripts: bool = Form(default=False),
    allowed_connect_urls: str = Form(default=""),
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> UploadResponse:
    """Upload an HTML or PDF report for indexing.

    Args:
        allowed_connect_urls: Comma-separated list of external URLs allowed for CSP connect-src
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_REPORT_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed: {', '.join(ALLOWED_REPORT_EXTENSIONS)}",
        )

    report_format = "pdf" if ext == ".pdf" else "html"

    settings.REPORT_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    content = await file.read()
    content_hash = hashlib.sha256(content).hexdigest()[:12]
    safe_filename = f"{content_hash}_{Path(file.filename).name}"
    file_path = settings.REPORT_STORAGE_DIR / safe_filename

    # Check for existing report with same content hash (prefix match) to catch
    # duplicate content regardless of original filename
    existing = (
        db.query(Report)
        .filter(Report.filename.startswith(f"{content_hash}_"))
        .first()
    )
    if not existing:
        # Also check exact filename match for update-in-place
        existing = db.query(Report).filter(Report.filename == safe_filename).one_or_none()
    if existing:
        project_roles = get_user_project_roles(db, user) if not has_admin_scope(user) else {}
        if not user_can_access(user, existing, project_roles):
            raise HTTPException(status_code=403, detail="Cannot overwrite this report")

    file_path.write_bytes(content)
    logger.info(f"Saved report to {file_path}")

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    url_list = (
        [u.strip() for u in allowed_connect_urls.split(",") if u.strip()]
        if allowed_connect_urls
        else None
    )

    try:
        result = dispatch_job(
            session=db,
            job_type=JobType.CONTENT_INGEST,
            task_name=SYNC_REPORT,
            task_kwargs={
                "file_path": str(file_path),
                "tags": tag_list,
                "title": title or None,
                "report_format": report_format,
                "project_id": project_id,
                "creator_id": user.id,
                "allow_scripts": allow_scripts,
                "allowed_connect_urls": url_list,
            },
            user_id=user.id,
        )
    except Exception:
        file_path.unlink(missing_ok=True)
        logger.warning(f"Cleaned up orphaned file after dispatch failure: {file_path}")
        raise

    return UploadResponse(
        status="queued" if result.is_new else result.job.status,
        message=f"Report '{file.filename}' uploaded and queued for processing",
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
