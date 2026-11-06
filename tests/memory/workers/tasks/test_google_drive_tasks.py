"""Tests for Google Drive syncing tasks."""

import time
from datetime import datetime, timezone

import pytest
from unittest.mock import Mock, patch

from memory.common import settings
from memory.common.celery_app import SYNC_GOOGLE_DOC
from memory.common.db.models import GoogleDoc, JobStatus, PendingJob
from memory.common.db.models.sources import GoogleAccount, GoogleFolder
from memory.common.jobs import LOST_JOB_ERROR, MAX_TASK_STARTS, WORKER_LOST_ERROR
from memory.workers.tasks.google_drive import (
    _needs_reindex,
    _create_google_doc,
    _update_existing_doc,
    sync_google_doc,
    sync_google_folder,
)
from memory.parsers.google_drive import GoogleFileData
from memory.common.db import connection as db_connection


@pytest.fixture(autouse=True)
def reset_db_cache():
    """Reset the cached database engine between tests."""
    db_connection._engine = None
    db_connection._session_factory = None
    yield
    db_connection._engine = None
    db_connection._session_factory = None


@pytest.fixture
def sample_file_data() -> GoogleFileData:
    """Sample Google file data for testing."""
    return GoogleFileData(
        file_id="abc123",
        title="Test Document",
        mime_type="text/plain",
        original_mime_type="application/vnd.google-apps.document",
        folder_path="/My Drive/Docs",
        owner="user@example.com",
        last_modified_by="editor@example.com",
        shared_with=[],
        modified_at=datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
        created_at=datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc),
        content="This is the document content.",
        content_hash="hash123",
        size=1024,
        word_count=5,
    )


@pytest.fixture
def mock_folder():
    """Mock GoogleFolder for testing."""
    folder = Mock()
    folder.id = 1
    folder.folder_name = "Test Folder"
    folder.tags = ["test", "docs"]
    return folder


@pytest.fixture
def mock_existing_doc():
    """Mock existing GoogleDoc."""
    doc = Mock(spec=GoogleDoc)
    doc.id = 100
    doc.google_file_id = "abc123"
    doc.modality = "doc"
    doc.content_hash = "old_hash"
    doc.google_modified_at = datetime(2024, 1, 10, 10, 0, 0, tzinfo=timezone.utc)
    doc.chunks = []
    return doc


# Tests for _needs_reindex


def test_needs_reindex_true_when_hash_different(mock_existing_doc, sample_file_data):
    """Returns True when content hash differs."""
    mock_existing_doc.content_hash = "different_hash"

    assert _needs_reindex(mock_existing_doc, sample_file_data) is True


def test_needs_reindex_true_when_newer_modified_time(mock_existing_doc, sample_file_data):
    """Returns True when new data has newer modified time."""
    mock_existing_doc.content_hash = sample_file_data["content_hash"]
    mock_existing_doc.google_modified_at = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    sample_file_data["modified_at"] = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)

    assert _needs_reindex(mock_existing_doc, sample_file_data) is True


def test_needs_reindex_false_when_unchanged(mock_existing_doc, sample_file_data):
    """Returns False when content hash and time are same or older."""
    mock_existing_doc.content_hash = sample_file_data["content_hash"]
    mock_existing_doc.google_modified_at = datetime(2024, 1, 20, 0, 0, 0, tzinfo=timezone.utc)

    assert _needs_reindex(mock_existing_doc, sample_file_data) is False


def test_needs_reindex_handles_none_modified_times(mock_existing_doc, sample_file_data):
    """Handles None modified times gracefully."""
    mock_existing_doc.content_hash = sample_file_data["content_hash"]
    mock_existing_doc.google_modified_at = None
    sample_file_data["modified_at"] = None

    assert _needs_reindex(mock_existing_doc, sample_file_data) is False


# Tests for _create_google_doc


def test_create_google_doc_sets_fields(mock_folder, sample_file_data):
    """Creates GoogleDoc with correct fields."""
    doc = _create_google_doc(mock_folder, sample_file_data)

    assert doc.google_file_id == "abc123"
    assert doc.title == "Test Document"
    assert doc.modality == "doc"
    assert doc.folder_id == 1
    assert doc.owner == "user@example.com"
    assert doc.word_count == 5
    assert "gdrive" in doc.tags
    assert "Test Folder" in doc.tags


def test_create_google_doc_handles_none_folder_tags(sample_file_data):
    """Handles folder with None tags."""
    folder = Mock()
    folder.id = 1
    folder.folder_name = None
    folder.tags = None

    doc = _create_google_doc(folder, sample_file_data)

    assert "gdrive" in doc.tags


