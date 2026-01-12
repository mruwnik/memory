"""
Tests for photo processing tasks.
"""

import hashlib
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch, mock_open

import pytest
from PIL import Image

from memory.workers.tasks import photo


# Test extract_exif_data


@patch("memory.workers.tasks.photo.Image.open")
def test_extract_exif_successfully(mock_open):
    mock_img = MagicMock()
    mock_img._getexif.return_value = {
        271: "Canon",  # Make
        272: "Canon EOS 5D",  # Model
        306: "2023:01:15 14:30:45",  # DateTime
    }
    mock_open.return_value.__enter__.return_value = mock_img

    result = photo.extract_exif_data(Path("/test/photo.jpg"))

    assert "Make" in result
    assert "Model" in result
    assert "DateTime" in result


@patch("memory.workers.tasks.photo.Image.open")
def test_extract_exif_returns_empty_dict_when_no_exif(mock_open):
    mock_img = MagicMock()
    mock_img._getexif.return_value = None
    mock_open.return_value.__enter__.return_value = mock_img

    result = photo.extract_exif_data(Path("/test/photo.jpg"))

    assert result == {}


@patch("memory.workers.tasks.photo.Image.open")
def test_extract_exif_handles_image_open_error(mock_open):
    mock_open.side_effect = IOError("Cannot open image")

    result = photo.extract_exif_data(Path("/test/photo.jpg"))

    assert result == {}


@patch("memory.workers.tasks.photo.Image.open")
def test_extract_exif_handles_extraction_error(mock_open):
    mock_img = MagicMock()
    mock_img._getexif.side_effect = AttributeError("No EXIF")
    mock_open.return_value.__enter__.return_value = mock_img

    result = photo.extract_exif_data(Path("/test/photo.jpg"))

    assert result == {}


# Test parse_exif_datetime


@pytest.mark.parametrize(
    "field_name",
    ["DateTimeOriginal", "DateTime", "DateTimeDigitized"],
)
def test_parse_exif_datetime_from_field(field_name):
    exif_data = {field_name: "2023:01:15 14:30:45"}
    result = photo.parse_exif_datetime(exif_data)

    assert result == datetime(2023, 1, 15, 14, 30, 45)


def test_parse_exif_datetime_prioritizes_original():
    exif_data = {
        "DateTimeOriginal": "2023:01:15 14:30:45",
        "DateTime": "2023:01:16 10:00:00",
        "DateTimeDigitized": "2023:01:17 12:00:00",
    }
    result = photo.parse_exif_datetime(exif_data)

    # Should use DateTimeOriginal
    assert result == datetime(2023, 1, 15, 14, 30, 45)


def test_parse_exif_datetime_falls_back_to_datetime():
    exif_data = {
        "DateTime": "2023:01:16 10:00:00",
        "DateTimeDigitized": "2023:01:17 12:00:00",
    }
    result = photo.parse_exif_datetime(exif_data)

    # Should use DateTime when DateTimeOriginal is missing
    assert result == datetime(2023, 1, 16, 10, 0, 0)


def test_parse_exif_datetime_returns_none_when_no_datetime():
    exif_data = {"Make": "Canon", "Model": "EOS 5D"}
    result = photo.parse_exif_datetime(exif_data)

    assert result is None


@pytest.mark.parametrize(
    "invalid_value",
    ["invalid-format", None, "", 12345],
)
def test_parse_exif_datetime_handles_invalid_values(invalid_value):
    exif_data = {"DateTimeOriginal": invalid_value}
    result = photo.parse_exif_datetime(exif_data)

    assert result is None


# Test get_camera_info


def test_get_camera_info_returns_make_and_model():
    exif_data = {"Make": "Canon", "Model": "EOS 5D"}
    result = photo.get_camera_info(exif_data)

    assert result == "Canon EOS 5D"


def test_get_camera_info_avoids_duplicate_make():
    # Some cameras include make in model string
    exif_data = {"Make": "Canon", "Model": "Canon EOS 5D"}
    result = photo.get_camera_info(exif_data)

    # Should not duplicate "Canon"
    assert result == "Canon EOS 5D"


