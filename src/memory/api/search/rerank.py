"""
Cross-encoder reranking using VoyageAI's reranker.

Reranking improves search precision by using a cross-encoder model that
sees query and document together, rather than comparing embeddings separately.
"""

import asyncio
import logging
from collections.abc import Sequence
from typing import Optional

import voyageai

from memory.common import settings
from memory.common.db.models import Chunk

logger = logging.getLogger(__name__)

# VoyageAI reranker models
# rerank-2: More accurate, slower
# rerank-2-lite: Faster, slightly less accurate
DEFAULT_RERANK_MODEL = "rerank-2-lite"


def merge_no_content_chunks(
    reranked: list[Chunk],
    no_content_chunks: Sequence[Chunk],
    top_k: Optional[int] = None,
) -> list[Chunk]:
    """Merge content-less chunks into a reranked list, sort, and truncate.

    Content-less chunks (e.g. rasterized PDF pages / images) can't be scored
    by the text cross-encoder. Don't strand them on the RRF scale — that's an
    order of magnitude below cross-encoder scores and would force every image
    chunk below every text chunk (and under any downstream min_score cut),
    regardless of how well it actually matched. Fall back to their raw
    embedding similarity, which is on a comparable 0..1 scale, so they compete
    fairly with reranked text. The merged list is re-sorted by relevance so the
    fallback-scored chunks interleave instead of always trailing, and truncated
    to ``top_k`` when set.

    The embedding-score fallback deliberately overwrites any recency/popularity/
    title boost ``_apply_boosts`` placed on these chunks: those boosts are tuned
    to the RRF/cross-encoder scale, not the embedding-similarity scale, so
    carrying them over would mix incomparable magnitudes. Reranked text chunks
    likewise lose their boosts (the cross-encoder score replaces them), so both
    paths stay consistent.
    """
    merged = list(reranked)
    for chunk in no_content_chunks:
        fallback = getattr(chunk, "embedding_score", 0.0) or 0.0
        if fallback:
            chunk.relevance_score = fallback
        merged.append(chunk)

    merged.sort(key=lambda c: c.relevance_score or 0, reverse=True)
    if top_k:
        merged = merged[:top_k]
    return merged


async def rerank_chunks(
    query: str,
    chunks: Sequence[Chunk],
    model: str = DEFAULT_RERANK_MODEL,
    top_k: Optional[int] = None,
) -> list[Chunk]:
    """
    Rerank chunks using VoyageAI's cross-encoder reranker.

    Cross-encoders are more accurate than bi-encoders (embeddings) because
    they see query and document together, allowing for deeper semantic matching.

    Args:
        query: The search query
        chunks: List of candidate chunks to rerank
        model: VoyageAI reranker model to use
        top_k: If set, only return top k results

    Returns:
        Chunks sorted by reranker relevance score
    """
    if not chunks:
        return []

    if not query.strip():
        return list(chunks)

    # Extract text content from chunks
    documents = []
    chunk_map = {}  # Map document index to chunk
    no_content_chunks = []  # Chunks without content (can't be reranked)

    for chunk in chunks:
        content = chunk.content or ""
        if not content and hasattr(chunk, "data"):
            try:
                data = chunk.data
                content = "\n".join(str(d) for d in data if isinstance(d, str))
            except Exception:
                pass

        if content:
            documents.append(content[:8000])  # VoyageAI has length limits
            chunk_map[len(documents) - 1] = chunk
        else:
            # Track chunks with no content - they'll be appended at the end
            no_content_chunks.append(chunk)

    if not documents:
        # Every candidate is content-less (pure image / PDF-page result set —
        # the exact case this fallback targets). Still apply the embedding-score
        # fallback and top_k truncation rather than returning RRF-scale scores.
        return merge_no_content_chunks([], no_content_chunks, top_k)

    try:
        vo = voyageai.Client(api_key=settings.VOYAGE_API_KEY)  # type: ignore[reportPrivateImportUsage]
        result = await asyncio.to_thread(
            vo.rerank,
            query=query,
            documents=documents,
            model=model,
            top_k=top_k or len(documents),
        )

        # Map results back to chunks with updated scores
        reranked = []
        for item in result.results:
            chunk = chunk_map.get(item.index)
            if chunk:
                chunk.relevance_score = item.relevance_score
                reranked.append(chunk)

        return merge_no_content_chunks(reranked, no_content_chunks, top_k)

    except Exception as e:
        logger.warning(f"Reranking failed, returning original order: {e}")
        return list(chunks)
