"""
Cross-encoder reranking using VoyageAI's reranker.

Reranking improves search precision by using a cross-encoder model that
sees query and document together, rather than comparing embeddings separately.
"""

import asyncio
import logging
from typing import Optional

import voyageai

from memory.common import settings
from memory.common.db.models import Chunk

logger = logging.getLogger(__name__)

# VoyageAI reranker models
# rerank-2: More accurate, slower
# rerank-2-lite: Faster, slightly less accurate
DEFAULT_RERANK_MODEL = "rerank-2-lite"


async def rerank_chunks(
    query: str,
    chunks: list[Chunk],
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
        return chunks

    # Extract text content from chunks
    documents = []
    chunk_map = {}  # Map index to chunk

    for i, chunk in enumerate(chunks):
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

    if not documents:
        return chunks

    try:
        vo = voyageai.Client()
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

        return reranked

    except Exception as e:
        logger.warning(f"Reranking failed, returning original order: {e}")
        return chunks
