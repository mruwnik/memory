"""Celery tasks for Google Drive document syncing."""

import logging
from datetime import datetime, timezone
from itertools import batched
from typing import Any, cast

from memory.common import settings
from memory.common.celery_app import SYNC_GOOGLE_DOC, SYNC_GOOGLE_FOLDER, app
from memory.common.db.connection import make_session
from memory.common.db.models import GoogleDoc, JobStatus
from memory.common.db.models.sources import GoogleAccount, GoogleFolder
from memory.parsers.google_drive import (
    GoogleDriveClient,
    GoogleCredentials,
    GoogleFileData,
    parse_google_date,
    refresh_credentials,
    _get_oauth_config,
)
from memory.common.content_processing import (
    create_content_hash,
    delete_chunk_vectors,
    create_task_result,
    process_content_item,
)
from memory.common.jobs import dispatch_job, get_job_by_external_id, killed_its_worker, tracked_task
from memory.common.people import link_people

logger = logging.getLogger(__name__)

FOLDER_MIME = "application/vnd.google-apps.folder"
DOC_SYNC_JOB = "sync_google_doc"


def _build_credentials(account: GoogleAccount, session) -> GoogleCredentials:
    """Build credentials from account, refreshing if needed."""
    credentials = refresh_credentials(account, session)
    return GoogleCredentials(
        access_token=credentials.token,
        refresh_token=credentials.refresh_token,
        token_expires_at=credentials.expiry,
        scopes=list(credentials.scopes or []),
    )


def _needs_reindex(existing: GoogleDoc, new_data: GoogleFileData) -> bool:
    """Check if an existing document needs reindexing."""
    # Compare content hash
    if existing.content_hash != new_data["content_hash"]:
        return True

    # Check if modified time is newer
    existing_modified = cast(datetime | None, existing.google_modified_at)
    if existing_modified and new_data["modified_at"]:
        if new_data["modified_at"] > existing_modified:
            return True

    return False


def _create_google_doc(
    folder: GoogleFolder,
    file_data: GoogleFileData,
) -> GoogleDoc:
    """Create a GoogleDoc from parsed file data."""
    folder_tags = cast(list[str], folder.tags) or []

    # Auto-add source and folder tags for filtering
    auto_tags = ["gdrive"]
    if folder_name := cast(str | None, folder.folder_name):
        auto_tags.append(folder_name)

    return GoogleDoc(
        modality="doc",
        sha256=create_content_hash(file_data["content"]),
        content=file_data["content"],
        google_file_id=file_data["file_id"],
        title=file_data["title"],
        filename=file_data["title"],
        original_mime_type=file_data["original_mime_type"],
        folder_id=folder.id,
        folder_path=file_data["folder_path"],
        owner=file_data["owner"],
        last_modified_by=file_data["last_modified_by"],
        google_modified_at=file_data["modified_at"],
        word_count=file_data["word_count"],
        content_hash=file_data["content_hash"],
        tags=auto_tags + folder_tags,
        size=file_data["size"],
        mime_type=file_data["mime_type"],
    )


def _update_existing_doc(
    session: Any,
    existing: GoogleDoc,
    folder: GoogleFolder,
    file_data: GoogleFileData,
) -> dict[str, Any]:
    """Update an existing GoogleDoc and reindex if content changed."""
    if not _needs_reindex(existing, file_data):
        return create_task_result(existing, "unchanged")

    logger.info(f"Content changed for {file_data['title']}, reindexing")

    # Delete old chunks from Qdrant
    existing_chunks = existing.chunks or []
    if existing_chunks:
        try:
            delete_chunk_vectors(existing_chunks)
        except IOError:
            # Re-raise to fail the task - leaving stale vectors causes duplicate results
            logger.exception("Error deleting chunks from Qdrant")
            raise

    # Delete chunks from database (clear before delete to avoid SQLAlchemy issues)
    chunks_to_delete = list(existing_chunks)
    if existing.chunks is not None:
        existing.chunks.clear()
    for chunk in chunks_to_delete:
        session.delete(chunk)

    # Update the existing item
    existing.content = file_data["content"]
    existing.sha256 = create_content_hash(file_data["content"])
    existing.title = file_data["title"]
    existing.filename = file_data["title"]
    existing.google_modified_at = file_data["modified_at"]
    existing.last_modified_by = file_data["last_modified_by"]
    existing.word_count = file_data["word_count"]
    existing.content_hash = file_data["content_hash"]
    existing.size = file_data["size"]
    existing.folder_path = file_data["folder_path"]

    # Update tags
    folder_tags = cast(list[str], folder.tags) or []
    existing.tags = folder_tags

    session.flush()

    return process_content_item(existing, session)


