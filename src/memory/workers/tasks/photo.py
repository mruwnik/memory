"""Photo processing tasks."""

import hashlib
import logging
from pathlib import Path
from datetime import datetime
from typing import TypedDict

from PIL import Image
from PIL.ExifTags import TAGS
from sqlalchemy.orm import Session

from memory.common import settings
from memory.common.celery_app import SYNC_PHOTO, REPROCESS_PHOTO, app
from memory.common.db.connection import make_session
from memory.common.db.models import Photo
from memory.common import jobs as job_utils
from memory.workers.tasks.content_processing import (
    check_content_exists,
    clear_item_chunks,
    process_content_item,
    safe_task_execution,
)

logger = logging.getLogger(__name__)


class PhotoProcessingResult(TypedDict, total=False):
    """Result of photo processing operations."""

    status: str
    photo_id: int
    error: str


def extract_exif_data(image_path: Path) -> dict:
    """Extract EXIF metadata from an image file."""
    exif_data = {}
    try:
        with Image.open(image_path) as img:
            exif = img._getexif()
            if exif:
                for tag_id, value in exif.items():
                    tag = TAGS.get(tag_id, tag_id)
                    exif_data[tag] = value
    except Exception as e:
        logger.warning(f"Failed to extract EXIF from {image_path}: {e}")
    return exif_data


def parse_exif_datetime(exif_data: dict) -> datetime | None:
    """Parse EXIF datetime fields."""
    for field in ["DateTimeOriginal", "DateTime", "DateTimeDigitized"]:
        if field in exif_data:
            try:
                return datetime.strptime(exif_data[field], "%Y:%m:%d %H:%M:%S")
            except (ValueError, TypeError):
                continue
    return None


def get_camera_info(exif_data: dict) -> str | None:
    """Extract camera make/model from EXIF."""
    make = exif_data.get("Make", "")
    model = exif_data.get("Model", "")
    if make and model:
        # Avoid duplication like "Canon Canon EOS 5D"
        if model.startswith(make):
            return model
        return f"{make} {model}"
    return model or make or None


def get_gps_coordinates(exif_data: dict) -> tuple[float | None, float | None]:
    """Extract GPS coordinates from EXIF."""
    gps_info = exif_data.get("GPSInfo", {})
    if not gps_info:
        return None, None

    def convert_to_degrees(value):
        """Convert GPS coordinates to degrees."""
        try:
            d, m, s = value
            return float(d) + float(m) / 60 + float(s) / 3600
        except (TypeError, ValueError):
            return None

    lat = convert_to_degrees(gps_info.get(2))  # GPSLatitude
    lon = convert_to_degrees(gps_info.get(4))  # GPSLongitude

    if lat and gps_info.get(1) == "S":  # GPSLatitudeRef
        lat = -lat
    if lon and gps_info.get(3) == "W":  # GPSLongitudeRef
        lon = -lon

    return lat, lon


def prepare_photo_for_reingest(session: Session, item_id: int) -> Photo | None:
    """
    Fetch an existing photo and clear its chunks for reprocessing.

    Returns the photo if found, None otherwise.
    """
    photo = session.get(Photo, item_id)
    if not photo:
        return None

    clear_item_chunks(photo, session)
    session.flush()
    logger.info(f"Prepared photo {item_id} for reingest: cleared chunks")
    return photo


def execute_photo_processing(
    session: Session,
    photo: Photo,
    job_id: int | None = None,
) -> PhotoProcessingResult:
    """
    Run the full processing pipeline on a photo.

    This is the shared processing step for both ingest and reingest:
    1. Generate embeddings
    2. Push to Qdrant

    Args:
        session: Database session
        photo: Photo record (new or existing with chunks cleared)
        job_id: Optional job ID for status tracking

    Returns:
        Dict with processing results
    """
    # Capture ID before try block to avoid DetachedInstanceError after rollback
    photo_id = photo.id

    try:
        result = process_content_item(photo, session)

        if job_id:
            job_utils.complete_job(session, job_id, result_id=photo.id, result_type="Photo")

        session.commit()
        logger.info(f"Successfully processed photo: {photo.filename}")

        return result

    except Exception as e:
        logger.exception(f"Failed to process photo {photo_id}: {e}")
        # Rollback partial work to avoid persisting incomplete state
        session.rollback()
        # Now mark the job as failed in a clean transaction
        if job_id:
            job_utils.fail_job(session, job_id, str(e))
            session.commit()
        return {"status": "error", "error": str(e), "photo_id": photo_id}


