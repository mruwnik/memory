"""Celery tasks for Google Drive document syncing."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from memory.common import qdrant
from memory.common.celery_app import app
from memory.common.db.connection import make_session
from memory.common.db.models import GoogleDoc
from memory.common.db.models.sources import GoogleAccount, GoogleFolder
from memory.parsers.google_drive import (
    GoogleDriveClient,
    GoogleCredentials,
    GoogleFileData,
    refresh_credentials,
    _get_oauth_config,
)
from memory.common.content_processing import (
    create_content_hash,
    create_task_result,
    process_content_item,
    safe_task_execution,
)

logger = logging.getLogger(__name__)

# Task name constants
GOOGLE_ROOT = "memory.workers.tasks.google_drive"
SYNC_GOOGLE_FOLDER = f"{GOOGLE_ROOT}.sync_google_folder"
SYNC_GOOGLE_DOC = f"{GOOGLE_ROOT}.sync_google_doc"
SYNC_ALL_GOOGLE_ACCOUNTS = f"{GOOGLE_ROOT}.sync_all_google_accounts"


def _build_credentials(account: GoogleAccount, session) -> GoogleCredentials:
    """Build credentials from account, refreshing if needed."""
    credentials = refresh_credentials(account, session)
    return GoogleCredentials(
        access_token=credentials.token,
        refresh_token=credentials.refresh_token,
        token_expires_at=credentials.expiry,
        scopes=list(credentials.scopes or []),
    )


def _serialize_file_data(data: GoogleFileData) -> dict[str, Any]:
    """Serialize GoogleFileData for Celery task passing."""
    return {
        **data,
        "modified_at": data["modified_at"].isoformat() if data["modified_at"] else None,
        "created_at": data["created_at"].isoformat() if data["created_at"] else None,
    }


def _deserialize_file_data(data: dict[str, Any]) -> GoogleFileData:
    """Deserialize file data from Celery task."""
    from memory.parsers.google_drive import parse_google_date

    return GoogleFileData(
        file_id=data["file_id"],
        title=data["title"],
        mime_type=data["mime_type"],
        original_mime_type=data["original_mime_type"],
        folder_path=data["folder_path"],
        owner=data["owner"],
        last_modified_by=data["last_modified_by"],
        modified_at=parse_google_date(data.get("modified_at")),
        created_at=parse_google_date(data.get("created_at")),
        content=data["content"],
        content_hash=data["content_hash"],
        size=data["size"],
        word_count=data["word_count"],
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
    chunk_ids = [str(c.id) for c in existing_chunks if c.id]
    if chunk_ids:
        try:
            client = qdrant.get_qdrant_client()
            qdrant.delete_points(client, cast(str, existing.modality), chunk_ids)
        except IOError as e:
            logger.error(f"Error deleting chunks: {e}")

    # Delete chunks from database
    for chunk in existing_chunks:
        session.delete(chunk)
    if existing.chunks is not None:
        existing.chunks.clear()

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


@app.task(name=SYNC_GOOGLE_DOC)
@safe_task_execution
def sync_google_doc(
    folder_id: int,
    file_data_serialized: dict[str, Any],
) -> dict[str, Any]:
    """Sync a single Google Drive document."""
    file_data = _deserialize_file_data(file_data_serialized)
    logger.info(f"Syncing Google Doc: {file_data['title']}")

    with make_session() as session:
        folder = session.get(GoogleFolder, folder_id)
        if not folder:
            return {"status": "error", "error": "Folder not found"}

        # Check for existing document by Google file ID
        existing = (
            session.query(GoogleDoc)
            .filter(GoogleDoc.google_file_id == file_data["file_id"])
            .first()
        )

        if existing:
            return _update_existing_doc(session, existing, folder, file_data)

        # Create new document
        google_doc = _create_google_doc(folder, file_data)
        return process_content_item(google_doc, session)


@app.task(name=SYNC_GOOGLE_FOLDER)
@safe_task_execution
def sync_google_folder(folder_id: int, force_full: bool = False) -> dict[str, Any]:
    """Sync all documents in a Google Drive folder."""
    logger.info(f"Syncing Google folder {folder_id}")

    with make_session() as session:
        folder = session.get(GoogleFolder, folder_id)
        if not folder or not cast(bool, folder.active):
            return {"status": "error", "error": "Folder not found or inactive"}

        account = folder.account
        if not account or not cast(bool, account.active):
            return {"status": "error", "error": "Account not found or inactive"}

        now = datetime.now(timezone.utc)
        last_sync = cast(datetime | None, folder.last_sync_at)

        # Check if sync is needed based on interval
        if last_sync and not force_full:
            check_interval = cast(int, folder.check_interval)
            if now - last_sync < timedelta(minutes=check_interval):
                return {"status": "skipped_recent_check", "folder_id": folder_id}

        # Build credentials (refresh if needed)
        try:
            credentials = _build_credentials(account, session)
        except Exception as e:
            account.sync_error = str(e)
            account.active = False  # Disable until re-auth
            session.commit()
            return {"status": "error", "error": f"Token refresh failed: {e}"}

        # Get OAuth config for client
        client_id, client_secret, token_uri = _get_oauth_config(session)
        client = GoogleDriveClient(
            credentials,
            client_id=client_id,
            client_secret=client_secret,
            token_uri=token_uri,
        )

        # Determine sync window
        since = None if force_full else last_sync

        docs_synced = 0
        task_ids = []
        is_single_doc = False

        try:
            google_id = cast(str, folder.folder_id)

            # Check if this is a single document or a folder
            file_metadata = client.get_file_metadata(google_id)
            is_folder = file_metadata.get("mimeType") == "application/vnd.google-apps.folder"
            is_single_doc = not is_folder

            if is_folder:
                # It's a folder - list and sync all files inside
                # Get excluded folder IDs
                exclude_ids = set(cast(list[str], folder.exclude_folder_ids) or [])
                if exclude_ids:
                    logger.info(f"Excluding {len(exclude_ids)} folder(s) from sync")

                for file_meta, file_folder_path in client.list_files_in_folder(
                    google_id,
                    recursive=cast(bool, folder.recursive),
                    since=since,
                    exclude_folder_ids=exclude_ids,
                ):
                    try:
                        file_data = client.fetch_file(file_meta, file_folder_path)
                        serialized = _serialize_file_data(file_data)
                        task = sync_google_doc.delay(folder.id, serialized)
                        task_ids.append(task.id)
                        docs_synced += 1
                    except Exception as e:
                        logger.error(f"Error fetching file {file_meta.get('name')}: {e}")
                        continue
            else:
                # It's a single document - sync it directly
                logger.info(f"Syncing single document: {file_metadata.get('name')}")
                folder_path = client.get_folder_path(google_id)

                # Check if we need to sync based on modification time
                if since and file_metadata.get("modifiedTime"):
                    from memory.parsers.google_drive import parse_google_date
                    modified_at = parse_google_date(file_metadata.get("modifiedTime"))
                    if modified_at and modified_at <= since:
                        logger.info(f"Document not modified since last sync, skipping")
                        folder.last_sync_at = now
                        session.commit()
                        return {
                            "status": "completed",
                            "sync_type": "incremental",
                            "folder_id": folder_id,
                            "folder_name": folder.folder_name,
                            "docs_synced": 0,
                            "task_ids": [],
                            "is_single_doc": True,
                        }

                try:
                    file_data = client.fetch_file(file_metadata, folder_path)
                    serialized = _serialize_file_data(file_data)
                    task = sync_google_doc.delay(folder.id, serialized)
                    task_ids.append(task.id)
                    docs_synced = 1
                except Exception as e:
                    logger.error(f"Error fetching document {file_metadata.get('name')}: {e}")
                    raise

            # Update sync timestamps
            folder.last_sync_at = now
            account.last_sync_at = now
            account.sync_error = None
            session.commit()

        except Exception as e:
            account.sync_error = str(e)
            session.commit()
            raise

        return {
            "status": "completed",
            "sync_type": "full" if force_full else "incremental",
            "folder_id": folder_id,
            "folder_name": folder.folder_name,
            "docs_synced": docs_synced,
            "task_ids": task_ids,
            "is_single_doc": is_single_doc,
        }


@app.task(name=SYNC_ALL_GOOGLE_ACCOUNTS)
def sync_all_google_accounts(force_full: bool = False) -> list[dict[str, Any]]:
    """Trigger sync for all active Google Drive folders."""
    with make_session() as session:
        active_folders = (
            session.query(GoogleFolder)
            .join(GoogleAccount)
            .filter(GoogleFolder.active, GoogleAccount.active)
            .all()
        )

        results = [
            {
                "folder_id": folder.id,
                "folder_name": folder.folder_name,
                "task_id": sync_google_folder.delay(folder.id, force_full=force_full).id,
            }
            for folder in active_folders
        ]

        logger.info(
            f"Scheduled {'full' if force_full else 'incremental'} sync "
            f"for {len(results)} active Google folders"
        )
        return results