def test_get_camera_info_model_only():
    exif_data = {"Model": "iPhone 12 Pro"}
    result = photo.get_camera_info(exif_data)

    assert result == "iPhone 12 Pro"


def test_get_camera_info_make_only():
    exif_data = {"Make": "Canon"}
    result = photo.get_camera_info(exif_data)

    assert result == "Canon"


def test_get_camera_info_returns_none_when_neither_present():
    exif_data = {"DateTime": "2023:01:15 14:30:45"}
    result = photo.get_camera_info(exif_data)

    assert result is None


@pytest.mark.parametrize(
    "make,model,expected",
    [
        ("", "EOS 5D", "EOS 5D"),
        ("Canon", "", "Canon"),
        ("", "", None),
    ],
)
def test_get_camera_info_handles_empty_strings(make, model, expected):
    exif_data = {"Make": make, "Model": model}
    result = photo.get_camera_info(exif_data)

    assert result == expected


# Test get_gps_coordinates


def test_get_gps_coordinates_north_east():
    exif_data = {
        "GPSInfo": {
            2: (40, 45, 30),  # Latitude 40°45'30"
            1: "N",  # North
            4: (73, 59, 15),  # Longitude 73°59'15"
            3: "E",  # East
        }
    }
    lat, lon = photo.get_gps_coordinates(exif_data)

    assert lat == pytest.approx(40.758333, rel=1e-5)
    assert lon == pytest.approx(73.9875, rel=1e-5)


def test_get_gps_coordinates_south_latitude():
    exif_data = {
        "GPSInfo": {
            2: (33, 52, 0),
            1: "S",  # South
            4: (151, 12, 0),
            3: "E",
        }
    }
    lat, lon = photo.get_gps_coordinates(exif_data)

    # South latitude should be negative
    assert lat == pytest.approx(-33.866667, rel=1e-5)
    assert lon == pytest.approx(151.2, rel=1e-5)


def test_get_gps_coordinates_west_longitude():
    exif_data = {
        "GPSInfo": {
            2: (37, 46, 30),
            1: "N",
            4: (122, 25, 10),
            3: "W",  # West
        }
    }
    lat, lon = photo.get_gps_coordinates(exif_data)

    assert lat == pytest.approx(37.775, rel=1e-5)
    # West longitude should be negative
    assert lon == pytest.approx(-122.419444, rel=1e-5)


@pytest.mark.parametrize(
    "exif_data",
    [
        {"Make": "Canon"},  # No GPSInfo
        {"GPSInfo": {}},  # Empty GPSInfo
    ],
)
def test_get_gps_coordinates_returns_none_when_missing(exif_data):
    lat, lon = photo.get_gps_coordinates(exif_data)

    assert lat is None
    assert lon is None


def test_get_gps_coordinates_handles_invalid_format():
    exif_data = {
        "GPSInfo": {
            2: "invalid",  # Not a tuple
            1: "N",
            4: (73, 59, 15),
            3: "E",
        }
    }
    lat, lon = photo.get_gps_coordinates(exif_data)

    assert lat is None
    # Longitude should still work
    assert lon == pytest.approx(73.9875, rel=1e-5)


def test_get_gps_coordinates_missing_latitude():
    exif_data = {
        "GPSInfo": {
            4: (73, 59, 15),
            3: "E",
        }
    }
    lat, lon = photo.get_gps_coordinates(exif_data)

    assert lat is None
    assert lon == pytest.approx(73.9875, rel=1e-5)


def test_get_gps_coordinates_missing_longitude():
    exif_data = {
        "GPSInfo": {
            2: (40, 45, 30),
            1: "N",
        }
    }
    lat, lon = photo.get_gps_coordinates(exif_data)

    assert lat == pytest.approx(40.758333, rel=1e-5)
    assert lon is None


def test_get_gps_coordinates_decimal_seconds():
    exif_data = {
        "GPSInfo": {
            2: (40, 45, 30.5),  # Half second
            1: "N",
            4: (73, 59, 15.25),
            3: "E",
        }
    }
    lat, lon = photo.get_gps_coordinates(exif_data)

    assert lat == pytest.approx(40.758472, rel=1e-5)
    assert lon == pytest.approx(73.987569, rel=1e-5)


# Test prepare_photo_for_reingest