def validate_and_parse_photo(file_path: str) -> tuple[Path, bytes, dict]:
    """
    Validate file exists and extract photo data.

    Returns:
        Tuple of (resolved path, file content, exif_data dict)
    """
    path = Path(file_path)

    # Resolve relative paths
    if not path.is_absolute():
        path = settings.PHOTO_STORAGE_DIR / path

    if not path.exists():
        raise FileNotFoundError(f"Photo file not found: {path}")

    logger.info(f"Validating and parsing photo: {path}")

    content = path.read_bytes()
    exif_data = extract_exif_data(path)

    return path, content, exif_data


def create_photo_from_file(
    path: Path,
    content: bytes,
    exif_data: dict,
    tags: list[str],
) -> Photo:
    """Create a Photo model from file data."""
    sha256 = hashlib.sha256(content).digest()
    taken_at = parse_exif_datetime(exif_data)
    camera = get_camera_info(exif_data)
    lat, lon = get_gps_coordinates(exif_data)

    # Determine mime type
    suffix = path.suffix.lower()
    mime_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".heic": "image/heic",
        ".heif": "image/heif",
    }
    mime_type = mime_types.get(suffix, "image/jpeg")

    # Compute relative path for storage
    try:
        relative_path = path.relative_to(settings.FILE_STORAGE_DIR).as_posix()
    except ValueError:
        # File is outside FILE_STORAGE_DIR, use just the filename
        relative_path = path.name

    return Photo(
        filename=relative_path,
        mime_type=mime_type,
        modality="photo",
        size=len(content),
        sha256=sha256,
        tags=tags,
        embed_status="RAW",
        exif_taken_at=taken_at,
        exif_lat=lat,
        exif_lon=lon,
        camera=camera,
    )


@app.task(name=SYNC_PHOTO)
@safe_task_execution
def sync_photo(
    file_path: str,
    tags: list[str] | None = None,
    job_id: int | None = None,
) -> PhotoProcessingResult:
    """
    Process a new photo file and add it to the knowledge base.

    Args:
        file_path: Path to the photo file (absolute or relative to PHOTO_STORAGE_DIR)
        tags: Optional list of tags to apply
        job_id: Optional job ID for status tracking

    Returns:
        PhotoProcessingResult with status and photo_id
    """
    logger.info(f"Processing new photo from {file_path} (job_id={job_id})")

    tags = tags or []
    path, content, exif_data = validate_and_parse_photo(file_path)
    sha256 = hashlib.sha256(content).digest()

    with make_session() as session:
        if job_id:
            job_utils.start_job(session, job_id)
            session.commit()

        # Check for existing photo (idempotency)
        existing = check_content_exists(session, Photo, sha256=sha256)
        if existing:
            logger.info(f"Photo already exists: {existing.filename}")
            if job_id:
                job_utils.complete_job(
                    session, job_id, result_id=existing.id, result_type="Photo"
                )
                session.commit()
            return {"status": "already_exists", "photo_id": existing.id}

        # Create new photo record
        photo = create_photo_from_file(path, content, exif_data, tags)
        session.add(photo)
        session.flush()

        return execute_photo_processing(session, photo, job_id=job_id)


@app.task(name=REPROCESS_PHOTO)
@safe_task_execution
def reprocess_photo(
    item_id: int,
    job_id: int | None = None,
) -> PhotoProcessingResult:
    """
    Reprocess an existing photo.

    Fetches the photo, clears existing chunks, and re-runs the processing pipeline.

    Args:
        item_id: ID of the photo to reprocess
        job_id: Optional job ID for status tracking

    Returns:
        PhotoProcessingResult with status and photo_id or error
    """
    logger.info(f"Reprocessing photo {item_id} (job_id={job_id})")

    with make_session() as session:
        if job_id:
            job_utils.start_job(session, job_id)
            session.commit()

        photo = prepare_photo_for_reingest(session, item_id)
        if not photo:
            error = f"Photo {item_id} not found"
            if job_id:
                job_utils.fail_job(session, job_id, error)
                session.commit()
            return {"status": "error", "error": error}

        return execute_photo_processing(session, photo, job_id=job_id)
