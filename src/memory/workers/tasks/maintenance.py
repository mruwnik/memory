import itertools
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Sequence, Any, cast

from qdrant_client.http.exceptions import ApiException, UnexpectedResponse
from sqlalchemy import select
from sqlalchemy.orm import contains_eager, selectinload

from memory.common import collections, embedding, extract, qdrant, settings
from memory.common.db.models.discord import DiscordChannel, DiscordServer
from memory.common.db.models.slack import SlackChannel, SlackWorkspace
from memory.common.celery_app import (
    app,
    CLEAN_ALL_COLLECTIONS,
    CLEAN_COLLECTION,
    CLEANUP_EXPIRED_OAUTH_STATES,
    CLEANUP_EXPIRED_SESSIONS,
    CLEANUP_OLD_CLAUDE_SESSIONS,
    REINGEST_MISSING_CHUNKS,
    REINGEST_CHUNK,
    REINGEST_ITEM,
    REINGEST_EMPTY_SOURCE_ITEMS,
    REINGEST_ALL_EMPTY_SOURCE_ITEMS,
    PROCESS_RAW_ITEMS,
    PROCESS_RAW_ITEM,
    UPDATE_METADATA_FOR_SOURCE_ITEMS,
    UPDATE_METADATA_FOR_ITEM,
    UPDATE_SOURCE_ACCESS_CONTROL,
)
from memory.common.db.connection import make_session
from memory.common.db.models import Chunk, CodingProject, Session, SourceItem
from memory.common.content_processing import (
    clear_item_chunks,
    process_content_item,
    safe_task_execution,
)

logger = logging.getLogger(__name__)


@app.task(name=CLEAN_COLLECTION)
def clean_collection(collection: str) -> dict[str, int]:
    logger.info(f"Cleaning collection {collection}")

    if collection not in collections.ALL_COLLECTIONS:
        raise ValueError(f"Unsupported collection {collection}")

    client = qdrant.get_qdrant_client()
    batches, deleted, checked = 0, 0, 0
    for batch in qdrant.batch_ids(client, collection):
        batches += 1
        batch_ids = set(batch)
        with make_session() as session:
            db_ids = {
                str(c.id) for c in session.query(Chunk).filter(Chunk.id.in_(batch_ids))
            }
        ids_to_delete = batch_ids - db_ids
        checked += len(batch_ids)
        if ids_to_delete:
            qdrant.delete_points(client, collection, list(ids_to_delete))
            deleted += len(ids_to_delete)
    return {
        "batches": batches,
        "deleted": deleted,
        "checked": checked,
    }


@app.task(name=CLEAN_ALL_COLLECTIONS)
@safe_task_execution
def clean_all_collections():
    logger.info("Cleaning all collections")
    for collection in collections.ALL_COLLECTIONS:
        clean_collection.delay(collection)  # type: ignore
    return {"status": "dispatched", "collections": len(collections.ALL_COLLECTIONS)}


@app.task(name=REINGEST_CHUNK)
def reingest_chunk(chunk_id: str, collection: str):
    logger.info(f"Reingesting chunk {chunk_id}")
    with make_session() as session:
        chunk = session.get(Chunk, chunk_id)
        if not chunk:
            logger.error(f"Chunk {chunk_id} not found")
            return

        if collection not in collections.ALL_COLLECTIONS:
            raise ValueError(f"Unsupported collection {collection}")

        data = [extract.DataChunk(data=chunk.data)]
        if collection in collections.MULTIMODAL_COLLECTIONS:
            vector = embedding.embed_mixed(data)[0]
        elif collection in collections.TEXT_COLLECTIONS:
            vector = embedding.embed_text(data)[0]
        else:
            raise ValueError(f"Unsupported data type for collection {collection}")

        client = qdrant.get_qdrant_client()
        qdrant.upsert_vectors(
            client,
            collection,
            [chunk_id],
            [vector],
            [dict(chunk.source.as_payload())],
        )
        chunk.checked_at = datetime.now()
        session.commit()


def get_item_class(item_type: str) -> type[SourceItem]:
    class_ = SourceItem.registry._class_registry.get(item_type)
    if not class_:
        available_types = ", ".join(sorted(SourceItem.registry._class_registry.keys()))
        raise ValueError(
            f"Unsupported item type {item_type}. Available types: {available_types}"
        )
    if not hasattr(class_, "chunks"):
        raise ValueError(f"Item type {item_type} does not have chunks")
    return cast(type[SourceItem], class_)


