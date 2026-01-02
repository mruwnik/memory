"""API endpoints for listing content sources (books, forum posts, photos)."""

import hashlib
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Query, UploadFile, File, Form, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from memory.api.auth import get_current_user
from memory.common.db.connection import get_session
from memory.common.db.models import User
from memory.common.db.models.sources import Book
from memory.common.db.models.source_items import BookSection, Photo
from memory.common import settings
from memory.common.celery_app import SYNC_BOOK, SYNC_LESSWRONG, SYNC_PHOTO
from memory.common.celery_app import app as celery_app

logger = logging.getLogger(__name__)

router = APIRouter(tags=["content-sources"])


# === Books ===


class BookResponse(BaseModel):
    id: int
    title: str
    author: str | None
    publisher: str | None
    published: str | None
    language: str | None
    total_pages: int | None
    tags: list[str]
    section_count: int
    file_path: str | None


@router.get("/books")
def list_books(
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[BookResponse]:
    """List all books in the knowledge base."""
    # Get books with section counts
    section_counts = (
        db.query(BookSection.book_id, func.count(BookSection.id).label("count"))
        .group_by(BookSection.book_id)
        .subquery()
    )

    books = (
        db.query(Book, func.coalesce(section_counts.c.count, 0).label("section_count"))
        .outerjoin(section_counts, Book.id == section_counts.c.book_id)
        .order_by(Book.title)
        .limit(limit)
        .all()
    )

    return [
        BookResponse(
            id=book.id,
            title=book.title,
            author=book.author,
            publisher=book.publisher,
            published=book.published.isoformat() if book.published else None,
            language=book.language,
            total_pages=book.total_pages,
            tags=book.tags or [],
            section_count=section_count,
            file_path=book.file_path,
        )
        for book, section_count in books
    ]


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
    db: Session = Depends(get_session),
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
    task_id: str | None = None
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
) -> UploadResponse:
    """Upload an ebook file for processing."""
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

    # Trigger celery task
    task = celery_app.send_task(
        SYNC_BOOK,
        kwargs={
            "file_path": str(file_path),
            "tags": tag_list,
            "title": title,
            "author": author,
        },
    )

    return UploadResponse(
        status="queued",
        message=f"Book '{file.filename}' uploaded and queued for processing",
        task_id=task.id,
        filename=safe_filename,
    )


@router.post("/photos/upload")
async def upload_photo(
    file: UploadFile = File(...),
    tags: str = Form(default=""),
    user: User = Depends(get_current_user),
) -> UploadResponse:
    """Upload a photo for indexing."""
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

    # Trigger celery task for processing (extracts EXIF, generates embeddings)
    task = celery_app.send_task(
        SYNC_PHOTO,
        kwargs={
            "file_path": str(file_path),
            "tags": tag_list,
        },
    )

    return UploadResponse(
        status="queued",
        message=f"Photo '{file.filename}' uploaded and queued for processing",
        task_id=task.id,
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