@patch("memory.workers.tasks.photo.clear_item_chunks")
def test_prepare_photo_for_reingest_clears_chunks(mock_clear):
    mock_session = MagicMock()
    mock_photo = MagicMock()
    mock_session.get.return_value = mock_photo

    result = photo.prepare_photo_for_reingest(mock_session, 123)

    assert result is mock_photo
    mock_clear.assert_called_once_with(mock_photo, mock_session)
    mock_session.flush.assert_called_once()


def test_prepare_photo_for_reingest_returns_none_when_not_found():
    mock_session = MagicMock()
    mock_session.get.return_value = None

    result = photo.prepare_photo_for_reingest(mock_session, 999)

    assert result is None


# Test validate_and_parse_photo


@patch("memory.workers.tasks.photo.extract_exif_data")
@patch("memory.workers.tasks.photo.Path")
def test_validate_and_parse_photo_absolute_path(mock_path_class, mock_extract):
    mock_path = MagicMock()
    mock_path.is_absolute.return_value = True
    mock_path.exists.return_value = True
    mock_path.read_bytes.return_value = b"fake image data"
    mock_path_class.return_value = mock_path

    mock_extract.return_value = {"Make": "Canon"}

    result_path, content, exif = photo.validate_and_parse_photo("/absolute/path/photo.jpg")

    assert content == b"fake image data"
    assert exif == {"Make": "Canon"}


@patch("memory.workers.tasks.photo.extract_exif_data")
@patch("memory.workers.tasks.photo.settings")
def test_validate_and_parse_photo_relative_path(mock_settings, mock_extract):
    # Create a mock path that looks absolute after resolution
    mock_storage_dir = MagicMock()
    mock_resolved = MagicMock()
    mock_resolved.is_absolute.return_value = True  # After resolution, it's absolute
    mock_resolved.exists.return_value = True
    mock_resolved.read_bytes.return_value = b"image"

    mock_storage_dir.__truediv__ = MagicMock(return_value=mock_resolved)
    mock_settings.PHOTO_STORAGE_DIR = mock_storage_dir

    mock_extract.return_value = {}

    with patch("memory.workers.tasks.photo.Path") as mock_path:
        # First call creates relative path object
        mock_path.return_value.is_absolute.return_value = False
        result_path, content, exif = photo.validate_and_parse_photo("relative/photo.jpg")

    assert content == b"image"


@patch("memory.workers.tasks.photo.Path")
def test_validate_and_parse_photo_raises_when_not_found(mock_path_class):
    mock_path = MagicMock()
    mock_path.is_absolute.return_value = True
    mock_path.exists.return_value = False
    mock_path_class.return_value = mock_path

    with pytest.raises(FileNotFoundError, match="Photo file not found"):
        photo.validate_and_parse_photo("/nonexistent/photo.jpg")


# Test create_photo_from_file


@patch("memory.workers.tasks.photo.get_gps_coordinates")
@patch("memory.workers.tasks.photo.get_camera_info")
@patch("memory.workers.tasks.photo.parse_exif_datetime")
@patch("memory.workers.tasks.photo.settings")
def test_create_photo_from_file_with_all_exif(
    mock_settings, mock_parse_dt, mock_camera, mock_gps
):
    mock_settings.FILE_STORAGE_DIR = Path("/storage")
    mock_parse_dt.return_value = datetime(2023, 1, 15, 14, 30, 45)
    mock_camera.return_value = "Canon EOS 5D"
    mock_gps.return_value = (40.7589, -73.9851)

    path = Path("/storage/photos/vacation.jpg")
    content = b"fake jpg content"
    exif_data = {"Make": "Canon", "Model": "EOS 5D"}
    tags = ["vacation", "2023"]

    result = photo.create_photo_from_file(path, content, exif_data, tags)

    assert result.filename == "photos/vacation.jpg"
    assert result.mime_type == "image/jpeg"
    assert result.modality == "photo"
    assert result.size == len(content)
    assert result.tags == tags
    assert result.exif_taken_at == datetime(2023, 1, 15, 14, 30, 45)
    assert result.exif_lat == 40.7589
    assert result.exif_lon == -73.9851
    assert result.camera == "Canon EOS 5D"
    assert result.embed_status == "RAW"


