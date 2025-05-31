"""
Search endpoints for the knowledge base API.
"""

import asyncio
import base64
from hashlib import sha256
import io
import logging
from collections import defaultdict
from typing import Any, Callable, Optional, TypedDict, NotRequired

import bm25s
import Stemmer
import qdrant_client
from PIL import Image
from pydantic import BaseModel
from qdrant_client.http import models as qdrant_models

from memory.common import embedding, extract, qdrant, settings
from memory.common.collections import (
    ALL_COLLECTIONS,
    MULTIMODAL_COLLECTIONS,
    TEXT_COLLECTIONS,
)
from memory.common.db.connection import make_session
from memory.common.db.models import Chunk

logger = logging.getLogger(__name__)


class AnnotatedChunk(BaseModel):
    id: str
    score: float
    metadata: dict
    preview: Optional[str | None] = None


class SourceData(BaseModel):
    """Holds source item data to avoid SQLAlchemy session issues"""

    id: int
    size: int | None
    mime_type: str | None
    filename: str | None
    content: str | dict | None
    content_length: int


class SearchResponse(BaseModel):
    collection: str
    results: list[dict]


class SearchResult(BaseModel):
    id: int
    size: int
    mime_type: str
    chunks: list[AnnotatedChunk]
    content: Optional[str | dict] = None
    filename: Optional[str] = None


class SearchFilters(TypedDict):
    subject: NotRequired[str | None]
    confidence: NotRequired[float]
    tags: NotRequired[list[str] | None]
    observation_types: NotRequired[list[str] | None]
    source_ids: NotRequired[list[int] | None]


async def with_timeout(
    call, timeout: int = 2
) -> list[tuple[SourceData, AnnotatedChunk]]:
    """
    Run a function with a timeout.

    Args:
        call: The function to run
        timeout: The timeout in seconds
    """
    try:
        return await asyncio.wait_for(call, timeout=timeout)
    except TimeoutError:
        logger.warning(f"Search timed out after {timeout}s")
        return []
    except Exception as e:
        logger.error(f"Search failed: {e}")
        return []


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
    source = chunk.source
    source_data = SourceData(
        id=source.id,
        size=source.size,
        mime_type=source.mime_type,
        filename=source.filename,
        content=source.display_contents,
        content_length=len(source.content) if source.content else 0,
    )

    return source_data, AnnotatedChunk(
        id=str(chunk.id),
        score=search_result.score,
        metadata=metadata,
        preview=serialize_item(chunk.data[0]) if chunk.data else None,
    )


def group_chunks(chunks: list[tuple[SourceData, AnnotatedChunk]]) -> list[SearchResult]:
    items = defaultdict(list)
    source_lookup = {}

    for source, chunk in chunks:
        items[source.id].append(chunk)
        source_lookup[source.id] = source

    return [
        SearchResult(
            id=source.id,
            size=source.size or source.content_length,
            mime_type=source.mime_type or "text/plain",
            filename=source.filename
            and source.filename.replace(
                str(settings.FILE_STORAGE_DIR).lstrip("/"), "/files"
            ),
            content=source.content,
            chunks=sorted(chunks, key=lambda x: x.score, reverse=True),
        )
        for source_id, chunks in items.items()
        for source in [source_lookup[source_id]]
    ]


def query_chunks(
    client: qdrant_client.QdrantClient,
    upload_data: list[extract.DataChunk],
    allowed_modalities: set[str],
    embedder: Callable,
    min_score: float = 0.0,
    limit: int = 10,
    filters: dict[str, Any] | None = None,
) -> dict[str, list[qdrant_models.ScoredPoint]]:
    if not upload_data or not allowed_modalities:
        return {}

    chunks = [chunk for chunk in upload_data if chunk.data]
    if not chunks:
        logger.error(f"No chunks to embed for {allowed_modalities}")
        return {}

    logger.error(f"Embedding {len(chunks)} chunks for {allowed_modalities}")
    for c in chunks:
        logger.error(f"Chunk: {c.data}")
    vectors = embedder([c.data for c in chunks], input_type="query")

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


