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

from memory.agents.call import Record, run_agent
from memory.agents.provider_keys import Credential
from memory.agents.system import HYDE

logger = logging.getLogger(__name__)

# Cache for recent HyDE expansions (simple in-memory cache)
_hyde_cache: dict[str, str] = {}
_CACHE_MAX_SIZE = 100


async def expand_query_hyde(query: str, key: Credential | None, timeout: float = 5.0) -> str | None:
    """A hypothetical passage answering `query`, or None without a key or on failure/timeout."""
    if key is None:
        return None
    cache_key = query.lower().strip()
    if cache_key in _hyde_cache:
        return _hyde_cache[cache_key]
    try:
        result = await asyncio.wait_for(
            run_agent(HYDE.slug, f"Search query: {query}", user=key.user_id, key=key, record=Record.COST),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("HyDE expansion timed out")
        return None
    except Exception as e:
        # LLMError or any provider's transient error: search goes on without the passage.
        # Its text can carry the key's label.
        logger.warning(f"HyDE expansion failed: {type(e).__name__}")
        return None
    passage = result.text.strip()
    if not passage:
        return None
    if len(_hyde_cache) >= _CACHE_MAX_SIZE:
        for stale in list(_hyde_cache)[: _CACHE_MAX_SIZE // 2]:
            del _hyde_cache[stale]
    _hyde_cache[cache_key] = passage
    return passage
