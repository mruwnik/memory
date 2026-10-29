"""Tests for HyDE (Hypothetical Document Embeddings) query expansion."""

import asyncio

import pytest

from memory.agents.call import LLMError
from memory.agents.provider_keys import credential
from memory.api.search import hyde
from tests.memory.agents.fakes import reply


@pytest.fixture(autouse=True)
def clear_hyde_cache():
    hyde._hyde_cache.clear()
    yield
    hyde._hyde_cache.clear()


async def slow():
    await asyncio.sleep(5)
    return reply("too late")


async def rate_limited():
    raise ConnectionError("429 rate limited")


async def model_refused():
    raise LLMError("stopped early")


@pytest.mark.asyncio
async def test_hyde_writes_a_passage_on_the_searchers_key(db_session, llm_key, scripted_llm):
    scripted_llm.script = [reply("A passage about tabooing words.")]

    passage = await hyde.expand_query_hyde("saying what you mean without words", credential(llm_key, None))

    assert passage == "A passage about tabooing words."
    assert scripted_llm.requests[0]["messages"][0].content[0].text == "Search query: saying what you mean without words"


@pytest.mark.asyncio
async def test_without_a_key_hyde_is_skipped_even_when_cached(scripted_llm):
    hyde._hyde_cache["a cached query"] = "cached passage"

    assert await hyde.expand_query_hyde("a cached query", None) is None
    assert scripted_llm.requests == []


@pytest.mark.asyncio
async def test_queries_differing_in_case_and_whitespace_share_a_passage(db_session, llm_key, scripted_llm):
    scripted_llm.script = [reply("  passage  ")]
    key = credential(llm_key, None)

    first = await hyde.expand_query_hyde("Some Query", key)
    second = await hyde.expand_query_hyde("  some query ", key)

    assert (first, second, len(scripted_llm.requests)) == ("passage", "passage", 1)


@pytest.mark.parametrize("answer", [reply(""), reply("   \n"), slow, rate_limited, model_refused])
@pytest.mark.asyncio
async def test_unusable_answers_give_no_passage_and_are_not_cached(db_session, llm_key, scripted_llm, answer):
    scripted_llm.script = [answer]

    assert await hyde.expand_query_hyde("q", credential(llm_key, None), timeout=0.05) is None
    assert hyde._hyde_cache == {}


@pytest.mark.asyncio
async def test_a_full_cache_drops_its_oldest_half(db_session, llm_key, scripted_llm):
    hyde._hyde_cache.update({f"old {i}": "p" for i in range(hyde._CACHE_MAX_SIZE)})
    scripted_llm.script = [reply("new passage")]

    await hyde.expand_query_hyde("new", credential(llm_key, None))

    assert len(hyde._hyde_cache) == hyde._CACHE_MAX_SIZE // 2 + 1
    assert ("old 0" in hyde._hyde_cache, hyde._hyde_cache["new"]) == (False, "new passage")