@app.task(name=REINGEST_ITEM)
def reingest_item(item_id: str, item_type: str):
    logger.info(f"Reingesting {item_type} {item_id}")
    try:
        class_ = get_item_class(item_type)
    except ValueError as e:
        logger.error(f"Error getting item class: {e}")
        return {"status": "error", "error": str(e)}

    with make_session() as session:
        item = session.get(class_, item_id)
        if not item:
            return {"status": "error", "error": f"Item {item_id} not found"}

        clear_item_chunks(item, session)
        return process_content_item(item, session)


@app.task(name=REINGEST_EMPTY_SOURCE_ITEMS)
def reingest_empty_source_items(item_type: str):
    logger.info("Reingesting empty source items")
    try:
        class_ = get_item_class(item_type)
    except ValueError as e:
        logger.error(f"Error getting item class: {e}")
        return {"status": "error", "error": str(e)}

    with make_session() as session:
        item_ids = session.query(class_.id).filter(~class_.chunks.any()).all()

        logger.info(f"Found {len(item_ids)} items to reingest")

        for item_id in item_ids:
            reingest_item.delay(item_id.id, item_type)  # type: ignore

        return {"status": "success", "items": len(item_ids)}


@app.task(name=REINGEST_ALL_EMPTY_SOURCE_ITEMS)
def reingest_all_empty_source_items():
    logger.info("Reingesting all empty source items")
    for item_type in SourceItem.registry._class_registry.keys():
        reingest_empty_source_items.delay(item_type)  # type: ignore


@app.task(name=PROCESS_RAW_ITEM)
def process_raw_item(item_id: int, item_type: str):
    """Process a single RAW item - generate embeddings and store in Qdrant.

    Args:
        item_id: ID of the source item to process
        item_type: Type name (e.g., 'slack_message', 'discord_message')

    Returns:
        Task result dict with status and metadata
    """
    logger.info(f"Processing RAW item {item_type} {item_id}")
    try:
        class_ = get_item_class(item_type)
    except ValueError as e:
        logger.error(f"Error getting item class: {e}")
        return {"status": "error", "error": str(e)}

    with make_session() as session:
        item = session.get(class_, item_id)
        if not item:
            return {"status": "error", "error": f"Item {item_id} not found"}

        if item.embed_status != "RAW":
            return {
                "status": "skipped",
                "reason": f"Item already processed (status={item.embed_status})",
            }

        return process_content_item(item, session)


@app.task(name=PROCESS_RAW_ITEMS)
@safe_task_execution
def process_raw_items(
    item_type: str | None = None,
    modality: str | None = None,
    batch_size: int = 100,
):
    """Find and process all items with embed_status='RAW'.

    Can filter by item_type (e.g., 'slack_message') or modality (e.g., 'message').
    Dispatches individual process_raw_item tasks for each item found.

    Args:
        item_type: Filter by specific item type (optional)
        modality: Filter by modality (optional, e.g., 'message', 'mail')
        batch_size: Maximum items to process per run (default 100)

    Returns:
        Dict with status and count of items queued for processing
    """
    logger.info(
        f"Finding RAW items to process (item_type={item_type}, modality={modality})"
    )

    with make_session() as session:
        query = session.query(SourceItem.id, SourceItem.type).filter(
            SourceItem.embed_status == "RAW"
        )

        if item_type:
            try:
                class_ = get_item_class(item_type)
                query = session.query(class_.id, class_.type).filter(
                    class_.embed_status == "RAW"
                )
            except ValueError as e:
                logger.error(f"Error getting item class: {e}")
                return {"status": "error", "error": str(e)}

        if modality:
            query = query.filter(SourceItem.modality == modality)

        items = query.limit(batch_size).all()
        logger.info(f"Found {len(items)} RAW items to process")

        for item_id, type_name in items:
            process_raw_item.delay(item_id, type_name)  # type: ignore

        return {"status": "success", "queued": len(items)}


