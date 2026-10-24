import hashlib
import logging
import threading
import time
from typing import Any, Callable, Literal, cast

import voyageai
from PIL import Image
from sqlalchemy.orm import object_session

from memory.agents.call import LLMError, Record, run_agent_sync
from memory.agents.provider_keys import Credential, credential, ingest_key
from memory.agents.system import SUMMARISE
from memory.common import extract, settings, summarizer
from memory.common.llms import PROVIDER_ERRORS
from memory.common.llms.anthropic_provider import AnthropicProvider
from memory.common.chunker import (
    DEFAULT_CHUNK_TOKENS,
    OVERLAP_TOKENS,
    chunk_text,
)
from memory.common.collections import Vector
from memory.common.db.models import Chunk, SourceItem, SummaryRequest

logger = logging.getLogger(__name__)

# Simple TTL cache for query embeddings to avoid repeated API calls
# Key: (query_hash, model), Value: (embedding, timestamp)
_query_embedding_cache: dict[tuple[str, str], tuple[Vector, float]] = {}
# Query embedding runs in worker threads (asyncio.to_thread), concurrently.
_query_embedding_cache_lock = threading.Lock()
_CACHE_TTL_SECONDS = 300  # 5 minutes
_CACHE_MAX_SIZE = 100


def _get_query_cache_key(query_text: str, model: str) -> tuple[str, str]:
    """Generate cache key from query text and model."""
    query_hash = hashlib.md5(query_text.encode()).hexdigest()
    return (query_hash, model)


def _get_cached_embedding(query_text: str, model: str) -> Vector | None:
    """Get cached embedding if it exists and hasn't expired."""
    key = _get_query_cache_key(query_text, model)
    with _query_embedding_cache_lock:
        entry = _query_embedding_cache.get(key)
        if entry is None:
            return None
        embedding, timestamp = entry
        if time.time() - timestamp < _CACHE_TTL_SECONDS:
            return embedding
        del _query_embedding_cache[key]
    return None


def _cache_embedding(query_text: str, model: str, embedding: Vector) -> None:
    """Cache an embedding with current timestamp."""
    key = _get_query_cache_key(query_text, model)
    with _query_embedding_cache_lock:
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

    timeout = 60.0
    if input_type == "query":
        # A search is waiting: fail within seconds rather than spend ingestion's
        # long timeout and three attempts on a stalled connection.
        timeout, max_retries = settings.QUERY_EMBEDDING_TIMEOUT, min(max_retries, 2)

    logger.debug(f"Embedding {len(chunks)} chunks with model {model}")
    vo = voyageai.Client(api_key=settings.VOYAGE_API_KEY, timeout=timeout)  # type: ignore

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
        except Image.DecompressionBombError:
            # Deterministic: an oversized image fails identically every time.
            # Don't burn retry backoff on it — let it propagate to FAILED now.
            raise
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


def summariser(key: Credential) -> summarizer.Summarise:
    def summarise(prompt: str) -> dict[str, Any] | None:
        try:
            return run_agent_sync(
                SUMMARISE.slug, prompt, user=key.user_id, key=key, record=Record.COST
            ).data
        # A summary is optional: a provider outage shouldn't fail the item's ingest.
        except (LLMError, *PROVIDER_ERRORS) as e:
            logger.warning("summary skipped: %s", type(e).__name__)
            return None

    return summarise


def embed_source_item(
    item: SourceItem, before_embed: Callable[[], None] = lambda: None
) -> list[Chunk]:
    """Chunk and embed item. before_embed runs once the chunks are built, before
    any embedding API call, e.g. to end a DB transaction the calls would outlast."""
    db = object_session(item)
    found = ingest_key(db, item, SUMMARISE) if db is not None else None
    key = credential(found) if found is not None else None
    # Only the Anthropic Batches API is wired up; other keys summarise inline.
    batching = key is not None and key.provider == AnthropicProvider.provider and summarizer.batch_enabled()
    inline = summariser(key) if key is not None and not batching else None
    with summarizer.summarising(inline), summarizer.deferring(batching) as deferred:
        chunks = list(item.data_chunks())
    if chunks:
        # make_chunk's backref put the chunks in item.chunks but not in the session,
        # which before_embed's flush warns about. Callers assign the returned chunks.
        for chunk in chunks:
            chunk.source = None  # pyright: ignore[reportAttributeAccessIssue]
        before_embed()
        text_chunks = embed_by_model(chunks, settings.TEXT_EMBEDDING_MODEL)
        mixed_chunks = embed_by_model(chunks, settings.MIXED_EMBEDDING_MODEL)
        chunks = text_chunks + mixed_chunks

    # Only after a successful embed, so a failed item doesn't later gain a lone
    # summary chunk (which would also hide it from the empty-item reingest).
    # Assigning replaces, and so deletes, requests left over from an earlier embed,
    # including one from before the item lost its key.
    requests = [
        SummaryRequest(
            prompt=d.prompt,
            chunk_metadata=d.chunk_metadata,
            modality=d.modality,
            provider_key_id=key.id,
        )
        for d in deferred
        if key is not None
    ]
    item.summary_requests = requests if chunks else []
    return chunks
