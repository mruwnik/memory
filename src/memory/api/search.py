"""
Search endpoints for the knowledge base API.
"""

import base64
import io
from collections import defaultdict
from typing import Callable, Optional
import logging

from PIL import Image
from pydantic import BaseModel
import qdrant_client
from qdrant_client.http import models as qdrant_models

from memory.common import embedding, qdrant, extract, settings
from memory.common.collections import TEXT_COLLECTIONS, ALL_COLLECTIONS
from memory.common.db.connection import make_session
from memory.common.db.models import Chunk, SourceItem

logger = logging.getLogger(__name__)


class AnnotatedChunk(BaseModel):
    id: str
    score: float
    metadata: dict
    preview: Optional[str | None] = None


class SearchResponse(BaseModel):
    collection: str
    results: list[dict]


class SearchResult(BaseModel):
    id: int
    size: int
    mime_type: str
    chunks: list[AnnotatedChunk]
    content: Optional[str] = None
    filename: Optional[str] = None


def annotated_chunk(
    chunk: Chunk, search_result: qdrant_models.ScoredPoint, previews: bool
) -> tuple[SourceItem, AnnotatedChunk]:
    def serialize_item(item: bytes | str | Image.Image) -> str | None:
        if not previews and not isinstance(item, str):
            return None

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
    return chunk.source, AnnotatedChunk(
        id=str(chunk.id),
        score=search_result.score,
        metadata=metadata,
        preview=serialize_item(chunk.data[0]) if chunk.data else None,
    )


def group_chunks(chunks: list[tuple[SourceItem, AnnotatedChunk]]) -> list[SearchResult]:
    items = defaultdict(list)
    for source, chunk in chunks:
        items[source].append(chunk)

    return [
        SearchResult(
            id=source.id,
            size=source.size or len(source.content),
            mime_type=source.mime_type or "text/plain",
            filename=source.filename
            and source.filename.replace(
                str(settings.FILE_STORAGE_DIR).lstrip("/"), "/files"
            ),
            content=source.display_contents,
            chunks=sorted(chunks, key=lambda x: x.score, reverse=True),
        )
        for source, chunks in items.items()
    ]


def query_chunks(
    client: qdrant_client.QdrantClient,
    upload_data: list[extract.DataChunk],
    allowed_modalities: set[str],
    embedder: Callable,
    min_score: float = 0.0,
    limit: int = 10,
) -> dict[str, list[qdrant_models.ScoredPoint]]:
    if not upload_data:
        return {}

    chunks = [chunk for data_chunk in upload_data for chunk in data_chunk.data]
    if not chunks:
        logger.error(f"No chunks to embed for {allowed_modalities}")
        return {}

    vector = embedder(chunks, input_type="query")[0]

    return {
        collection: [
            r
            for r in qdrant.search_vectors(
                client=client,
                collection_name=collection,
                query_vector=vector,
                limit=limit,
            )
            if r.score >= min_score
        ]
        for collection in allowed_modalities
    }


async def search(
    data: list[extract.DataChunk],
    previews: Optional[bool] = False,
    modalities: list[str] = [],
    limit: int = 10,
    min_text_score: float = 0.3,
    min_multimodal_score: float = 0.3,
) -> list[SearchResult]:
    """
    Search across knowledge base using text query and optional files.

    Parameters:
    - query: Optional text search query
    - modalities: List of modalities to search in (e.g., "text", "photo", "doc")
    - files: Optional files to include in the search context
    - limit: Maximum number of results per modality

    Returns:
    - List of search results sorted by score
    """
    client = qdrant.get_qdrant_client()
    allowed_modalities = set(modalities or ALL_COLLECTIONS.keys())
    text_results = query_chunks(
        client,
        data,
        allowed_modalities & TEXT_COLLECTIONS,
        embedding.embed_text,
        min_score=min_text_score,
        limit=limit,
    )
    multimodal_results = query_chunks(
        client,
        data,
        allowed_modalities,
        embedding.embed_mixed,
        min_score=min_multimodal_score,
        limit=limit,
    )
    search_results = {
        k: text_results.get(k, []) + multimodal_results.get(k, [])
        for k in allowed_modalities
    }

    found_chunks = {
        str(r.id): r for results in search_results.values() for r in results
    }
    with make_session() as db:
        chunks = db.query(Chunk).filter(Chunk.id.in_(found_chunks.keys())).all()
        logger.error(f"Found chunks: {chunks}")

        results = group_chunks(
            [
                annotated_chunk(chunk, found_chunks[str(chunk.id)], previews or False)
                for chunk in chunks
            ]
        )
    return sorted(results, key=lambda x: max(c.score for c in x.chunks), reverse=True)
