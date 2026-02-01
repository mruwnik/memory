"""
Content processing utilities for memory workers.

This module provides core functionality for processing content items through
the complete workflow: existence checking, content hashing, embedding generation,
vector storage, and result tracking.
"""

from collections import defaultdict
import hashlib
import inspect
import time
import traceback
import logging
from collections.abc import Mapping
from typing import Any, Callable, Sequence, TypeVar, cast

from sqlalchemy import or_
from memory.common import embedding, qdrant
from memory.common.db.models import SourceItem, Chunk
from memory.common.discord import notify_task_failure
from memory.common.metrics import record_metric

logger = logging.getLogger(__name__)

# TypeVar for model classes (any SQLAlchemy model)
T = TypeVar("T")


def clear_item_chunks(item: SourceItem, session) -> int:
    """
    Delete all chunks for a source item from both Qdrant and PostgreSQL.

    Args:
        item: The source item whose chunks should be deleted
        session: Database session

    Returns:
        Number of chunks deleted
    """
    if not item.chunks:
        return 0

    chunk_ids = [str(c.id) for c in item.chunks if c.id]
    count = len(chunk_ids)

    # Delete from Qdrant
    if chunk_ids:
        client = qdrant.get_qdrant_client()
        try:
            qdrant.delete_points(client, item.modality, chunk_ids)
        except IOError as e:
            logger.error(f"Error deleting chunks from Qdrant for item {item.id}: {e}")

    # Delete from PostgreSQL
    for chunk in item.chunks:
        session.delete(chunk)

    session.flush()
    logger.info(f"Deleted {count} chunks for {item.__class__.__name__} {item.id}")
    return count


# Configuration for which task parameters to log in metrics
# Keys are function names, values are lists of parameter names to capture
TASK_LOGGED_PARAMS: dict[str, list[str]] = {
    "sync_account": ["account_id"],
    "process_message": ["account_id", "message_id"],
    "execute_scheduled_call": ["scheduled_call_id"],
    "sync_lesswrong": ["since_date"],
    "sync_github_repo": ["repo_id"],
    "sync_google_account": ["account_id"],
    "sync_calendar_account": ["account_id"],
    # Add more tasks as needed
}


