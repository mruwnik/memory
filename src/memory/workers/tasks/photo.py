"""Photo processing tasks."""

import hashlib
import logging
from pathlib import Path
from datetime import datetime

from PIL import Image
from PIL.ExifTags import TAGS

from memory.common import settings
from memory.common.celery_app import SYNC_PHOTO, app
from memory.common.db.connection import make_session
from memory.common.db.models import Photo
from memory.workers.tasks.content_processing import (
    check_content_exists,
    process_content_item,
    safe_task_execution,
)

logger = logging.getLogger(__name__)


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


@app.task(name=SYNC_PHOTO)
@safe_task_execution
def sync_photo(
    file_path: str,
    tags: list[str] | None = None,
) -> dict:
    """
    Process a photo file and add it to the knowledge base.

    Args:
        file_path: Path to the photo file (absolute or relative to PHOTO_STORAGE_DIR)
        tags: Optional list of tags to apply

    Returns:
        dict: Summary of what was processed
    """
    tags = tags or []
    path = Path(file_path)

    # Resolve relative paths
    if not path.is_absolute():
        path = settings.PHOTO_STORAGE_DIR / path

    if not path.exists():
        raise FileNotFoundError(f"Photo file not found: {path}")

    logger.info(f"Processing photo: {path}")

    # Read file and compute hash
    content = path.read_bytes()
    sha256 = hashlib.sha256(content).digest()

    # Extract EXIF metadata
    exif_data = extract_exif_data(path)
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

    with make_session() as session:
        # Check for existing photo
        existing = check_content_exists(session, Photo, sha256=sha256)
        if existing:
            logger.info(f"Photo already exists: {existing.filename}")
            return {"status": "already_exists", "photo_id": existing.id}

        photo = Photo(
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
        return process_content_item(photo, session)