def check_batch(batch: Sequence[Chunk]) -> dict:
    client = qdrant.get_qdrant_client()
    by_collection = defaultdict(list)
    for chunk in batch:
        by_collection[chunk.source.modality].append(chunk)

    stats = {}
    for collection, chunks in by_collection.items():
        missing = qdrant.find_missing_points(
            client, collection, [str(c.id) for c in chunks]
        )

        for chunk in chunks:
            if str(chunk.id) in missing:
                reingest_chunk.delay(str(chunk.id), collection)  # type: ignore
            else:
                chunk.checked_at = datetime.now()

        stats[collection] = {
            "missing": len(missing),
            "correct": len(chunks) - len(missing),
            "total": len(chunks),
        }

    return stats


@app.task(name=REINGEST_MISSING_CHUNKS)
@safe_task_execution
def reingest_missing_chunks(
    batch_size: int = 1000,
    collection: str | None = None,
    minutes_ago: int = settings.CHUNK_REINGEST_SINCE_MINUTES,
):
    logger.info("Reingesting missing chunks")
    total_stats = defaultdict(lambda: {"missing": 0, "correct": 0, "total": 0})
    since = datetime.now() - timedelta(minutes=minutes_ago)

    with make_session() as session:
        query = session.query(Chunk).filter(Chunk.checked_at < since)
        if collection:
            # Chunk.source is a backref relationship
            query = query.filter(Chunk.source.has(SourceItem.modality == collection))  # type: ignore[union-attr]
        total_count = query.count()

        logger.info(
            f"Found {total_count} chunks to check, processing in batches of {batch_size}"
        )

        num_batches = (total_count + batch_size - 1) // batch_size

        for batch_num in range(num_batches):
            stmt = (
                select(Chunk)
                .join(SourceItem, Chunk.source_id == SourceItem.id)
                .filter(Chunk.checked_at < since)
                .options(
                    contains_eager(Chunk.source).load_only(  # type: ignore[arg-type,union-attr]
                        SourceItem.id,  # type: ignore
                        SourceItem.modality,  # type: ignore
                        SourceItem.tags,  # type: ignore
                    )
                )
                .order_by(Chunk.id)
                .limit(batch_size)
            )
            chunks = session.execute(stmt).scalars().all()

            if not chunks:
                break

            logger.info(
                f"Processing batch {batch_num + 1}/{num_batches} with {len(chunks)} chunks"
            )
            batch_stats = check_batch(chunks)
            session.commit()

            for collection, stats in batch_stats.items():
                total_stats[collection]["missing"] += stats["missing"]
                total_stats[collection]["correct"] += stats["correct"]
                total_stats[collection]["total"] += stats["total"]

        return dict(total_stats)


def _payloads_equal(current: dict[str, Any], new: dict[str, Any]) -> bool:
    """Compare two payloads to see if they're effectively equal."""
    # Handle tags specially - compare as sets since order doesn't matter
    current_tags = set(current.get("tags", []))
    new_tags = set(new.get("tags", []))

    if current_tags != new_tags:
        return False

    # Compare all other fields
    current_without_tags = {k: v for k, v in current.items() if k != "tags"}
    new_without_tags = {k: v for k, v in new.items() if k != "tags"}

    return current_without_tags == new_without_tags


@app.task(name=UPDATE_METADATA_FOR_ITEM)
def update_metadata_for_item(item_id: str, item_type: str):
    """Update metadata in Qdrant for all chunks of a single source item, merging tags."""
    logger.info(f"Updating metadata for {item_type} {item_id}")
    try:
        class_ = get_item_class(item_type)
    except ValueError as e:
        logger.error(f"Error getting item class: {e}")
        return {"status": "error", "error": str(e)}

    client = qdrant.get_qdrant_client()
    updated_chunks = 0
    errors = 0

    with make_session() as session:
        item = session.get(class_, item_id)
        if not item:
            return {"status": "error", "error": f"Item {item_id} not found"}

        chunk_ids = [str(chunk.id) for chunk in item.chunks if chunk.id]
        if not chunk_ids:
            return {"status": "success", "updated_chunks": 0, "errors": 0}

        collection = item.modality

        try:
            current_payloads = qdrant.get_payloads(client, collection, chunk_ids)

            # Get new metadata from source item
            # Note: as_payload() triggers a lazy load of item.people. For single-item
            # operations this N+1 cost is acceptable. Bulk operations should eager-load.
            new_metadata: dict[str, Any] = dict(item.as_payload())
            new_tags: set[str] = set(new_metadata.get("tags", []))

            for chunk_id in chunk_ids:
                if chunk_id not in current_payloads:
                    logger.warning(
                        f"Chunk {chunk_id} not found in Qdrant collection {collection}"
                    )
                    continue

                current_payload = current_payloads[chunk_id]
                current_tags: set[str] = set(current_payload.get("tags", []))

                # Merge tags (combine existing and new tags)
                merged_tags: list[str] = list(current_tags | new_tags)
                updated_metadata: dict[str, Any] = dict(new_metadata)
                updated_metadata["tags"] = merged_tags

                if _payloads_equal(current_payload, updated_metadata):
                    continue

                qdrant.set_payload(client, collection, chunk_id, updated_metadata)
                updated_chunks += 1

        except Exception as e:
            logger.error(f"Error updating metadata for item {item.id}: {e}")
            errors += 1

    return {"status": "success", "updated_chunks": updated_chunks, "errors": errors}