def drive_client(session: Any, credentials: GoogleCredentials) -> GoogleDriveClient:
    client_id, client_secret, token_uri = _get_oauth_config(session)
    return GoogleDriveClient(
        credentials,
        client_id=client_id,
        client_secret=client_secret,
        token_uri=token_uri,
    )


@app.task(name=SYNC_GOOGLE_DOC)
@tracked_task
def sync_google_doc(
    folder_id: int,
    file_metadata: dict[str, Any],
    folder_path: str | None,
) -> dict[str, Any]:
    """Download one Drive file, as listed by its folder's sync, and store it."""
    with make_session() as session:
        folder = session.get(GoogleFolder, folder_id)
        if not folder or not folder.account:
            return {"status": "error", "error": "Folder not found"}

        client = drive_client(session, _build_credentials(folder.account, session))
        # A large download outlasts Postgres' idle_in_transaction_session_timeout.
        session.commit()
        file_data = client.fetch_file(file_metadata, folder_path)
        if not file_data:
            return {"status": "skipped", "error": "No text could be extracted"}

        existing = (
            session.query(GoogleDoc)
            .filter(GoogleDoc.google_file_id == file_data["file_id"])
            .first()
        )
        if existing:
            return _update_existing_doc(session, existing, folder, file_data)

        google_doc = _create_google_doc(folder, file_data)

        # LIMITATION: Person associations are only set on document creation.
        # Users added to document permissions after initial sync won't be linked,
        # meaning person-filtered searches may miss content they should see.
        # TODO(#50): Add permission reconciliation in _update_existing_doc or via periodic task
        emails = set(file_data.get("shared_with", []))
        if owner := file_data.get("owner"):
            emails.add(owner)
        link_people(session, google_doc, emails)

        return process_content_item(google_doc, session)


def stored_versions(session: Any, file_ids: list[str]) -> dict[str, datetime | None]:
    """google_modified_at of the stored docs among file_ids."""
    return {
        file_id: modified
        for batch in batched(file_ids, 1000)
        for file_id, modified in session.query(GoogleDoc.google_file_id, GoogleDoc.google_modified_at)
        .filter(GoogleDoc.google_file_id.in_(batch))
        .all()
    }


def is_stored(stored: dict[str, datetime | None], meta: dict[str, Any]) -> bool:
    stored_at = stored.get(meta["id"])
    listed_at = parse_google_date(meta.get("modifiedTime"))
    return bool(stored_at and listed_at and listed_at <= stored_at)


def is_too_large(meta: dict[str, Any]) -> bool:
    # Google-native files list no size; Drive caps their exports itself.
    return int(meta.get("size") or 0) > settings.GOOGLE_DRIVE_MAX_FILE_BYTES


