import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Sequence, Any

from memory.common import extract
from memory.workers.tasks.content_processing import process_content_item
from sqlalchemy import select
from sqlalchemy.orm import contains_eager

from memory.common import collections, embedding, qdrant, settings
from memory.common.db.connection import make_session
from memory.common.db.models import Chunk, SourceItem
from memory.common.celery_app import (
    app,
    CLEAN_ALL_COLLECTIONS,
    CLEAN_COLLECTION,
    REINGEST_MISSING_CHUNKS,
    REINGEST_CHUNK,
    REINGEST_ITEM,
    REINGEST_EMPTY_SOURCE_ITEMS,
    REINGEST_ALL_EMPTY_SOURCE_ITEMS,
    UPDATE_METADATA_FOR_SOURCE_ITEMS,
    UPDATE_METADATA_FOR_ITEM,
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
def clean_all_collections():
    logger.info("Cleaning all collections")
    for collection in collections.ALL_COLLECTIONS:
        clean_collection.delay(collection)  # type: ignore


@app.task(name=REINGEST_CHUNK)
def reingest_chunk(chunk_id: str, collection: str):
    logger.info(f"Reingesting chunk {chunk_id}")
    with make_session() as session:
        chunk = session.query(Chunk).get(chunk_id)
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
            [chunk.source.as_payload()],
        )
        chunk.checked_at = datetime.now()
        session.commit()


def get_item_class(item_type: str):
    class_ = SourceItem.registry._class_registry.get(item_type)
    if not class_:
        available_types = ", ".join(sorted(SourceItem.registry._class_registry.keys()))
        raise ValueError(
            f"Unsupported item type {item_type}. Available types: {available_types}"
        )
    if not hasattr(class_, "chunks"):
        raise ValueError(f"Item type {item_type} does not have chunks")
    return class_


@app.task(name=REINGEST_ITEM)
def reingest_item(item_id: str, item_type: str):
    logger.info(f"Reingesting {item_type} {item_id}")
    try:
        class_ = get_item_class(item_type)
    except ValueError as e:
        logger.error(f"Error getting item class: {e}")
        return {"status": "error", "error": str(e)}

    with make_session() as session:
        item = session.query(class_).get(item_id)
        if not item:
            return {"status": "error", "error": f"Item {item_id} not found"}

        chunk_ids = [str(c.id) for c in item.chunks if c.id]
        if chunk_ids:
            client = qdrant.get_qdrant_client()
            try:
                qdrant.delete_points(client, item.modality, chunk_ids)
            except IOError as e:
                logger.error(f"Error deleting chunks for {item_id}: {e}")

        for chunk in item.chunks:
            session.delete(chunk)

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
            query = query.filter(Chunk.source.has(SourceItem.modality == collection))
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
                    contains_eager(Chunk.source).load_only(
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
        item = session.query(class_).get(item_id)
        if not item:
            return {"status": "error", "error": f"Item {item_id} not found"}

        chunk_ids = [str(chunk.id) for chunk in item.chunks if chunk.id]
        if not chunk_ids:
            return {"status": "success", "updated_chunks": 0, "errors": 0}

        collection = item.modality

        try:
            current_payloads = qdrant.get_payloads(client, collection, chunk_ids)

            # Get new metadata from source item
            new_metadata = item.as_payload()
            new_tags = set(new_metadata.get("tags", []))

            for chunk_id in chunk_ids:
                if chunk_id not in current_payloads:
                    logger.warning(
                        f"Chunk {chunk_id} not found in Qdrant collection {collection}"
                    )
                    continue

                current_payload = current_payloads[chunk_id]
                current_tags = set(current_payload.get("tags", []))

                # Merge tags (combine existing and new tags)
                merged_tags = list(current_tags | new_tags)
                updated_metadata = new_metadata.copy()
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