@app.task(name=UPDATE_METADATA_FOR_SOURCE_ITEMS)
def update_metadata_for_source_items(item_type: str):
    """Update metadata in Qdrant for all chunks of all items of a given source type."""
    logger.info(f"Updating metadata for all {item_type} source items")
    try:
        class_ = get_item_class(item_type)
    except ValueError as e:
        logger.error(f"Error getting item class: {e}")
        return {"status": "error", "error": str(e)}

    with make_session() as session:
        item_ids = session.query(class_.id).all()
        logger.info(f"Found {len(item_ids)} items to update metadata for")

        for item_id in item_ids:
            update_metadata_for_item.delay(item_id.id, item_type)  # type: ignore

        return {"status": "success", "items": len(item_ids)}


@app.task(name=CLEANUP_EXPIRED_OAUTH_STATES)
@safe_task_execution
def cleanup_expired_oauth_states(max_age_hours: int = 1):
    """Clean up OAuth states that are older than max_age_hours.

    OAuth states should be short-lived - they're only needed during the
    authorization flow which should complete within minutes.
    """
    from memory.common.db.models import MCPServer

    logger.info(f"Cleaning up OAuth states older than {max_age_hours} hours")

    cutoff = datetime.now() - timedelta(hours=max_age_hours)
    cleaned = 0

    with make_session() as session:
        # Find MCP servers with stale OAuth state
        stale_servers = (
            session.query(MCPServer)
            .filter(
                MCPServer.state.isnot(None),
                MCPServer.updated_at < cutoff,
            )
            .all()
        )

        for server in stale_servers:
            # Clear the temporary OAuth state fields
            server.state = None
            server.code_verifier = None
            cleaned += 1

        session.commit()

    logger.info(f"Cleaned up {cleaned} expired OAuth states")
    return {"cleaned": cleaned}


@app.task(name=CLEANUP_EXPIRED_SESSIONS)
def cleanup_expired_sessions():
    """Clean up expired user sessions from the database."""
    from memory.common.db.models import UserSession

    logger.info("Cleaning up expired user sessions")

    now = datetime.now(timezone.utc)
    deleted = 0

    with make_session() as session:
        expired_sessions = (
            session.query(UserSession).filter(UserSession.expires_at < now).all()
        )

        for user_session in expired_sessions:
            session.delete(user_session)
            deleted += 1

        session.commit()

    logger.info(f"Deleted {deleted} expired user sessions")
    return {"deleted": deleted}