async def search_bm25(
    query: str,
    modalities: list[str],
    limit: int = 10,
    filters: SearchFilters = SearchFilters(),
) -> list[tuple[SourceData, AnnotatedChunk]]:
    with make_session() as db:
        items_query = db.query(Chunk.id, Chunk.content).filter(
            Chunk.collection_name.in_(modalities)
        )
        if source_ids := filters.get("source_ids"):
            items_query = items_query.filter(Chunk.source_id.in_(source_ids))
        items = items_query.all()
        item_ids = {
            sha256(item.content.lower().strip().encode("utf-8")).hexdigest(): item.id
            for item in items
        }
        corpus = [item.content.lower().strip() for item in items]

    stemmer = Stemmer.Stemmer("english")
    corpus_tokens = bm25s.tokenize(corpus, stopwords="en", stemmer=stemmer)
    retriever = bm25s.BM25()
    retriever.index(corpus_tokens)

    query_tokens = bm25s.tokenize(query, stemmer=stemmer)
    results, scores = retriever.retrieve(
        query_tokens, k=min(limit, len(corpus)), corpus=corpus
    )

    item_scores = {
        item_ids[sha256(doc.encode("utf-8")).hexdigest()]: score
        for doc, score in zip(results[0], scores[0])
    }

    with make_session() as db:
        chunks = db.query(Chunk).filter(Chunk.id.in_(item_scores.keys())).all()
        results = []
        for chunk in chunks:
            # Prefetch all needed source data while in session
            source = chunk.source
            source_data = SourceData(
                id=source.id,
                size=source.size,
                mime_type=source.mime_type,
                filename=source.filename,
                content=source.display_contents,
                content_length=len(source.content) if source.content else 0,
            )

            annotated = AnnotatedChunk(
                id=str(chunk.id),
                score=item_scores[chunk.id],
                metadata=source.as_payload(),
                preview=None,
            )
            results.append((source_data, annotated))

        return results


async def search_embeddings(
    data: list[extract.DataChunk],
    previews: Optional[bool] = False,
    modalities: set[str] = set(),
    limit: int = 10,
    min_score: float = 0.3,
    filters: SearchFilters = SearchFilters(),
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
    query_filters = {
        "must": [
            {"key": "confidence", "range": {"gte": filters.get("confidence", 0.5)}},
        ],
    }
    if tags := filters.get("tags"):
        query_filters["must"] += [{"key": "tags", "match": {"any": tags}}]
    if observation_types := filters.get("observation_types"):
        query_filters["must"] += [
            {"key": "observation_type", "match": {"any": observation_types}}
        ]

    client = qdrant.get_qdrant_client()
    results = query_chunks(
        client,
        data,
        modalities,
        embedding.embed_text if not multimodal else embedding.embed_mixed,
        min_score=min_score,
        limit=limit,
        filters=query_filters,
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


async def search(
    data: list[extract.DataChunk],
    previews: Optional[bool] = False,
    modalities: list[str] = [],
    limit: int = 10,
    min_text_score: float = 0.3,
    min_multimodal_score: float = 0.3,
    filters: SearchFilters = {},
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
    allowed_modalities = set(modalities or ALL_COLLECTIONS.keys())

    text_embeddings_results = with_timeout(
        search_embeddings(
            data,
            previews,
            allowed_modalities & TEXT_COLLECTIONS,
            limit,
            min_text_score,
            filters,
            multimodal=False,
        )
    )
    multimodal_embeddings_results = with_timeout(
        search_embeddings(
            data,
            previews,
            allowed_modalities & MULTIMODAL_COLLECTIONS,
            limit,
            min_multimodal_score,
            filters,
            multimodal=True,
        )
    )
    bm25_results = with_timeout(
        search_bm25(
            " ".join([c for chunk in data for c in chunk.data if isinstance(c, str)]),
            modalities,
            limit=limit,
            filters=filters,
        )
    )

    results = await asyncio.gather(
        text_embeddings_results,
        multimodal_embeddings_results,
        bm25_results,
        return_exceptions=False,
    )

    results = group_chunks([c for r in results for c in r])
    return sorted(results, key=lambda x: max(c.score for c in x.chunks), reverse=True)