# Tests for _update_existing_doc (including null chunks fix)


@patch("memory.workers.tasks.google_drive.qdrant")
@patch("memory.workers.tasks.google_drive.process_content_item")
def test_update_existing_doc_returns_unchanged_when_no_reindex(
    mock_process, mock_qdrant, mock_folder, mock_existing_doc, sample_file_data
):
    """Returns 'unchanged' when content doesn't need reindexing."""
    mock_existing_doc.content_hash = sample_file_data["content_hash"]
    mock_existing_doc.google_modified_at = datetime(2024, 1, 20, 0, 0, 0, tzinfo=timezone.utc)
    session = Mock()

    result = _update_existing_doc(session, mock_existing_doc, mock_folder, sample_file_data)

    assert result["status"] == "unchanged"
    mock_process.assert_not_called()


@patch("memory.workers.tasks.google_drive.qdrant")
@patch("memory.workers.tasks.google_drive.process_content_item")
def test_update_existing_doc_handles_null_chunks(
    mock_process, mock_qdrant, mock_folder, mock_existing_doc, sample_file_data
):
    """Handles existing.chunks being None gracefully (null chunks fix)."""
    mock_existing_doc.chunks = None  # This is the critical case!
    mock_existing_doc.content_hash = "old_hash"  # Force reindex
    session = Mock()
    mock_process.return_value = {"status": "success"}

    # Should not raise AttributeError
    result = _update_existing_doc(session, mock_existing_doc, mock_folder, sample_file_data)

    assert result["status"] == "success"
    mock_process.assert_called_once()


@patch("memory.workers.tasks.google_drive.qdrant")
@patch("memory.workers.tasks.google_drive.process_content_item")
def test_update_existing_doc_deletes_old_chunks(
    mock_process, mock_qdrant, mock_folder, mock_existing_doc, sample_file_data
):
    """Deletes old chunks from Qdrant and database."""
    chunk1 = Mock(id=1)
    chunk2 = Mock(id=2)
    mock_existing_doc.chunks = [chunk1, chunk2]
    mock_existing_doc.content_hash = "old_hash"
    session = Mock()
    mock_process.return_value = {"status": "success"}

    mock_client = Mock()
    mock_qdrant.get_qdrant_client.return_value = mock_client

    _update_existing_doc(session, mock_existing_doc, mock_folder, sample_file_data)

    # Verify chunks were deleted from database
    session.delete.assert_any_call(chunk1)
    session.delete.assert_any_call(chunk2)

    # Verify chunks were deleted from Qdrant
    mock_qdrant.delete_points.assert_called_once_with(
        mock_client, "doc", ["1", "2"]
    )


@patch("memory.workers.tasks.google_drive.qdrant")
@patch("memory.workers.tasks.google_drive.process_content_item")
def test_update_existing_doc_updates_fields(
    mock_process, mock_qdrant, mock_folder, mock_existing_doc, sample_file_data
):
    """Updates document fields correctly."""
    mock_existing_doc.chunks = []
    mock_existing_doc.content_hash = "old_hash"
    session = Mock()
    mock_process.return_value = {"status": "success"}

    _update_existing_doc(session, mock_existing_doc, mock_folder, sample_file_data)

    assert mock_existing_doc.content == sample_file_data["content"]
    assert mock_existing_doc.title == sample_file_data["title"]
    assert mock_existing_doc.folder_path == sample_file_data["folder_path"]
    assert mock_existing_doc.word_count == sample_file_data["word_count"]
    session.flush.assert_called_once()


@patch("memory.workers.tasks.google_drive.qdrant")
@patch("memory.workers.tasks.google_drive.process_content_item")
def test_update_existing_doc_raises_on_qdrant_error(
    mock_process, mock_qdrant, mock_folder, mock_existing_doc, sample_file_data
):
    """Qdrant deletion errors are re-raised to prevent data loss.

    If Qdrant deletion fails but we continue to delete DB chunks,
    we'd end up with orphaned vectors causing duplicate search results.
    """
    chunk = Mock(id=1)
    mock_existing_doc.chunks = [chunk]
    mock_existing_doc.content_hash = "old_hash"
    session = Mock()
    mock_process.return_value = {"status": "success"}

    mock_qdrant.get_qdrant_client.side_effect = IOError("Connection failed")

    # Should raise to prevent partial state (orphaned vectors)
    with pytest.raises(IOError, match="Connection failed"):
        _update_existing_doc(session, mock_existing_doc, mock_folder, sample_file_data)


FOLDER_MIME = "application/vnd.google-apps.folder"
DOC_MIME = "application/vnd.google-apps.document"
LISTED_AT = "2024-01-15T10:30:00.000Z"