@app.task(name=CLEANUP_OLD_CLAUDE_SESSIONS)
@safe_task_execution
def cleanup_old_claude_sessions(max_age_days: int | None = None):
    """Clean up old coding sessions.

    Deletes old sessions and their transcript files to maintain storage limits.
    Also cleans up orphaned projects (projects with no remaining sessions).
    """
    if max_age_days is None:
        max_age_days = settings.SESSION_RETENTION_DAYS

    logger.info(f"Cleaning up sessions older than {max_age_days} days")

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    deleted_sessions = 0
    deleted_files = 0
    deleted_projects = 0

    with make_session() as db_session:
        # Find old sessions
        old_sessions = (
            db_session.query(Session)
            .filter(Session.started_at < cutoff)
            .all()
        )

        for coding_session in old_sessions:
            # Delete transcript file if it exists
            if not coding_session.transcript_path:
                db_session.delete(coding_session)
                deleted_sessions += 1
                continue

            transcript_file = settings.SESSIONS_STORAGE_DIR / coding_session.transcript_path
            if transcript_file.exists():
                try:
                    transcript_file.unlink()
                    deleted_files += 1
                except OSError as e:
                    logger.warning(f"Failed to delete transcript file {transcript_file}: {e}")

            db_session.delete(coding_session)
            deleted_sessions += 1

        db_session.commit()

        # Clean up orphaned projects (no remaining sessions)
        orphaned_projects = (
            db_session.query(CodingProject)
            .filter(~CodingProject.sessions.any())
            .all()
        )

        for project in orphaned_projects:
            db_session.delete(project)
            deleted_projects += 1

        db_session.commit()

    logger.info(
        f"Deleted {deleted_sessions} old sessions ({deleted_files} files) and "
        f"{deleted_projects} orphaned projects"
    )
    return {
        "deleted_sessions": deleted_sessions,
        "deleted_files": deleted_files,
        "deleted_projects": deleted_projects,
    }


# Mapping of source types to (model, item query function)
DATA_SOURCE_TYPES = {
    "email_account": "EmailAccount",
    "slack_channel": "SlackChannel",
    "slack_workspace": "SlackWorkspace",
    "discord_channel": "DiscordChannel",
    "discord_server": "DiscordServer",
    "calendar_account": "CalendarAccount",
    "google_folder": "GoogleFolder",
    "article_feed": "ArticleFeed",
}


def get_items_for_source(
    session, source_type: str, source_id: int | str, offset: int = 0, limit: int = 100
):
    """Get items that belong to a data source for access control updates.

    Items are eager-loaded with their chunks to avoid N+1 queries when
    iterating over items and accessing item.chunks in the caller.
    """
    from memory.common.db.models.source_items import (
        BlogPost,
        CalendarEvent,
        DiscordMessage,
        GoogleDoc,
        MailMessage,
        SlackMessage,
    )

    if source_type == "email_account":
        return (
            session.query(MailMessage)
            .options(selectinload(MailMessage.chunks))
            .filter(MailMessage.email_account_id == source_id)
            .offset(offset)
            .limit(limit)
            .all()
        )
    elif source_type == "slack_channel":
        return (
            session.query(SlackMessage)
            .options(selectinload(SlackMessage.chunks))
            .filter(SlackMessage.channel_id == str(source_id))
            .offset(offset)
            .limit(limit)
            .all()
        )
    elif source_type == "slack_workspace":
        # Get all messages in channels belonging to this workspace
        channel_ids = [
            c.id for c in session.query(SlackChannel.id)
            .filter(SlackChannel.workspace_id == str(source_id))
            .all()
        ]
        if not channel_ids:
            return []
        return (
            session.query(SlackMessage)
            .options(selectinload(SlackMessage.chunks))
            .filter(SlackMessage.channel_id.in_(channel_ids))
            .offset(offset)
            .limit(limit)
            .all()
        )
    elif source_type == "discord_channel":
        return (
            session.query(DiscordMessage)
            .options(selectinload(DiscordMessage.chunks))
            .filter(DiscordMessage.channel_id == source_id)
            .offset(offset)
            .limit(limit)
            .all()
        )
    elif source_type == "discord_server":
        # Get all messages in channels belonging to this server
        channel_ids = [
            c.id for c in session.query(DiscordChannel.id)
            .filter(DiscordChannel.server_id == source_id)
            .all()
        ]
        if not channel_ids:
            return []
        return (
            session.query(DiscordMessage)
            .options(selectinload(DiscordMessage.chunks))
            .filter(DiscordMessage.channel_id.in_(channel_ids))
            .offset(offset)
            .limit(limit)
            .all()
        )
    elif source_type == "calendar_account":
        return (
            session.query(CalendarEvent)
            .options(selectinload(CalendarEvent.chunks))
            .filter(CalendarEvent.calendar_account_id == source_id)
            .offset(offset)
            .limit(limit)
            .all()
        )
    elif source_type == "google_folder":
        return (
            session.query(GoogleDoc)
            .options(selectinload(GoogleDoc.chunks))
            .filter(GoogleDoc.folder_id == source_id)
            .offset(offset)
            .limit(limit)
            .all()
        )
    elif source_type == "article_feed":
        return (
            session.query(BlogPost)
            .options(selectinload(BlogPost.chunks))
            .filter(BlogPost.feed_id == source_id)
            .offset(offset)
            .limit(limit)
            .all()
        )
    else:
        raise ValueError(f"Unknown source type: {source_type}")


