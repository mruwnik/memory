import asyncio
import logging
from collections.abc import Sequence

from memory.agents.call import Record, run_agent
from memory.agents.provider_keys import Credential
from memory.agents.system import CHUNK_SCORE
from memory.common.db.models.source_item import Chunk

logger = logging.getLogger(__name__)


SCORE_CHUNK_PROMPT = """
Here is the query:
<query>{query}</query>

Here is the chunk:
<chunk>
    {chunk}
</chunk>
"""


async def score_chunk(query: str, chunk: Chunk, key: Credential, timeout: float = 5.0) -> Chunk:
    try:
        data = chunk.data
    except Exception as e:
        logger.error(f"Error getting chunk data: {e}")
        return chunk

    # Neutral messages carry text only, so image-only chunks keep the reranker's score.
    chunk_text = "\n".join(text for text in data if isinstance(text, str))
    if not chunk_text:
        return chunk
    try:
        result = await asyncio.wait_for(
            run_agent(
                CHUNK_SCORE.slug,
                SCORE_CHUNK_PROMPT.format(query=query, chunk=chunk_text),
                user=key.user_id,
                key=key,
                record=Record.COST,
            ),
            timeout=timeout,
        )
    except Exception as e:
        # LLMError, a timeout or a transient provider error. Keep the reranker's score: a rate
        # limit shouldn't drop results under min_score.
        logger.error(f"Error scoring chunk: {e!r}")
        return chunk

    assert result.data is not None  # an agent with a result schema returns data or raises
    chunk.relevance_score = float(result.data["score"])
    return chunk


async def rank_chunks(
    query: str, chunks: Sequence[Chunk], key: Credential | None, min_score: float = 0
) -> list[Chunk]:
    if key is None:
        return list(chunks)
    scored = await asyncio.gather(*(score_chunk(query, chunk, key) for chunk in chunks))
    return sorted(
        [chunk for chunk in scored if (chunk.relevance_score or 0) >= min_score],
        key=lambda x: x.relevance_score or 0,
        reverse=True,
    )
