"""
HyDE (Hypothetical Document Embeddings) for query expansion.

When users search with vague or conceptual queries like "that article about not using
specific words", the query embedding may not match the actual document well. HyDE
generates a hypothetical document that would answer the query, then embeds that
instead. This bridges the gap between query terminology and document terminology.

Example:
- Query: "saying what you mean not using specific words"
- HyDE generates: "An article discussing the technique of 'tabooing' words - replacing
  specific terms with their definitions to clarify thinking and avoid confused debates..."
- The hypothetical document embeds closer to the actual "Taboo Your Words" article.

Reference: https://arxiv.org/abs/2212.10496
"""

import asyncio
import logging
from typing import Optional

from memory.common import settings
from memory.common.llms import create_provider, LLMSettings, Message

logger = logging.getLogger(__name__)

# Telling the model the passage is never shown and may be invented stops it
# refusing (or caveating at length) on queries about specific people and events;
# those refusals were both useless to embed and the slowest responses.
HYDE_SYSTEM_PROMPT = """You write hypothetical passages for HyDE retrieval in a private knowledge base (emails, chat, documents, notes).
Given a search query, write a short passage (2-3 sentences) that could appear in a document answering it.
The passage is never shown to anyone: it is only embedded to find real documents with similar wording.
So it is fine, and expected, to invent plausible names, dates, numbers and details; accuracy does not matter, vocabulary does.

Always write the passage. Never refuse, ask questions, add caveats, or comment on the query.
Write in the style of the likely source (email, chat message, article, issue, notes) and use the terminology such a document would use."""

# Cache for recent HyDE expansions (simple in-memory cache)
_hyde_cache: dict[str, str] = {}
_CACHE_MAX_SIZE = 100


async def expand_query_hyde(
    query: str,
    model: Optional[str] = None,
    timeout: float = 5.0,
) -> Optional[str]:
    """
    Expand a query using HyDE (Hypothetical Document Embeddings).

    Generates a hypothetical document passage that would answer the query,
    which can then be embedded for better semantic matching.

    Args:
        query: The user's search query
        model: LLM model to use (defaults to SUMMARIZER_MODEL)
        timeout: Maximum time to wait for LLM response

    Returns:
        A hypothetical document passage, or None if generation fails/times out
    """
    # Check cache first
    cache_key = query.lower().strip()
    if cache_key in _hyde_cache:
        logger.debug(f"HyDE cache hit for: {query[:50]}...")
        return _hyde_cache[cache_key]

    try:
        provider = create_provider(model=model or settings.SUMMARIZER_MODEL)

        messages = [
            Message.user(text=f"Search query: {query}")
        ]

        # A 2-3 sentence passage is ~150 tokens; the cap bounds the latency tail
        # under the search timeout, and a truncated passage still embeds fine.
        llm_settings = LLMSettings(max_tokens=512)

        # Run with timeout
        hypothetical_doc = await asyncio.wait_for(
            provider.agenerate(
                messages=messages,
                system_prompt=HYDE_SYSTEM_PROMPT,
                settings=llm_settings,
            ),
            timeout=timeout,
        )

        if hypothetical_doc:
            hypothetical_doc = hypothetical_doc.strip()

            # Cache the result
            if len(_hyde_cache) >= _CACHE_MAX_SIZE:
                # Simple eviction: clear half the cache
                keys_to_remove = list(_hyde_cache.keys())[:_CACHE_MAX_SIZE // 2]
                for key in keys_to_remove:
                    del _hyde_cache[key]
            _hyde_cache[cache_key] = hypothetical_doc

            logger.debug(f"HyDE expansion: '{query[:30]}...' -> '{hypothetical_doc[:50]}...'")
            return hypothetical_doc

    except asyncio.TimeoutError:
        logger.warning(f"HyDE expansion timed out for: {query[:50]}...")
    except Exception as e:
        logger.error(f"HyDE expansion failed: {e}")

    return None


async def get_hyde_chunks(
    query: str,
    model: Optional[str] = None,
    timeout: float = 5.0,
) -> list[str]:
    """
    Get both original query and HyDE-expanded version for embedding.

    Returns a list containing:
    1. The original query (always)
    2. The HyDE-expanded hypothetical document (if generation succeeds)

    This allows the search to match on both the literal query terms
    and the expanded semantic meaning.

    Args:
        query: The user's search query
        model: LLM model to use for HyDE expansion
        timeout: Maximum time to wait for HyDE generation

    Returns:
        List of strings to embed (original query + optional HyDE expansion)
    """
    chunks = [query]

    # Only expand queries that are vague/conceptual (more than a few words)
    # Short specific queries like "Taboo Your Words" don't need expansion
    word_count = len(query.split())
    if word_count >= 4:
        hyde_doc = await expand_query_hyde(query, model, timeout)
        if hyde_doc:
            chunks.append(hyde_doc)

    return chunks
