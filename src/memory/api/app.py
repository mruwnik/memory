"""
FastAPI application for the knowledge base.
"""

import base64
import io
from collections import defaultdict
import pathlib
from typing import Annotated, List, Optional, Callable
from fastapi import FastAPI, File, UploadFile, Query, HTTPException, Form
from fastapi.responses import FileResponse
import qdrant_client
from qdrant_client.http import models as qdrant_models
from PIL import Image
from pydantic import BaseModel

from memory.common import embedding, qdrant, extract, settings
from memory.common.collections import get_modality, TEXT_COLLECTIONS, ALL_COLLECTIONS
from memory.common.db.connection import make_session
from memory.common.db.models import Chunk, SourceItem

import logging

logger = logging.getLogger(__name__)

app = FastAPI(title="Knowledge Base API")


class AnnotatedChunk(BaseModel):
    id: str
    score: float
    metadata: dict
    preview: Optional[str | None] = None


class SearchResponse(BaseModel):
    collection: str
    results: List[dict]


class SearchResult(BaseModel):
    id: int
    size: int
    mime_type: str
    chunks: list[AnnotatedChunk]
    content: Optional[str] = None
    filename: Optional[str] = None


@app.get("/health")
def health_check():
    """Simple health check endpoint"""
    return {"status": "healthy"}


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


async def input_type(item: str | UploadFile) -> list[extract.DataChunk]:
    if not item:
        return []

    if isinstance(item, str):
        return extract.extract_text(item)
    content_type = item.content_type or "application/octet-stream"
    return extract.extract_data_chunks(content_type, await item.read())


@app.post("/search", response_model=list[SearchResult])
async def search(
    query: Optional[str] = Form(None),
    previews: Optional[bool] = Form(False),
    modalities: Annotated[list[str], Query()] = [],
    files: list[UploadFile] = File([]),
    limit: int = Query(10, ge=1, le=100),
    min_text_score: float = Query(0.3, ge=0.0, le=1.0),
    min_multimodal_score: float = Query(0.3, ge=0.0, le=1.0),
):
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
    upload_data = [
        chunk for item in [query, *files] for chunk in await input_type(item)
    ]
    logger.error(
        f"Querying chunks for {modalities}, query: {query}, previews: {previews}, upload_data: {upload_data}"
    )

    client = qdrant.get_qdrant_client()
    allowed_modalities = set(modalities or ALL_COLLECTIONS.keys())
    text_results = query_chunks(
        client,
        upload_data,
        allowed_modalities & TEXT_COLLECTIONS,
        embedding.embed_text,
        min_score=min_text_score,
        limit=limit,
    )
    multimodal_results = query_chunks(
        client,
        upload_data,
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


@app.get("/files/{path:path}")
def get_file_by_path(path: str):
    """
    Fetch a file by its path

    Parameters:
    - path: Path of the file to fetch (relative to FILE_STORAGE_DIR)

    Returns:
    - The file as a download
    """
    # Sanitize the path to prevent directory traversal
    sanitized_path = path.lstrip("/")
    if ".." in sanitized_path:
        raise HTTPException(status_code=400, detail="Invalid path")

    file_path = pathlib.Path(settings.FILE_STORAGE_DIR) / sanitized_path

    # Check if the file exists on disk
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found at path: {path}")

    return FileResponse(path=file_path, filename=file_path.name)


def main():
    """Run the FastAPI server in debug mode with auto-reloading."""
    import uvicorn

    uvicorn.run(
        "memory.api.app:app", host="0.0.0.0", port=8000, reload=True, log_level="debug"
    )


if __name__ == "__main__":
    main()
