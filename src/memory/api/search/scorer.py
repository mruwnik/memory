import asyncio
import logging
from collections.abc import Sequence

from memory.agents.call import run_agent
from memory.agents.provider_keys import Credential
from memory.agents.system import CHUNK_SCORE
from memory.api.search.types import chunk_score
from memory.common.db.models.source_item import Chunk

logger = logging.getLogger(__name__)

MAX_CONCURRENT_SCORES = 8


SCORE_CHUNK_PROMPT = """
Here is the query:
<query>{query}</query>

Here is the chunk:
<chunk>
    {chunk}
</chunk>
"""


async def score_chunk(query: str, chunk: Chunk, key: Credential) -> float | None:
    """The LLM's relevance score for `chunk`, or None for a chunk with no text to score."""
    try:
        data = chunk.data
    except OSError as e:
        # A chunk file missing on disk is the operator's to fix, unlike the LLM failures below.
        logger.error(f"Can't read chunk {chunk.id}: {type(e).__name__}")
        return None
    # Neutral messages carry text only, so image-only chunks keep the reranker's score.
    chunk_text = "\n".join(text for text in data if isinstance(text, str))
    if not chunk_text:
        return None
    result = await run_agent(
        CHUNK_SCORE.slug,
        SCORE_CHUNK_PROMPT.format(query=query, chunk=chunk_text),
        key=key,
    )
    assert result.data is not None  # an agent with a result schema returns data or raises
    return float(result.data["score"])


async def rank_chunks(
    query: str,
    chunks: Sequence[Chunk],
    key: Credential | None,
    min_score: float = 0,
    timeout: float = 5.0,
) -> list[Chunk]:
    if key is None:
        return list(chunks)
    # Each call opens DB sessions to check and charge the key's pool.
    slots = asyncio.Semaphore(MAX_CONCURRENT_SCORES)

    async def score(chunk: Chunk) -> float | None:
        async with slots:
            return await score_chunk(query, chunk, key)

    tasks = [asyncio.ensure_future(score(chunk)) for chunk in chunks]
    # One deadline for the batch: cancelling everything at once frees no slot for a new
    # call while the calls already sent finish (and are charged) in the background.
    _, pending = await asyncio.wait(tasks, timeout=timeout)
    for task in pending:
        task.cancel()
    scores = [None if t in pending else (t.exception() or t.result()) for t in tasks]
    # Mostly the searcher's own key being spent or refused, or the deadline: not the
    # operator's to act on, and the errors can carry the key's label.
    failures = sorted({type(s).__name__ for s in scores if isinstance(s, BaseException)})
    if pending:
        failures.append(f"{len(pending)} timed out")
    if failures:
        logger.warning(f"Scoring failed for some of {len(chunks)} chunks: {', '.join(failures)}")
    for chunk, llm_score in zip(chunks, scores):
        # A failed score keeps the reranker's: a rate limit shouldn't drop results under min_score.
        if isinstance(llm_score, float):
            chunk.relevance_score = llm_score
    return sorted(
        [chunk for chunk in chunks if chunk_score(chunk) >= min_score],
        key=chunk_score,
        reverse=True,
    )