def get_data_source_model(source_type: str):
    """Get the SQLAlchemy model for a data source type."""
    from memory.common.db.models.sources import (
        ArticleFeed,
        CalendarAccount,
        EmailAccount,
        GoogleFolder,
    )

    models = {
        "email_account": EmailAccount,
        "slack_channel": SlackChannel,
        "slack_workspace": SlackWorkspace,
        "discord_channel": DiscordChannel,
        "discord_server": DiscordServer,
        "calendar_account": CalendarAccount,
        "google_folder": GoogleFolder,
        "article_feed": ArticleFeed,
    }
    if source_type not in models:
        raise ValueError(f"Unknown source type: {source_type}")
    return models[source_type]


@app.task(name=UPDATE_SOURCE_ACCESS_CONTROL, bind=True, max_retries=3)
def update_source_access_control(
    self, source_type: str, source_id: int | str, config_version: int
):
    """Update Qdrant payloads when data source access config changes.

    When a data source's project_id or sensitivity changes, this task updates
    the resolved values in Qdrant payloads for all items belonging to that source.

    Args:
        source_type: Type of data source (email_account, slack_channel, etc.)
        source_id: ID of the data source
        config_version: Version number to detect stale jobs (race condition mitigation)

    The config_version parameter prevents race conditions: if a newer config change
    happens during processing, this job will abort because the version won't match.

    Retries on transient Qdrant failures (connection errors, API errors) if no
    progress has been made yet. Once updates start succeeding, errors are logged
    but processing continues to avoid losing partial progress.
    """
    logger.info(
        f"Updating access control for {source_type} {source_id} (version {config_version})"
    )

    model = get_data_source_model(source_type)
    client = qdrant.get_qdrant_client()
    updated_items = 0
    updated_chunks = 0
    errors = 0

    with make_session() as session:
        # Get the data source and verify config_version
        source = session.get(model, source_id)
        if source is None:
            logger.warning(f"Data source {source_type} {source_id} not found")
            return {"status": "not_found"}

        current_version = getattr(source, "config_version", None)
        if current_version is not None and current_version != config_version:
            logger.info(
                f"Stale job: source version {current_version} != job version {config_version}"
            )
            return {"status": "stale", "reason": "config_version_mismatch"}

        # Process items in batches
        batch_size = 100

        for batch_num in itertools.count():
            items = get_items_for_source(
                session, source_type, source_id, offset=batch_num * batch_size, limit=batch_size
            )
            if not items:
                break

            for item in items:
                # Get resolved access control values
                resolved_project_id, resolved_sensitivity = item.resolve_access_control()

                # Update Qdrant payload for each chunk
                for chunk in item.chunks:
                    if not chunk.id:
                        continue

                    try:
                        qdrant.set_payload(
                            client,
                            chunk.collection_name,
                            str(chunk.id),
                            {
                                "project_id": resolved_project_id,
                                "sensitivity": resolved_sensitivity,
                            },
                        )
                        updated_chunks += 1
                    except (UnexpectedResponse, ApiException, ConnectionError) as e:
                        # Retry on transient failures if we haven't made progress yet
                        if updated_chunks == 0:
                            logger.warning(
                                f"Transient error updating chunk {chunk.id}, retrying task: {e}"
                            )
                            raise self.retry(exc=e, countdown=60)
                        # Once we've made progress, log and continue to avoid losing work
                        logger.error(
                            f"Failed to update chunk {chunk.id} payload: {e}"
                        )
                        errors += 1
                    except Exception as e:
                        logger.error(
                            f"Failed to update chunk {chunk.id} payload: {e}"
                        )
                        errors += 1

                updated_items += 1

    logger.info(
        f"Updated {updated_items} items ({updated_chunks} chunks, {errors} errors) for "
        f"{source_type} {source_id}"
    )
    return {
        "status": "success",
        "updated_items": updated_items,
        "updated_chunks": updated_chunks,
        "errors": errors,
    }