@pytest.mark.parametrize(
    "extension,expected_mime",
    [
        (".jpg", "image/jpeg"),
        (".jpeg", "image/jpeg"),
        (".png", "image/png"),
        (".gif", "image/gif"),
        (".webp", "image/webp"),
        (".heic", "image/heic"),
        (".heif", "image/heif"),
        (".unknown", "image/jpeg"),  # Default
    ],
)
@patch("memory.workers.tasks.photo.settings")
def test_create_photo_from_file_mime_types(mock_settings, extension, expected_mime):
    mock_settings.FILE_STORAGE_DIR = Path("/storage")
    path = Path(f"/storage/image{extension}")
    content = b"data"

    result = photo.create_photo_from_file(path, content, {}, [])

    assert result.mime_type == expected_mime


@patch("memory.workers.tasks.photo.settings")
def test_create_photo_from_file_outside_storage_dir(mock_settings):
    mock_settings.FILE_STORAGE_DIR = Path("/storage")
    path = Path("/other/location/photo.jpg")
    content = b"data"

    result = photo.create_photo_from_file(path, content, {}, [])

    # Should use just the filename
    assert result.filename == "photo.jpg"


@patch("memory.workers.tasks.photo.settings")
def test_create_photo_from_file_computes_sha256(mock_settings):
    mock_settings.FILE_STORAGE_DIR = Path("/storage")
    path = Path("/storage/photo.jpg")
    content = b"test content"

    result = photo.create_photo_from_file(path, content, {}, [])

    expected_hash = hashlib.sha256(content).digest()
    assert result.sha256 == expected_hash


# Test execute_photo_processing


@patch("memory.workers.tasks.photo.job_utils")
@patch("memory.workers.tasks.photo.process_content_item")
def test_execute_photo_processing_success(mock_process, mock_job_utils):
    mock_session = MagicMock()
    mock_photo = MagicMock()
    mock_photo.id = 123
    mock_photo.filename = "test.jpg"

    mock_process.return_value = {"status": "created", "photo_id": 123}

    result = photo.execute_photo_processing(mock_session, mock_photo)

    assert result == {"status": "created", "photo_id": 123}
    mock_process.assert_called_once_with(mock_photo, mock_session)
    mock_session.commit.assert_called_once()


@patch("memory.workers.tasks.photo.job_utils")
@patch("memory.workers.tasks.photo.process_content_item")
def test_execute_photo_processing_completes_job(mock_process, mock_job_utils):
    mock_session = MagicMock()
    mock_photo = MagicMock()
    mock_photo.id = 123

    mock_process.return_value = {"status": "created"}

    photo.execute_photo_processing(mock_session, mock_photo, job_id=456)

    mock_job_utils.complete_job.assert_called_once_with(
        mock_session, 456, result_id=123, result_type="Photo"
    )


@patch("memory.workers.tasks.photo.job_utils")
@patch("memory.workers.tasks.photo.process_content_item")
def test_execute_photo_processing_handles_error(mock_process, mock_job_utils):
    mock_session = MagicMock()
    mock_photo = MagicMock()
    mock_photo.id = 123

    mock_process.side_effect = ValueError("Processing failed")

    result = photo.execute_photo_processing(mock_session, mock_photo)

    assert result["status"] == "error"
    assert "Processing failed" in result["error"]
    assert result["photo_id"] == 123
    mock_session.rollback.assert_called_once()


@patch("memory.workers.tasks.photo.job_utils")
@patch("memory.workers.tasks.photo.process_content_item")
def test_execute_photo_processing_fails_job_on_error(mock_process, mock_job_utils):
    mock_session = MagicMock()
    mock_photo = MagicMock()
    mock_photo.id = 123

    mock_process.side_effect = ValueError("Processing failed")

    photo.execute_photo_processing(mock_session, mock_photo, job_id=456)

    mock_job_utils.fail_job.assert_called_once()
    assert mock_session.commit.call_count == 1


# Test sync_photo