def check_content_exists(
    session,
    model_class: type[T],
    **kwargs: Any,
) -> T | None:
    """
    Check if content already exists in the database.

    Searches for existing content by any of the provided attributes
    (typically URL, file_path, or SHA256 hash).
    Uses OR logic - returns content if ANY attribute matches.

    Args:
        session: Database session for querying
        model_class: The SourceItem model class to search in
        **kwargs: Attribute-value pairs to search for

    Returns:
        Existing SourceItem if found, None otherwise
    """
    # Return None if no search criteria provided
    if not kwargs:
        return None

    filters = []
    for key, value in kwargs.items():
        if hasattr(model_class, key):
            filters.append(getattr(model_class, key) == value)

    # Return None if none of the provided attributes exist on the model
    if not filters:
        return None

    # Use OR logic to find content matching any of the provided attributes
    query = session.query(model_class).filter(or_(*filters))
    return query.first()


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
    embed_status based on success, skip, or failure.

    Args:
        source_item: The SourceItem to embed

    Returns:
        Number of successfully embedded chunks

    Side effects:
        - Sets source_item.chunks with generated chunks
        - Sets source_item.embed_status to "QUEUED", "SKIPPED", or "FAILED"
    """
    # Check if content should be embedded (e.g., not too short)
    if not source_item.should_embed:
        source_item.embed_status = "SKIPPED"  # type: ignore
        logger.debug(
            f"Skipping embedding for {type(source_item).__name__}: "
            f"{getattr(source_item, 'title', 'unknown')} (should_embed=False)"
        )
        return 0

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
        # Reflect actual embed_status in task result
        status = "skipped" if item.embed_status == "SKIPPED" else "failed"
        return create_task_result(item, status, content_length=getattr(item, "size", 0))

    # Commit DB changes first to ensure chunks are persisted.
    # This prevents orphaned vectors if we push to Qdrant but DB commit fails.
    # Status stays QUEUED to indicate processing is in progress but vectors
    # haven't been pushed to Qdrant yet. If process crashes here, items remain
    # QUEUED and can be retried.
    item.embed_status = "QUEUED"  # type: ignore
    session.commit()

    try:
        push_to_qdrant([item])
        status = "processed"
        item.embed_status = "STORED"  # type: ignore
        logger.info(
            f"Successfully processed {type(item).__name__}: {getattr(item, 'title', 'unknown')} ({chunks_count} chunks embedded)"
        )
    except Exception as e:
        status = "failed"
        item.embed_status = "FAILED"  # type: ignore
        logger.error(f"Failed to push embeddings to Qdrant: {e}")
        logger.error(traceback.format_exc())
    session.commit()

    return create_task_result(item, status, content_length=getattr(item, "size", 0))


def extract_task_params(
    func: Callable, args: tuple, kwargs: dict
) -> dict[str, Any]:
    """Extract parameters to log based on TASK_LOGGED_PARAMS config."""
    param_names = TASK_LOGGED_PARAMS.get(func.__name__, [])
    if not param_names:
        return {}

    try:
        sig = inspect.signature(func)
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        all_params = dict(bound.arguments)
    except Exception:
        return {}

    return {k: v for k, v in all_params.items() if k in param_names}


def safe_task_execution(
    func: Callable[..., Mapping[str, Any]],
) -> Callable[..., Mapping[str, Any]]:
    """
    Decorator for safe task execution with comprehensive error handling and metrics.

    Wraps task functions to:
    - Record execution timing and status to metrics
    - Log exceptions and notify on failures
    - Allow Celery to handle retries

    Note: This decorator has its own metric recording rather than using the @profile
    decorator from memory.common.metrics because Celery tasks require:
    - Extraction of Celery-specific context (queue name, retry count from task.request)
    - Integration with task failure notifications (notify_task_failure)
    - Handling of bound tasks (first arg is self with request attribute)
    The @profile decorator is designed for general functions and doesn't have access
    to Celery's runtime context.

    Args:
        func: Task function to wrap

    Returns:
        Wrapped function that records metrics, logs exceptions, and re-raises for retry

    Example:
        @app.task(bind=True)
        @safe_task_execution
        def my_task(self, arg1, arg2):
            # Task implementation
            return {"status": "success"}
    """
    from functools import wraps

    @wraps(func)
    def wrapper(*args, **kwargs) -> Mapping[str, Any]:
        start_time = time.perf_counter()
        status = "success"

        try:
            return func(*args, **kwargs)
        except Exception as e:
            status = "failure"
            logger.error(f"Task {func.__name__} failed: {e}")
            traceback_str = traceback.format_exc()
            logger.error(traceback_str)

            # Check if this is a bound task and if retries are exhausted
            task_self = args[0] if args and hasattr(args[0], "request") else None
            is_final_retry = (
                task_self
                and hasattr(task_self, "request")
                and task_self.request.retries >= task_self.max_retries
            )

            # Notify on final failure only
            if is_final_retry or task_self is None:
                notify_task_failure(
                    task_name=func.__name__,
                    error_message=str(e),
                    task_args=args[1:] if task_self else args,
                    task_kwargs=kwargs,
                    traceback_str=traceback_str,
                )

            # Re-raise to allow Celery retries
            raise
        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000
            labels = extract_task_params(func, args, kwargs)

            # Try to get queue name from Celery task
            task_self = args[0] if args and hasattr(args[0], "request") else None
            if task_self and hasattr(task_self, "request"):
                request = task_self.request
                if hasattr(request, "delivery_info"):
                    queue = request.delivery_info.get("routing_key")
                    if queue:
                        labels["queue"] = queue
                if hasattr(request, "retries"):
                    labels["retry_count"] = request.retries

            # Wrap metric recording to avoid masking original exceptions
            try:
                record_metric(
                    metric_type="task",
                    name=func.__name__,
                    duration_ms=duration_ms,
                    status=status,
                    labels=labels,
                )
            except Exception as metric_err:
                logger.warning(f"Failed to record metric for {func.__name__}: {metric_err}")

    return wrapper