@pytest.fixture
def drive_folder(db_session, test_user) -> GoogleFolder:
    account = GoogleAccount(user_id=test_user.id, name="Drive", email="drive@example.com")
    folder = GoogleFolder(account=account, folder_id="gone", folder_name="Gone")
    db_session.add(folder)
    db_session.commit()
    return folder


@pytest.fixture
def drive_client():
    with (
        patch("memory.workers.tasks.google_drive._get_oauth_config", return_value=("id", "secret", "uri")),
        patch("memory.workers.tasks.google_drive._build_credentials"),
        patch("memory.workers.tasks.google_drive.GoogleDriveClient") as client_class,
    ):
        client = client_class.return_value
        client.get_file_metadata.return_value = {"id": "gone", "mimeType": FOLDER_MIME}
        client.get_folder_path.return_value = "/Drive"
        yield client


def listed(file_id: str, modified: str = LISTED_AT, size: str | None = None) -> tuple[dict, str]:
    meta = {"id": file_id, "name": f"{file_id}.txt", "mimeType": DOC_MIME, "modifiedTime": modified}
    if size is not None:
        meta["size"] = size
    return meta, "/Drive"


def dispatched_files(send_task) -> list[str]:
    return sorted(
        call.kwargs["kwargs"]["file_metadata"]["id"]
        for call in send_task.call_args_list
        if call.args[0] == SYNC_GOOGLE_DOC
    )


def doc_sync_job(db_session) -> PendingJob:
    return db_session.query(PendingJob).filter(PendingJob.job_type == "sync_google_doc").one()


def test_sync_google_folder_records_failure_on_the_folder(drive_client, drive_folder, db_session):
    drive_client.get_file_metadata.side_effect = Exception("<HttpError 404 File not found>")

    with pytest.raises(Exception):
        sync_google_folder(drive_folder.id)
    db_session.expire_all()

    assert drive_folder.sync_error == "<HttpError 404 File not found>"
    assert drive_folder.sync_failing_since is not None
    assert drive_folder.account.sync_error is None


def test_sync_google_folder_queues_a_doc_sync_per_file_without_fetching_any(
    drive_client, drive_folder, no_celery_dispatch
):
    drive_client.list_files_in_folder.return_value = [listed("f1"), listed("f2")]

    result = sync_google_folder(drive_folder.id)

    assert result["docs_synced"] == 2
    assert dispatched_files(no_celery_dispatch) == ["f1", "f2"]
    drive_client.fetch_file.assert_not_called()
    drive_client.export_file.assert_not_called()


def test_sync_google_folder_queues_a_single_document(drive_client, drive_folder, no_celery_dispatch):
    drive_client.get_file_metadata.return_value = listed("gone")[0]

    result = sync_google_folder(drive_folder.id)

    assert result["is_single_doc"] is True
    assert dispatched_files(no_celery_dispatch) == ["gone"]
    drive_client.fetch_file.assert_not_called()


@pytest.mark.parametrize(
    "stored_modified, force_full, expected",
    [
        (datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc), False, []),
        (datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc), True, ["f1"]),
        (datetime(2024, 1, 10, tzinfo=timezone.utc), False, ["f1"]),
        (None, False, ["f1"]),
    ],
)
def test_sync_google_folder_queues_files_not_stored_at_their_listed_version(
    stored_modified, force_full, expected, drive_client, drive_folder, db_session, no_celery_dispatch
):
    db_session.add(
        GoogleDoc(
            sha256=b"f1".ljust(32, b"0"),
            modality="doc",
            google_file_id="f1",
            google_modified_at=stored_modified,
            title="f1",
            folder=drive_folder,
        )
    )
    db_session.commit()
    drive_client.list_files_in_folder.return_value = [listed("f1")]

    sync_google_folder(drive_folder.id, force_full=force_full)

    assert dispatched_files(no_celery_dispatch) == expected


@pytest.mark.parametrize(
    "status, error, attempts, expected",
    [
        (JobStatus.PENDING, None, 0, []),
        (JobStatus.PROCESSING, None, 1, []),
        (JobStatus.COMPLETE, None, 1, []),
        (JobStatus.FAILED, "<HttpError 500 Backend Error>", 1, ["f1"]),
        (JobStatus.FAILED, LOST_JOB_ERROR, 1, ["f1"]),
        (JobStatus.FAILED, LOST_JOB_ERROR, MAX_TASK_STARTS, []),
        (JobStatus.FAILED, WORKER_LOST_ERROR, MAX_TASK_STARTS, []),
    ],
)
def test_sync_google_folder_requeues_a_version_only_if_its_doc_sync_failed(
    status, error, attempts, expected, drive_client, drive_folder, db_session, no_celery_dispatch
):
    drive_client.list_files_in_folder.return_value = [listed("f1")]
    sync_google_folder(drive_folder.id)
    job = doc_sync_job(db_session)
    job.status = status.value
    job.error_message = error
    job.attempts = attempts
    db_session.commit()
    no_celery_dispatch.reset_mock()

    sync_google_folder(drive_folder.id)

    assert dispatched_files(no_celery_dispatch) == expected