@patch("memory.workers.tasks.photo.execute_photo_processing")
@patch("memory.workers.tasks.photo.create_photo_from_file")
@patch("memory.workers.tasks.photo.check_content_exists")
@patch("memory.workers.tasks.photo.validate_and_parse_photo")
@patch("memory.workers.tasks.photo.job_utils")
@patch("memory.workers.tasks.photo.make_session")
def test_sync_photo_creates_new(
    mock_make_session,
    mock_job_utils,
    mock_validate,
    mock_check,
    mock_create,
    mock_execute,
):
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_path = Path("/storage/photo.jpg")
    mock_content = b"image data"
    mock_exif = {"Make": "Canon"}
    mock_validate.return_value = (mock_path, mock_content, mock_exif)

    mock_check.return_value = None  # No existing photo

    mock_photo = MagicMock()
    mock_create.return_value = mock_photo

    mock_execute.return_value = {"status": "created", "photo_id": 123}

    result = photo.sync_photo("/storage/photo.jpg", tags=["test"])

    assert result == {"status": "created", "photo_id": 123}
    mock_session.add.assert_called_once_with(mock_photo)
    mock_execute.assert_called_once()


@patch("memory.workers.tasks.photo.check_content_exists")
@patch("memory.workers.tasks.photo.validate_and_parse_photo")
@patch("memory.workers.tasks.photo.job_utils")
@patch("memory.workers.tasks.photo.make_session")
def test_sync_photo_returns_existing(
    mock_make_session, mock_job_utils, mock_validate, mock_check
):
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_validate.return_value = (Path("/test.jpg"), b"data", {})

    existing_photo = MagicMock()
    existing_photo.id = 456
    existing_photo.filename = "existing.jpg"
    mock_check.return_value = existing_photo

    result = photo.sync_photo("/test.jpg")

    assert result == {"status": "already_exists", "photo_id": 456}


@patch("memory.workers.tasks.photo.validate_and_parse_photo")
@patch("memory.workers.tasks.photo.job_utils")
@patch("memory.workers.tasks.photo.make_session")
def test_sync_photo_starts_job(mock_make_session, mock_job_utils, mock_validate):
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session
    mock_validate.return_value = (Path("/test.jpg"), b"data", {})

    existing = MagicMock()
    existing.id = 123

    with patch("memory.workers.tasks.photo.check_content_exists", return_value=existing):
        photo.sync_photo("/test.jpg", job_id=789)

    mock_job_utils.start_job.assert_called_once_with(mock_session, 789)


# Test reprocess_photo


@patch("memory.workers.tasks.photo.execute_photo_processing")
@patch("memory.workers.tasks.photo.prepare_photo_for_reingest")
@patch("memory.workers.tasks.photo.job_utils")
@patch("memory.workers.tasks.photo.make_session")
def test_reprocess_photo_success(
    mock_make_session, mock_job_utils, mock_prepare, mock_execute
):
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_photo = MagicMock()
    mock_prepare.return_value = mock_photo

    mock_execute.return_value = {"status": "reprocessed", "photo_id": 123}

    result = photo.reprocess_photo(123)

    assert result == {"status": "reprocessed", "photo_id": 123}
    mock_prepare.assert_called_once_with(mock_session, 123)
    mock_execute.assert_called_once_with(mock_session, mock_photo, job_id=None)


@patch("memory.workers.tasks.photo.prepare_photo_for_reingest")
@patch("memory.workers.tasks.photo.job_utils")
@patch("memory.workers.tasks.photo.make_session")
def test_reprocess_photo_not_found(mock_make_session, mock_job_utils, mock_prepare):
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_prepare.return_value = None

    result = photo.reprocess_photo(999)

    assert result["status"] == "error"
    assert "not found" in result["error"]


@patch("memory.workers.tasks.photo.prepare_photo_for_reingest")
@patch("memory.workers.tasks.photo.job_utils")
@patch("memory.workers.tasks.photo.make_session")
def test_reprocess_photo_fails_job_when_not_found(
    mock_make_session, mock_job_utils, mock_prepare
):
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_prepare.return_value = None

    photo.reprocess_photo(999, job_id=456)

    mock_job_utils.fail_job.assert_called_once()
    # Check the error message (third positional arg: session, job_id, error_message)
    call_args = mock_job_utils.fail_job.call_args[0]
    assert "not found" in call_args[2].lower()
