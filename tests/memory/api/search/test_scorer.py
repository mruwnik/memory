"""Tests for scorer module."""

import asyncio
from unittest.mock import MagicMock

import pytest
from PIL import Image

from memory.agents.call import LLMError
from memory.agents.provider_keys import credential
from memory.api.search import scorer
from tests.memory.agents.fakes import submitted


pytestmark = pytest.mark.usefixtures("serial_llm_charges")


def chunk_with_text(text, score=None):
    return MagicMock(data=[text], relevance_score=score)


async def slow():
    await asyncio.sleep(5)
    return submitted({"score": 0.0})


async def rate_limited():
    raise ConnectionError("429 rate limited")


async def model_refused():
    raise LLMError("answered without submitting a result")


@pytest.mark.asyncio
async def test_chunks_are_scored_on_the_searchers_key(db_session, llm_key, scripted_llm):
    scripted_llm.script = [submitted({"score": 0.9}), submitted({"score": 0.1})]
    chunks = [chunk_with_text("relevant"), chunk_with_text("noise")]

    ranked = await scorer.rank_chunks("query", chunks, credential(llm_key), min_score=0.3)

    assert [c.relevance_score for c in ranked] == [0.9]


@pytest.mark.asyncio
async def test_ranked_chunks_are_sorted_by_score(db_session, llm_key, scripted_llm):
    scripted_llm.script = [submitted({"score": s}) for s in (0.3, 0.9, 0.5)]
    chunks = [chunk_with_text(t) for t in ("a", "b", "c")]

    ranked = await scorer.rank_chunks("query", chunks, credential(llm_key))

    assert [c.relevance_score for c in ranked] == [0.9, 0.5, 0.3]


@pytest.mark.asyncio
async def test_without_a_key_chunks_keep_their_scores(scripted_llm):
    chunks = [chunk_with_text("a", score=0.5)]

    assert await scorer.rank_chunks("query", chunks, None) == chunks
    assert scripted_llm.requests == []


@pytest.mark.parametrize("answer", [slow, rate_limited, model_refused])
@pytest.mark.asyncio
async def test_a_failed_score_keeps_the_rerankers_score(db_session, llm_key, scripted_llm, answer):
    scripted_llm.script = [answer]
    chunk = chunk_with_text("text", score=0.6)

    result = await scorer.score_chunk("query", chunk, credential(llm_key), timeout=0.05)

    assert (result is chunk, result.relevance_score) == (True, 0.6)


@pytest.mark.asyncio
async def test_image_only_chunks_keep_their_score_unscored(db_session, llm_key, scripted_llm):
    chunk = MagicMock(data=[Image.new("RGB", (1, 1))], relevance_score=0.4)

    result = await scorer.score_chunk("query", chunk, credential(llm_key))

    assert (result.relevance_score, scripted_llm.requests) == (0.4, [])


@pytest.mark.asyncio
async def test_unreadable_chunks_are_returned_unscored(db_session, llm_key, scripted_llm):
    chunk = MagicMock(relevance_score=0.2)
    type(chunk).data = property(lambda self: (_ for _ in ()).throw(OSError("gone")))

    result = await scorer.score_chunk("query", chunk, credential(llm_key))

    assert (result is chunk, scripted_llm.requests) == (True, [])