@pytest.mark.parametrize("status", [JobStatus.PENDING, JobStatus.COMPLETE])
def test_sync_google_folder_queues_a_new_version_of_an_already_queued_file(
    status, drive_client, drive_folder, db_session, no_celery_dispatch
):
    drive_client.list_files_in_folder.return_value = [listed("f1")]
    sync_google_folder(drive_folder.id)
    doc_sync_job(db_session).status = status.value
    db_session.commit()
    no_celery_dispatch.reset_mock()
    drive_client.list_files_in_folder.return_value = [listed("f1", modified="2024-02-01T00:00:00.000Z")]

    sync_google_folder(drive_folder.id)

    assert dispatched_files(no_celery_dispatch) == ["f1"]


@pytest.mark.parametrize("size, expected", [("1001", []), ("1000", ["f1"]), (None, ["f1"])])
def test_sync_google_folder_skips_files_over_the_size_limit(
    size, expected, monkeypatch, drive_client, drive_folder, no_celery_dispatch
):
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_MAX_FILE_BYTES", 1000)
    drive_client.list_files_in_folder.return_value = [listed("f1", size=size)]

    sync_google_folder(drive_folder.id)

    assert dispatched_files(no_celery_dispatch) == expected


def slow(result=None, error=None):
    """A Drive call that outlasts the patched idle-in-transaction timeout."""

    def call(*_args, **_kwargs):
        time.sleep(0.5)
        if error:
            raise error
        return result

    return call


@pytest.fixture
def short_idle_timeout(monkeypatch):
    monkeypatch.setattr(db_connection, "IDLE_IN_TRANSACTION_TIMEOUT_MS", 100)


@pytest.mark.transactional_db
@pytest.mark.parametrize("mime_type", [FOLDER_MIME, DOC_MIME])
def test_sync_google_folder_completes_when_drive_outlasts_idle_timeout(
    mime_type, short_idle_timeout, drive_client, drive_folder, db_session, no_celery_dispatch
):
    drive_client.get_file_metadata.return_value = {"id": "gone", "mimeType": mime_type}
    drive_client.list_files_in_folder.side_effect = slow(result=[listed("f1")])
    drive_client.get_folder_path.side_effect = slow(result="/Drive")

    result = sync_google_folder(drive_folder.id)
    db_session.expire_all()

    assert result["status"] == "completed"
    assert result["docs_synced"] == 1
    assert drive_folder.last_sync_at is not None
    assert drive_folder.sync_error is None


@pytest.mark.transactional_db
def test_sync_google_folder_records_failure_after_idle_timeout(
    short_idle_timeout, drive_client, drive_folder, db_session
):
    drive_client.get_file_metadata.side_effect = slow(error=Exception("<HttpError 500 Backend Error>"))

    with pytest.raises(Exception, match="Backend Error"):
        sync_google_folder(drive_folder.id)
    db_session.expire_all()

    assert drive_folder.sync_error == "<HttpError 500 Backend Error>"
    assert drive_folder.sync_failing_since is not None


def sync_listed_doc(folder: GoogleFolder) -> dict:
    meta, path = listed("abc123")
    return sync_google_doc(folder_id=folder.id, file_metadata=meta, folder_path=path)


@pytest.mark.transactional_db
@patch("memory.workers.tasks.google_drive.process_content_item", return_value={"status": "processed"})
def test_sync_google_doc_fetches_and_stores_the_file(
    mock_process, short_idle_timeout, drive_client, drive_folder, sample_file_data
):
    drive_client.fetch_file.side_effect = slow(result=sample_file_data)

    result = sync_listed_doc(drive_folder)

    assert result == {"status": "processed"}
    drive_client.fetch_file.assert_called_once_with(*listed("abc123"))
    stored = mock_process.call_args.args[0]
    assert (stored.google_file_id, stored.content) == ("abc123", sample_file_data["content"])


@patch("memory.workers.tasks.google_drive.process_content_item")
def test_sync_google_doc_skips_a_file_without_text(mock_process, drive_client, drive_folder):
    drive_client.fetch_file.return_value = None

    result = sync_listed_doc(drive_folder)

    assert result["status"] == "skipped"
    mock_process.assert_not_called()
