import logging
import asyncio
from typing import Any, Callable, cast

import qdrant_client
from qdrant_client.http import models as qdrant_models

from memory.common import embedding, extract, qdrant
from memory.common.collections import (
    MULTIMODAL_COLLECTIONS,
    TEXT_COLLECTIONS,
)
from memory.api.search.types import SearchFilters

logger = logging.getLogger(__name__)


async def query_chunks(
    client: qdrant_client.QdrantClient,
    upload_data: list[extract.DataChunk],
    allowed_modalities: set[str],
    embedder: Callable,
    min_score: float = 0.3,
    limit: int = 10,
    filters: dict[str, Any] | None = None,
) -> dict[str, list[qdrant_models.ScoredPoint]]:
    if not upload_data or not allowed_modalities:
        return {}

    chunks = [chunk for chunk in upload_data if chunk.data]
    if not chunks:
        logger.error(f"No chunks to embed for {allowed_modalities}")
        return {}

    vectors = embedder(chunks, input_type="query")

    # Create all search tasks to run in parallel
    search_tasks = []
    task_metadata = []  # Keep track of which collection and vector each task corresponds to

    for collection in allowed_modalities:
        for vector in vectors:
            task = asyncio.to_thread(
                qdrant.search_vectors,
                client=client,
                collection_name=collection,
                query_vector=vector,
                limit=limit,
                filter_params=filters,
            )
            search_tasks.append(task)
            task_metadata.append((collection, vector))

    # Run all searches in parallel
    if not search_tasks:
        return {}

    search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

    # Group results by collection
    results_by_collection: dict[str, list[qdrant_models.ScoredPoint]] = {
        collection: [] for collection in allowed_modalities
    }

    for (collection, _), result in zip(task_metadata, search_results):
        if isinstance(result, Exception):
            logger.error(f"Search failed for collection {collection}: {result}")
            continue

        # Filter by min_score and add to collection results
        result_list = cast(list[qdrant_models.ScoredPoint], result)
        filtered_results = [r for r in result_list if r.score >= min_score]
        results_by_collection[collection].extend(filtered_results)

    return results_by_collection


def merge_range_filter(
    filters: list[dict[str, Any]], key: str, val: Any
) -> list[dict[str, Any]]:
    direction, field = key.split("_", maxsplit=1)
    item = next((f for f in filters if f["key"] == field), None)
    if not item:
        item = {"key": field, "range": {}}
        filters.append(item)

    if direction == "min":
        item["range"]["gte"] = val
    elif direction == "max":
        item["range"]["lte"] = val
    return filters


def merge_filters(
    filters: list[dict[str, Any]], key: str, val: Any
) -> list[dict[str, Any]]:
    if not val and val != 0:
        return filters

    list_filters = ["tags", "recipients", "observation_types", "authors"]
    range_filters = [
        "min_sent_at",
        "max_sent_at",
        "min_published",
        "max_published",
        "min_size",
        "max_size",
        "min_created_at",
        "max_created_at",
    ]
    if key in list_filters:
        filters.append({"key": key, "match": {"any": val}})

    elif key in range_filters:
        return merge_range_filter(filters, key, val)

    elif key == "min_confidences":
        confidence_filters = [
            {
                "key": f"confidence.{confidence_type}",
                "range": {"gte": min_confidence_score},
            }
            for confidence_type, min_confidence_score in cast(dict, val).items()
        ]
        filters.extend(confidence_filters)

    elif key == "source_ids":
        filters.append({"key": "id", "match": {"any": val}})

    else:
        # Log and ignore unknown filter keys to prevent injection
        logger.warning(f"Unknown filter key ignored: {key}")

    return filters


async def search_chunks(
    data: list[extract.DataChunk],
    modalities: set[str] = set(),
    limit: int = 10,
    min_score: float = 0.3,
    filters: SearchFilters = {},
    multimodal: bool = False,
) -> list[str]:
    """
    Search across knowledge base using text query and optional files.

    Parameters:
    - data: List of data to search in (e.g., text, images, files)
    - previews: Whether to include previews in the search results
    - modalities: List of modalities to search in (e.g., "text", "photo", "doc")
    - limit: Maximum number of results
    - min_score: Minimum score to include in the search results
    - filters: Filters to apply to the search results
    - multimodal: Whether to search in multimodal collections
    """
    search_filters = []
    for key, val in filters.items():
        search_filters = merge_filters(search_filters, key, val)

    client = qdrant.get_qdrant_client()
    results = await query_chunks(
        client,
        data,
        modalities,
        embedding.embed_text if not multimodal else embedding.embed_mixed,
        min_score=min_score,
        limit=limit,
        filters={"must": search_filters} if search_filters else None,
    )
    search_results = {k: results.get(k, []) for k in modalities}

    found_chunks = {
        str(r.id): r for results in search_results.values() for r in results
    }
    return list(found_chunks.keys())


async def search_chunks_embeddings(
    data: list[extract.DataChunk],
    modalities: set[str] = set(),
    limit: int = 10,
    filters: SearchFilters = SearchFilters(),
    timeout: int = 2,
) -> list[str]:
    # Note: Multimodal embeddings typically produce higher similarity scores,
    # so we use a higher threshold (0.4) to maintain selectivity.
    # Text embeddings produce lower scores, so we use 0.25.
    all_ids = await asyncio.gather(
        asyncio.wait_for(
            search_chunks(
                data,
                modalities & TEXT_COLLECTIONS,
                limit,
                0.25,
                filters,
                False,
            ),
            timeout,
        ),
        asyncio.wait_for(
            search_chunks(
                data,
                modalities & MULTIMODAL_COLLECTIONS,
                limit,
                0.4,
                filters,
                True,
            ),
            timeout,
        ),
    )
    return list({id for ids in all_ids for id in ids})
