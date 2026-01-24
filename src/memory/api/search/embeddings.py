import logging
import asyncio
from typing import Any, Callable, cast, TYPE_CHECKING

import qdrant_client
from qdrant_client.http import models as qdrant_models

from memory.common import embedding, extract, qdrant
from memory.common.collections import (
    MULTIMODAL_COLLECTIONS,
    TEXT_COLLECTIONS,
)
from memory.api.search.types import SearchFilters

if TYPE_CHECKING:
    from memory.common.access_control import AccessFilter

logger = logging.getLogger(__name__)

def build_access_qdrant_filter(
    access_filter: "AccessFilter | None",
) -> list[dict[str, Any]]:
    """
    Build Qdrant filter conditions from an AccessFilter.

    Returns a list of "should" conditions where each condition represents
    a project + allowed sensitivities combination. At least one must match.

    If access_filter is None (superadmin), returns empty list (no filtering).
    If access_filter has no conditions, returns a filter that matches nothing.
    """
    if access_filter is None:
        # Superadmin - no access filtering
        return []

    if access_filter.is_empty():
        # User has no project access - match nothing
        # Use an impossible condition
        return [{"key": "project_id", "match": {"value": -1}}]

    # Build should conditions for each project membership
    should_conditions = []
    for condition in access_filter.conditions:
        # Each condition: project_id must match AND sensitivity must be in allowed set
        project_condition = {
            "must": [
                {"key": "project_id", "match": {"value": condition.project_id}},
                {"key": "sensitivity", "match": {"any": list(condition.sensitivities)}},
            ]
        }
        should_conditions.append(project_condition)

    return should_conditions


def build_person_filter(person_id: int) -> dict[str, Any]:
    """
    Build a Qdrant filter for person_id.

    Returns a filter that matches items where:
    - 'people' field is null/missing (item not associated with specific people), OR
    - 'people' field contains the given person_id

    This ensures items without people associations are still returned,
    while items with people associations only return if the person is included.

    Note: The 'should' clause in Qdrant requires at least one condition to match
    by default, so no explicit min_should is needed for "match any" semantics.
    """
    return {
        "should": [
            {"is_null": {"key": "people"}},
            {"key": "people", "match": {"any": [person_id]}},
        ],
    }


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
    # String match filters - exact match on metadata fields
    string_filters = ["folder_path", "sender", "domain", "author"]

    if key in list_filters:
        filters.append({"key": key, "match": {"any": val}})

    elif key in range_filters:
        return merge_range_filter(filters, key, val)

    elif key in string_filters:
        filters.append({"key": key, "match": {"value": val}})

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
        filters.append({"key": "source_id", "match": {"any": val}})

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
) -> dict[str, float]:
    """
    Search across knowledge base using text query and optional files.

    Parameters:
    - data: List of data to search in (e.g., text, images, files)
    - modalities: List of modalities to search in (e.g., "text", "photo", "doc")
    - limit: Maximum number of results
    - min_score: Minimum score to include in the search results
    - filters: Filters to apply to the search results
    - multimodal: Whether to search in multimodal collections

    Returns:
    - Dictionary mapping chunk IDs to their similarity scores
    """
    search_filters: list[dict[str, Any]] = []
    for key, val in filters.items():
        if key in ("access_filter", "person_id"):
            # Handle these filters separately (they create compound conditions)
            continue
        search_filters = merge_filters(search_filters, key, val)

    # Build the complete Qdrant filter
    qdrant_filter: dict[str, Any] = {}

    if search_filters:
        qdrant_filter["must"] = search_filters

    # Add person_id filter if present
    # This matches items where 'people' is null OR contains the person_id
    #
    # Note: This filtering approach differs from BM25 (bm25.py):
    # - Qdrant (here): Uses payload-based filtering on 'people' field. Any content
    #   type with a 'people' field will be filtered.
    # - BM25: Uses schema-based filtering via meeting_attendees table. Only Meetings
    #   are filtered; other content types are always returned.
    #
    # Currently only Meetings populate the 'people' field, so results are equivalent.
    # If other content types add 'people' in the future, Qdrant will filter them
    # but BM25 will not.
    if (person_id := filters.get("person_id")) is not None:
        person_filter = build_person_filter(person_id)
        if "must" not in qdrant_filter:
            qdrant_filter["must"] = []
        qdrant_filter["must"].append(person_filter)

    # Add access control filter if present
    # Wrap in a nested Filter inside must for consistent structure
    access_filter = filters.get("access_filter")
    access_conditions = build_access_qdrant_filter(access_filter)
    if access_conditions:
        if "must" not in qdrant_filter:
            qdrant_filter["must"] = []
        # Distinguish between "no access" and "has project access" cases:
        #
        # - "No access" case: build_access_qdrant_filter returns a single condition
        #   with a "key" field (e.g., {"key": "project_id", "match": {"value": -1}}).
        #   This is an impossible condition that matches nothing.
        #
        # - "Has access" case: Returns list of nested {"must": [...]} conditions,
        #   one per project. These don't have a top-level "key" field.
        #
        # We detect the "no access" case by checking for a single condition with "key".
        is_no_access_condition = len(access_conditions) == 1 and "key" in access_conditions[0]
        if is_no_access_condition:
            # User has no project access - return empty results without querying Qdrant
            return {}

        # Multiple project conditions - wrap as nested Filter with should
        # The 'should' clause requires at least one condition to match by default
        # This ensures proper AND semantics with other must conditions
        access_nested_filter = {
            "should": access_conditions,
        }
        qdrant_filter["must"].append(access_nested_filter)

    client = qdrant.get_qdrant_client()
    results = await query_chunks(
        client,
        data,
        modalities,
        embedding.embed_text if not multimodal else embedding.embed_mixed,
        min_score=min_score,
        limit=limit,
        filters=qdrant_filter if qdrant_filter else None,
    )
    search_results = {k: results.get(k, []) for k in modalities}

    # Return chunk IDs with their scores (take max score if chunk appears multiple times)
    found_chunks: dict[str, float] = {}
    for collection_results in search_results.values():
        for r in collection_results:
            chunk_id = str(r.id)
            if chunk_id not in found_chunks or r.score > found_chunks[chunk_id]:
                found_chunks[chunk_id] = r.score
    return found_chunks


async def search_chunks_embeddings(
    data: list[extract.DataChunk],
    modalities: set[str] = set(),
    limit: int = 10,
    filters: SearchFilters = SearchFilters(),
    timeout: int = 2,
) -> dict[str, float]:
    """
    Search chunks using embeddings across text and multimodal collections.

    Returns:
    - Dictionary mapping chunk IDs to their similarity scores
    """
    # Note: Multimodal embeddings typically produce higher similarity scores,
    # so we use a higher threshold (0.4) to maintain selectivity.
    # Text embeddings produce lower scores, so we use 0.25.
    all_results = await asyncio.gather(
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
    # Merge scores, taking max if chunk appears in both
    merged_scores: dict[str, float] = {}
    for result_dict in all_results:
        for chunk_id, score in result_dict.items():
            if chunk_id not in merged_scores or score > merged_scores[chunk_id]:
                merged_scores[chunk_id] = score
    return merged_scores