def queue_doc_sync(
    session: Any, folder_id: int, meta: dict[str, Any], folder_path: str | None, force_full: bool
) -> bool:
    """Queue a sync of this version of the file, unless one is queued, done, or keeps killing its worker.

    A failed sync is queued again, so the next folder sync redoes it.
    """
    version = {"id": meta["id"], "modifiedTime": meta.get("modifiedTime")}
    external_id = f"gdrive:{folder_id}:{version['id']}:{version['modifiedTime']}"
    previous = get_job_by_external_id(session, external_id, DOC_SYNC_JOB, user_id=None)
    previous_status = previous.status if previous else None
    if previous_status in (JobStatus.PENDING.value, JobStatus.PROCESSING.value):
        return False
    if previous_status == JobStatus.COMPLETE.value and not force_full:
        return False
    if previous_status == JobStatus.FAILED.value and killed_its_worker(
        session, DOC_SYNC_JOB, folder_id=folder_id, file_metadata=version
    ):
        logger.warning("Not queueing Drive file %s: it keeps killing its worker", version["id"])
        return False

    dispatch_job(
        session,
        job_type=DOC_SYNC_JOB,
        task_name=SYNC_GOOGLE_DOC,
        task_kwargs={"folder_id": folder_id, "file_metadata": meta, "folder_path": folder_path},
        # A forced resync redoes completed versions, which their external_id would refuse.
        external_id=None if force_full else external_id,
    )
    return True


@app.task(name=SYNC_GOOGLE_FOLDER)
@tracked_task
def sync_google_folder(folder_id: int, force_full: bool = False) -> dict[str, Any]:
    """List a Drive folder and queue a sync_google_doc per file not stored at its listed version.

    Each sync lists the whole folder and compares it with what is stored, so a
    file whose doc sync failed is queued again by the next one.
    """
    logger.info(f"Syncing Google folder {folder_id}")

    with make_session() as session:
        folder = session.get(GoogleFolder, folder_id)
        if not folder or not cast(bool, folder.active):
            return {"status": "error", "error": "Folder not found or inactive"}

        account = folder.account
        if not account or not cast(bool, account.active):
            return {"status": "error", "error": "Account not found or inactive"}

        now = datetime.now(timezone.utc)

        try:
            credentials = _build_credentials(account, session)
        except Exception as e:
            session.rollback()
            account.record_sync(e)
            account.active = False  # Disable until re-auth
            session.commit()
            return {"status": "error", "error": f"Token refresh failed: {e}"}

        try:
            client = drive_client(session, credentials)
            # Listing a large folder outlasts Postgres' idle_in_transaction_session_timeout,
            # which would kill the connection under an open transaction.
            session.commit()

            google_id = cast(str, folder.folder_id)
            file_metadata = client.get_file_metadata(google_id)
            is_single_doc = file_metadata.get("mimeType") != FOLDER_MIME
            if is_single_doc:
                files = [(file_metadata, client.get_folder_path(google_id))]
            else:
                files = list(
                    client.list_files_in_folder(
                        google_id,
                        recursive=cast(bool, folder.recursive),
                        exclude_folder_ids=set(cast(list[str], folder.exclude_folder_ids) or []),
                    )
                )

            too_large = [meta["id"] for meta, _ in files if is_too_large(meta)]
            if too_large:
                logger.warning(
                    "Skipping %d Drive file(s) over %d bytes in folder %s: %s",
                    len(too_large),
                    settings.GOOGLE_DRIVE_MAX_FILE_BYTES,
                    folder.id,
                    too_large,
                )
            stored = {} if force_full else stored_versions(session, [meta["id"] for meta, _ in files])
            docs_synced = sum(
                queue_doc_sync(session, folder.id, meta, path, force_full)
                for meta, path in files
                if not is_too_large(meta) and not is_stored(stored, meta)
            )

            folder.last_sync_at = now
            folder.record_sync(None)
            account.last_sync_at = now
            account.record_sync(None)
            session.commit()

        except Exception as e:
            session.rollback()
            # On the folder, so one missing folder neither hides behind nor
            # overwrites the status of the account's other folders.
            folder.record_sync(e)
            session.commit()
            raise

        return {
            "status": "completed",
            "sync_type": "full" if force_full else "incremental",
            "folder_id": folder_id,
            "folder_name": folder.folder_name,
            "docs_synced": docs_synced,
            "too_large": len(too_large),
            "is_single_doc": is_single_doc,
        }
