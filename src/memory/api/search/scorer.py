import asyncio
import json
import logging
from collections.abc import Sequence

from PIL import Image

from memory.common.db.models.source_item import Chunk
from memory.common import llms, settings

logger = logging.getLogger(__name__)


SCORE_CHUNK_SYSTEM_PROMPT = """
You are a helpful assistant that scores how relevant a chunk of text and/or image is to a query.

You are given a query and a chunk of text and/or an image. The chunk should be relevant to the query, but often won't be. Score the chunk based on how relevant it is to the query and assign a score on a gradient between 0 and 1, which is the probability that the chunk is relevant to the query.
"""

SCORE_CHUNK_PROMPT = """
Here is the query:
<query>{query}</query>

Here is the chunk:
<chunk>
    {chunk}
</chunk>
"""

SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {
            "type": "number",
            "description": "Probability between 0 and 1 that the chunk is relevant",
        }
    },
    "required": ["score"],
    "additionalProperties": False,
}


async def score_chunk(query: str, chunk: Chunk) -> Chunk:
    try:
        data = chunk.data
    except Exception as e:
        logger.error(f"Error getting chunk data: {e}")
        return chunk

    chunk_text = "\n".join(text for text in data if isinstance(text, str))
    images = [image for image in data if isinstance(image, Image.Image)]
    prompt = SCORE_CHUNK_PROMPT.format(query=query, chunk=chunk_text)
    try:
        response = await asyncio.to_thread(
            llms.summarize,
            prompt,
            settings.RANKER_MODEL,
            images=images,
            system_prompt=SCORE_CHUNK_SYSTEM_PROMPT,
            json_schema=SCORE_SCHEMA,
        )
        score = json.loads(response)["score"] if response else 0.0
    except Exception as e:
        logger.error(f"Error scoring chunk: {e}")
        score = 0.0

    chunk.relevance_score = float(score)
    return chunk


async def rank_chunks(
    query: str, chunks: Sequence[Chunk], min_score: float = 0
) -> list[Chunk]:
    calls = [score_chunk(query, chunk) for chunk in chunks]
    scored = await asyncio.gather(*calls)
    return sorted(
        [chunk for chunk in scored if chunk.relevance_score >= min_score],
        key=lambda x: x.relevance_score or 0,
        reverse=True,
    )
