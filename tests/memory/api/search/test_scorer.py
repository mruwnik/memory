"""Tests for scorer module."""

import asyncio
import logging
import time
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


async def malformed():
    return submitted({"score": "high"})


@pytest.mark.parametrize("answer", [slow, rate_limited, model_refused, malformed])
@pytest.mark.asyncio
async def test_a_failed_score_keeps_the_rerankers_score(db_session, llm_key, scripted_llm, answer):
    scripted_llm.script = [answer]
    chunk = chunk_with_text("text", score=0.6)

    ranked = await scorer.rank_chunks("query", [chunk], credential(llm_key), timeout=0.05)

    assert [(c, c.relevance_score) for c in ranked] == [(chunk, 0.6)]


@pytest.mark.asyncio
async def test_failed_scores_log_one_warning_per_search_without_user_text(
    db_session, llm_key, scripted_llm, caplog
):
    async def pool_spent():
        raise LLMError(f"your API key {llm_key.label!r} reached its daily limit")

    scripted_llm.script = [pool_spent] * 3
    chunks = [chunk_with_text("private words") for _ in range(3)]

    with caplog.at_level(logging.DEBUG, logger=scorer.__name__):
        await scorer.rank_chunks("secret query", chunks, credential(llm_key))

    messages = [(r.levelno, r.getMessage()) for r in caplog.records if r.name == scorer.__name__]
    assert messages == [(logging.WARNING, "Scoring failed for some of 3 chunks: LLMError")]


@pytest.mark.asyncio
async def test_scoring_runs_a_bounded_number_of_calls_at_once(db_session, llm_key, scripted_llm):
    active, peak = 0, 0

    async def answer():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return submitted({"score": 0.5})

    chunk_count = scorer.MAX_CONCURRENT_SCORES * 2
    scripted_llm.script = [answer] * chunk_count
    chunks = [chunk_with_text(str(i)) for i in range(chunk_count)]

    ranked = await scorer.rank_chunks("query", chunks, credential(llm_key))

    assert len(ranked) == chunk_count
    assert 1 < peak <= scorer.MAX_CONCURRENT_SCORES


@pytest.mark.asyncio
async def test_a_slow_provider_bounds_both_the_wait_and_the_calls_in_flight(
    db_session, llm_key, scripted_llm
):
    active, peak = 0, 0

    async def stalled():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.5)
        active -= 1
        return submitted({"score": 0.5})

    chunk_count = scorer.MAX_CONCURRENT_SCORES * 2
    scripted_llm.script = [stalled] * chunk_count
    chunks = [chunk_with_text(str(i), score=0.4) for i in range(chunk_count)]

    started = time.monotonic()
    ranked = await scorer.rank_chunks("query", chunks, credential(llm_key), timeout=0.2)
    elapsed = time.monotonic() - started
    await asyncio.sleep(0.6)  # let the calls already sent finish and be charged

    assert elapsed < 0.4
    assert peak == scorer.MAX_CONCURRENT_SCORES
    assert [c.relevance_score for c in ranked] == [0.4] * chunk_count


@pytest.mark.asyncio
async def test_image_only_chunks_keep_their_score_unscored(db_session, llm_key, scripted_llm):
    chunk = MagicMock(data=[Image.new("RGB", (1, 1))], relevance_score=0.4)

    ranked = await scorer.rank_chunks("query", [chunk], credential(llm_key))

    assert ([c.relevance_score for c in ranked], scripted_llm.requests) == ([0.4], [])


@pytest.mark.asyncio
async def test_unreadable_chunks_keep_their_score_unscored(db_session, llm_key, scripted_llm):
    chunk = MagicMock(relevance_score=0.2)
    type(chunk).data = property(lambda self: (_ for _ in ()).throw(OSError("gone")))

    ranked = await scorer.rank_chunks("query", [chunk], credential(llm_key))

    assert ([c.relevance_score for c in ranked], scripted_llm.requests) == ([0.2], [])
