import base64
import io
import logging
from typing import Any, Callable, Optional, cast

import qdrant_client
from PIL import Image
from qdrant_client.http import models as qdrant_models

from memory.common import embedding, extract, qdrant
from memory.common.db.connection import make_session
from memory.common.db.models import Chunk
from memory.api.search.utils import SourceData, AnnotatedChunk, SearchFilters

logger = logging.getLogger(__name__)


def annotated_chunk(
    chunk: Chunk, search_result: qdrant_models.ScoredPoint, previews: bool
) -> tuple[SourceData, AnnotatedChunk]:
    def serialize_item(item: bytes | str | Image.Image) -> str | None:
        if not previews and not isinstance(item, str):
            return None
        if not previews and isinstance(item, str):
            return item[:100]

        if isinstance(item, Image.Image):
            buffer = io.BytesIO()
            format = item.format or "PNG"
            item.save(buffer, format=format)
            mime_type = f"image/{format.lower()}"
            return f"data:{mime_type};base64,{base64.b64encode(buffer.getvalue()).decode('utf-8')}"
        elif isinstance(item, bytes):
            return base64.b64encode(item).decode("utf-8")
        elif isinstance(item, str):
            return item
        else:
            raise ValueError(f"Unsupported item type: {type(item)}")

    metadata = search_result.payload or {}
    metadata = {
        k: v
        for k, v in metadata.items()
        if k not in ["content", "filename", "size", "content_type", "tags"]
    }

    # Prefetch all needed source data while in session
    return SourceData.from_chunk(chunk), AnnotatedChunk(
        id=str(chunk.id),
        score=search_result.score,
        metadata=metadata,
        preview=serialize_item(chunk.data[0]) if chunk.data else None,
        search_method="embeddings",
    )


def query_chunks(
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

    return {
        collection: [
            r
            for vector in vectors
            for r in qdrant.search_vectors(
                client=client,
                collection_name=collection,
                query_vector=vector,
                limit=limit,
                filter_params=filters,
            )
            if r.score >= min_score
        ]
        for collection in allowed_modalities
    }


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
        filters.append({"key": key, "match": val})

    return filters


async def search_embeddings(
    data: list[extract.DataChunk],
    previews: Optional[bool] = False,
    modalities: set[str] = set(),
    limit: int = 10,
    min_score: float = 0.3,
    filters: SearchFilters = {},
    multimodal: bool = False,
) -> list[tuple[SourceData, AnnotatedChunk]]:
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

    print(search_filters)
    client = qdrant.get_qdrant_client()
    results = query_chunks(
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
    with make_session() as db:
        chunks = db.query(Chunk).filter(Chunk.id.in_(found_chunks.keys())).all()
        return [
            annotated_chunk(chunk, found_chunks[str(chunk.id)], previews or False)
            for chunk in chunks
        ]
