"""
Content processing utilities for memory workers.

This module provides core functionality for processing content items through
the complete workflow: existence checking, content hashing, embedding generation,
vector storage, and result tracking.
"""

from collections import defaultdict
import hashlib
import traceback
import logging
from typing import Any, Callable, Iterable, Sequence, cast

from memory.common import embedding, qdrant, settings
from memory.common.db.models import SourceItem, Chunk
from memory.common.discord import notify_task_failure

logger = logging.getLogger(__name__)


def check_content_exists(
    session,
    model_class: type[SourceItem],
    **kwargs: Any,
) -> SourceItem | None:
    """
    Check if content already exists in the database.

    Searches for existing content by any of the provided attributes
    (typically URL, file_path, or SHA256 hash).

    Args:
        session: Database session for querying
        model_class: The SourceItem model class to search in
        **kwargs: Attribute-value pairs to search for

    Returns:
        Existing SourceItem if found, None otherwise
    """
    for key, value in kwargs.items():
        if not hasattr(model_class, key):
            continue

        existing = (
            session.query(model_class)
            .filter(getattr(model_class, key) == value)
            .first()
        )
        if existing:
            return existing

    return None


def create_content_hash(content: str, *additional_data: str) -> bytes:
    """
    Create SHA256 hash from content and optional additional data.

    Args:
        content: Primary content to hash
        *additional_data: Additional strings to include in the hash

    Returns:
        SHA256 hash digest as bytes
    """
    hash_input = content + "".join(additional_data)
    return hashlib.sha256(hash_input.encode()).digest()


def embed_source_item(source_item: SourceItem) -> int:
    """
    Generate embeddings for a source item's content.

    Processes the source item through the embedding pipeline, creating
    chunks and their corresponding vector embeddings. Updates the item's
    embed_status based on success or failure.

    Args:
        source_item: The SourceItem to embed

    Returns:
        Number of successfully embedded chunks

    Side effects:
        - Sets source_item.chunks with generated chunks
        - Sets source_item.embed_status to "QUEUED" or "FAILED"
    """
    try:
        chunks = embedding.embed_source_item(source_item)
        if chunks:
            source_item.chunks = chunks
            source_item.embed_status = "QUEUED"  # type: ignore
            return len(chunks)
        else:
            source_item.embed_status = "FAILED"  # type: ignore
            logger.warning(
                f"No chunks generated for {type(source_item).__name__}: {getattr(source_item, 'title', 'unknown')}"
            )
            return 0
    except Exception as e:
        source_item.embed_status = "FAILED"  # type: ignore
        logger.error(f"Failed to embed {type(source_item).__name__}: {e}")
        logger.error(traceback.format_exc())
        return 0


def by_collection(chunks: Sequence[Chunk]) -> dict[str, dict[str, Any]]:
    collections: dict[str, dict[str, list[Any]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for chunk in chunks:
        collection = collections[cast(str, chunk.collection_name)]
        collection["ids"].append(chunk.id)
        collection["vectors"].append(chunk.vector)
        collection["payloads"].append(chunk.item_metadata)
    return collections


def push_to_qdrant(source_items: Sequence[SourceItem]):
    """
    Push embeddings to Qdrant vector database.

    Uploads vector embeddings for all source items that have been successfully
    embedded (status "QUEUED") and have chunks available.

    Args:
        source_items: Sequence of SourceItems to process
        collection_name: Name of the Qdrant collection to store vectors in

    Raises:
        Exception: If the Qdrant upsert operation fails

    Side effects:
        - Updates embed_status to "STORED" for successful items
        - Updates embed_status to "FAILED" for failed items
    """
    items_to_process = [
        item
        for item in source_items
        if cast(str, getattr(item, "embed_status", None)) == "QUEUED" and item.chunks
    ]

    if not items_to_process:
        return

    all_chunks = [chunk for item in items_to_process for chunk in item.chunks]
    if not all_chunks:
        return

    try:
        client = qdrant.get_qdrant_client()
        collections = by_collection(all_chunks)
        for collection_name, collection in collections.items():
            qdrant.upsert_vectors(
                client=client,
                collection_name=collection_name,
                ids=collection["ids"],
                vectors=collection["vectors"],
                payloads=collection["payloads"],
            )

        for item in items_to_process:
            item.embed_status = "STORED"  # type: ignore
            logger.info(
                f"Successfully stored embeddings for: {getattr(item, 'title', 'unknown')}"
            )

    except Exception as e:
        for item in items_to_process:
            item.embed_status = "FAILED"  # type: ignore
        logger.error(f"Failed to push embeddings to Qdrant: {e}")
        logger.error(traceback.format_exc())
        raise


def create_task_result(
    item: SourceItem, status: str, **additional_fields: Any
) -> dict[str, Any]:
    """
    Create standardized task result dictionary.

    Generates a consistent result format for task execution reporting,
    including item metadata and processing status.

    Args:
        item: The processed SourceItem
        status: Processing status string
        **additional_fields: Extra fields to include in the result

    Returns:
        Dictionary with standardized task result format
    """
    return {
        f"{type(item).__name__.lower()}_id": item.id,
        "title": getattr(item, "title", None) or getattr(item, "subject", None),
        "status": status,
        "chunks_count": len(item.chunks),
        "embed_status": item.embed_status,
        **additional_fields,
    }


def process_content_item(item: SourceItem, session) -> dict[str, Any]:
    """
    Execute complete content processing workflow.

    Performs the full pipeline for processing a content item:
    1. Add to database session and flush to get ID
    2. Generate embeddings and chunks
    3. Push embeddings to Qdrant vector store
    4. Commit transaction and return result

    Args:
        item: SourceItem to process
        session: Database session for persistence
        tags: Optional tags to associate with the item (currently unused)

    Returns:
        Task result dictionary with processing status and metadata

    Side effects:
        - Adds item to database session
        - Commits database transaction
        - Stores vectors in Qdrant
    """
    status = "failed"
    session.add(item)
    session.flush()

    chunks_count = embed_source_item(item)
    session.flush()

    if not chunks_count:
        return create_task_result(item, status, content_length=getattr(item, "size", 0))

    try:
        push_to_qdrant([item])
        status = "processed"
        item.embed_status = "STORED"  # type: ignore
        logger.info(
            f"Successfully processed {type(item).__name__}: {getattr(item, 'title', 'unknown')} ({chunks_count} chunks embedded)"
        )
    except Exception as e:
        logger.error(f"Failed to push embeddings to Qdrant: {e}")
        logger.error(traceback.format_exc())
        item.embed_status = "FAILED"  # type: ignore
    session.commit()

    return create_task_result(item, status, content_length=getattr(item, "size", 0))


def safe_task_execution(func: Callable[..., dict]) -> Callable[..., dict]:
    """
    Decorator for safe task execution with comprehensive error handling.

    Wraps task functions to catch and log exceptions, ensuring tasks
    always return a result dictionary even when they fail.

    Args:
        func: Task function to wrap

    Returns:
        Wrapped function that handles exceptions gracefully

    Example:
        @safe_task_execution
        def my_task(arg1, arg2):
            # Task implementation
            return {"status": "success"}
    """

    def wrapper(*args, **kwargs) -> dict:
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Task {func.__name__} failed: {e}")
            traceback_str = traceback.format_exc()
            logger.error(traceback_str)

            notify_task_failure(
                task_name=func.__name__,
                error_message=str(e),
                task_args=args,
                task_kwargs=kwargs,
                traceback_str=traceback_str,
            )

            return {"status": "error", "error": str(e)}

    return wrapper
