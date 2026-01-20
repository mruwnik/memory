import hashlib
import logging
import time
from typing import Literal, cast

import voyageai

from memory.common import extract, settings
from memory.common.chunker import (
    DEFAULT_CHUNK_TOKENS,
    OVERLAP_TOKENS,
    chunk_text,
)
from memory.common.collections import Vector
from memory.common.db.models import Chunk, SourceItem

logger = logging.getLogger(__name__)

# Simple TTL cache for query embeddings to avoid repeated API calls
# Key: (query_hash, model), Value: (embedding, timestamp)
_query_embedding_cache: dict[tuple[str, str], tuple[Vector, float]] = {}
_CACHE_TTL_SECONDS = 300  # 5 minutes
_CACHE_MAX_SIZE = 100


def _get_query_cache_key(query_text: str, model: str) -> tuple[str, str]:
    """Generate cache key from query text and model."""
    query_hash = hashlib.md5(query_text.encode()).hexdigest()
    return (query_hash, model)


def _get_cached_embedding(query_text: str, model: str) -> Vector | None:
    """Get cached embedding if it exists and hasn't expired."""
    key = _get_query_cache_key(query_text, model)
    if key in _query_embedding_cache:
        embedding, timestamp = _query_embedding_cache[key]
        if time.time() - timestamp < _CACHE_TTL_SECONDS:
            return embedding
        else:
            del _query_embedding_cache[key]
    return None


def _cache_embedding(query_text: str, model: str, embedding: Vector) -> None:
    """Cache an embedding with current timestamp."""
    # Evict old entries if cache is full
    if len(_query_embedding_cache) >= _CACHE_MAX_SIZE:
        # Remove oldest entries
        now = time.time()
        expired = [k for k, (_, ts) in _query_embedding_cache.items()
                   if now - ts > _CACHE_TTL_SECONDS]
        for k in expired:
            del _query_embedding_cache[k]
        # If still full, remove oldest
        if len(_query_embedding_cache) >= _CACHE_MAX_SIZE:
            oldest = min(_query_embedding_cache.items(), key=lambda x: x[1][1])
            del _query_embedding_cache[oldest[0]]

    key = _get_query_cache_key(query_text, model)
    _query_embedding_cache[key] = (embedding, time.time())


class EmbeddingError(Exception):
    """Raised when embedding generation fails after retries."""

    pass


def as_string(
    chunk: extract.MulitmodalChunk | list[extract.MulitmodalChunk],
) -> str:
    if isinstance(chunk, str):
        return chunk.strip()
    if isinstance(chunk, list):
        return "\n".join(as_string(i) for i in chunk).strip()
    return ""


def embed_chunks(
    chunks: list[list[extract.MulitmodalChunk]],
    model: str = settings.TEXT_EMBEDDING_MODEL,
    input_type: Literal["document", "query"] = "document",
    max_retries: int = 3,
    retry_delay: float = 1.0,
) -> list[Vector]:
    """Embed chunks with retry logic for transient failures.

    Args:
        chunks: List of chunk lists to embed
        model: Embedding model to use
        input_type: Whether embedding documents or queries
        max_retries: Maximum number of retry attempts
        retry_delay: Base delay between retries (exponential backoff)

    Returns:
        List of embedding vectors

    Raises:
        EmbeddingError: If embedding fails after all retries
    """
    if not chunks:
        return []

    logger.debug(f"Embedding {len(chunks)} chunks with model {model}")
    vo = voyageai.Client()  # type: ignore

    last_error = None
    for attempt in range(max_retries):
        try:
            if model == settings.MIXED_EMBEDDING_MODEL:
                return vo.multimodal_embed(
                    chunks,  # type: ignore[arg-type]
                    model=model,
                    input_type=input_type,
                ).embeddings

            texts = [as_string(c) for c in chunks]
            return cast(
                list[Vector],
                vo.embed(texts, model=model, input_type=input_type).embeddings,
            )
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                delay = retry_delay * (2**attempt)
                logger.warning(
                    f"Embedding attempt {attempt + 1}/{max_retries} failed: {e}. "
                    f"Retrying in {delay:.1f}s..."
                )
                time.sleep(delay)
            else:
                logger.error(f"Embedding failed after {max_retries} attempts: {e}")

    raise EmbeddingError(
        f"Failed to generate embeddings after {max_retries} attempts"
    ) from last_error


def break_chunk(
    chunk: extract.DataChunk, chunk_size: int = DEFAULT_CHUNK_TOKENS
) -> list[extract.MulitmodalChunk]:
    result: list[extract.MulitmodalChunk] = []
    for c in chunk.data:
        if isinstance(c, str):
            result += chunk_text(c, chunk_size, OVERLAP_TOKENS)
        else:
            # Non-string items (e.g., images) are passed through directly
            result.append(c)
    return result


def embed_text(
    chunks: list[extract.DataChunk],
    model: str = settings.TEXT_EMBEDDING_MODEL,
    input_type: Literal["document", "query"] = "document",
    chunk_size: int = DEFAULT_CHUNK_TOKENS,
) -> list[Vector]:
    chunked_chunks = [break_chunk(chunk, chunk_size) for chunk in chunks if chunk.data]
    if not any(chunked_chunks):
        return []

    # For queries, check cache first
    if input_type == "query" and len(chunked_chunks) == 1:
        query_text = as_string(chunked_chunks[0])
        cached = _get_cached_embedding(query_text, model)
        if cached is not None:
            logger.debug(f"Query embedding cache hit for model {model}")
            return [cached]

    vectors = embed_chunks(chunked_chunks, model, input_type)

    # Cache query embeddings
    if input_type == "query" and len(chunked_chunks) == 1 and vectors:
        query_text = as_string(chunked_chunks[0])
        _cache_embedding(query_text, model, vectors[0])

    return vectors


def embed_mixed(
    items: list[extract.DataChunk],
    model: str = settings.MIXED_EMBEDDING_MODEL,
    input_type: Literal["document", "query"] = "document",
    chunk_size: int = DEFAULT_CHUNK_TOKENS,
) -> list[Vector]:
    chunked_chunks = [break_chunk(item, chunk_size) for item in items if item.data]
    if not chunked_chunks:
        return []

    # For queries, check cache first
    if input_type == "query" and len(chunked_chunks) == 1:
        query_text = as_string(chunked_chunks[0])
        cached = _get_cached_embedding(query_text, model)
        if cached is not None:
            logger.debug(f"Query embedding cache hit for model {model}")
            return [cached]

    vectors = embed_chunks(chunked_chunks, model, input_type)

    # Cache query embeddings
    if input_type == "query" and len(chunked_chunks) == 1 and vectors:
        query_text = as_string(chunked_chunks[0])
        _cache_embedding(query_text, model, vectors[0])

    return vectors


def embed_by_model(chunks: list[Chunk], model: str) -> list[Chunk]:
    model_chunks = [
        chunk for chunk in chunks if cast(str, chunk.embedding_model) == model
    ]
    if not model_chunks:
        return []

    vectors = embed_chunks([chunk.chunks for chunk in model_chunks], model)
    for chunk, vector in zip(model_chunks, vectors):
        chunk.vector = vector
    return model_chunks


def embed_source_item(item: SourceItem) -> list[Chunk]:
    chunks = list(item.data_chunks())
    if not chunks:
        return []

    text_chunks = embed_by_model(chunks, settings.TEXT_EMBEDDING_MODEL)
    mixed_chunks = embed_by_model(chunks, settings.MIXED_EMBEDDING_MODEL)
    return text_chunks + mixed_chunks
