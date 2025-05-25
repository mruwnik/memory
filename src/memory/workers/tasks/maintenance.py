import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import contains_eager

from memory.common import collections, embedding, qdrant, settings
from memory.common.db.connection import make_session
from memory.common.db.models import Chunk, SourceItem
from memory.workers.celery_app import app

logger = logging.getLogger(__name__)


CLEAN_ALL_COLLECTIONS = "memory.workers.tasks.maintenance.clean_all_collections"
CLEAN_COLLECTION = "memory.workers.tasks.maintenance.clean_collection"
REINGEST_MISSING_CHUNKS = "memory.workers.tasks.maintenance.reingest_missing_chunks"
REINGEST_CHUNK = "memory.workers.tasks.maintenance.reingest_chunk"


@app.task(name=CLEAN_COLLECTION)
def clean_collection(collection: str) -> dict[str, int]:
    logger.info(f"Cleaning collection {collection}")
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
    for collection in embedding.ALL_COLLECTIONS:
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

        data = chunk.data
        if collection in collections.MULTIMODAL_COLLECTIONS:
            vector = embedding.embed_mixed(data)[0]
        elif len(data) == 1 and isinstance(data[0], str):
            vector = embedding.embed_text([data[0]])[0]
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
def reingest_missing_chunks(batch_size: int = 1000):
    logger.info("Reingesting missing chunks")
    total_stats = defaultdict(lambda: {"missing": 0, "correct": 0, "total": 0})
    since = datetime.now() - timedelta(minutes=settings.CHUNK_REINGEST_SINCE_MINUTES)

    with make_session() as session:
        total_count = session.query(Chunk).filter(Chunk.checked_at < since).count()

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
