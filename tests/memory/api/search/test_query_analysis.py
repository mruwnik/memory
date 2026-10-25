"""Tests for query_analysis module."""

import asyncio
import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from memory.agents.call import LLMError
from memory.agents.provider_keys import credential
from memory.api.search import query_analysis
from memory.common.db.models import BlogPost
from tests.conftest import unique_sha256
from tests.memory.agents.fakes import reply, submitted

NOW = datetime(2026, 10, 22, 3, 30, tzinfo=timezone.utc)

ANSWER = {
    "modalities": ["forum", "made-up"], "sources": [], "cleaned_query": "bottom line",
    "query_variants": ["v"], "recalled_content": ["The Bottom Line"],
    "start_date": None, "end_date": None,
}

load_modalities = query_analysis._get_available_modalities


@pytest.fixture(autouse=True)
def no_cached_analyses(monkeypatch):
    monkeypatch.setattr(query_analysis, "_analysis_cache", {})
    monkeypatch.setattr(query_analysis, "_modality_cache", frozenset())
    monkeypatch.setattr(query_analysis, "_cache_timestamp", 0)
    monkeypatch.setattr(query_analysis, "_get_available_modalities", lambda: frozenset())


async def slow():
    await asyncio.sleep(5)
    return submitted(ANSWER)


async def rate_limited():
    raise ConnectionError("429 rate limited")


async def model_refused():
    raise LLMError("answered without submitting a result")


@pytest.mark.asyncio
async def test_analysis_keeps_only_known_modalities(db_session, llm_key, scripted_llm, monkeypatch):
    monkeypatch.setattr(query_analysis, "_get_available_modalities", lambda: frozenset({"forum"}))
    scripted_llm.script = [submitted(ANSWER)]

    result = await query_analysis.analyze_query("that thing about the bottom line", NOW, credential(llm_key))

    assert (result.success, result.modalities, result.cleaned_query) == (True, {"forum"}, "bottom line")


def test_the_prompt_carries_the_clock_and_the_indexed_modalities():
    prompt = query_analysis.analysis_prompt("q", NOW, {"mail", "forum"})

    assert "Current date/time: " in prompt
    assert "  - forum\n  - mail\n" in prompt
    assert prompt.endswith("Query: q")


def test_the_prompt_says_when_nothing_is_indexed():
    assert "(no content indexed yet)" in query_analysis.analysis_prompt("q", NOW, set())


@pytest.mark.asyncio
async def test_the_prompt_names_content_types_but_nothing_about_other_users_content(
    db_session, admin_user, llm_key, scripted_llm, monkeypatch
):
    db_session.add(
        BlogPost(
            modality="blog",
            sha256=unique_sha256("secret"),
            url="https://admins-private-diary.example.org/entry",
            content="x",
            creator_id=admin_user.id,
        )
    )
    db_session.commit()
    monkeypatch.setattr(query_analysis, "_get_available_modalities", load_modalities)
    scripted_llm.script = [submitted(ANSWER)]

    await query_analysis.analyze_query("q", NOW, credential(llm_key))

    prompt = scripted_llm.requests[0]["messages"][0].content[0].text
    assert "  - blog\n" in prompt
    assert "admins-private-diary" not in prompt
    assert "1 item" not in prompt


@pytest.mark.asyncio
async def test_without_a_key_analysis_is_skipped(scripted_llm):
    result = await query_analysis.analyze_query("q", NOW, None)

    assert (result.success, result.cleaned_query, scripted_llm.requests) == (False, "q", [])


@pytest.mark.parametrize("answer", [slow, rate_limited, model_refused, reply("not a submission")])
@pytest.mark.asyncio
async def test_a_failed_analysis_searches_the_query_as_typed(
    db_session, llm_key, scripted_llm, caplog, answer
):
    scripted_llm.script = [answer]

    with caplog.at_level(logging.DEBUG, logger=query_analysis.__name__):
        result = await query_analysis.analyze_query("secret q", NOW, credential(llm_key), timeout=0.05)

    assert (result.success, result.cleaned_query, query_analysis._analysis_cache) == (False, "secret q", {})
    # The searcher's own failures aren't the operator's, and stay out of their logs.
    records = [r for r in caplog.records if r.name == query_analysis.__name__]
    assert [(r.levelno, "secret" in r.getMessage()) for r in records] == [(logging.WARNING, False)]


@pytest.mark.parametrize(
    "now, expected",
    [
        (NOW, "Thursday 2026-10-22 03:30 UTC"),
        (datetime(2026, 1, 4, 23, 5, tzinfo=timezone.utc), "Sunday 2026-01-04 23:05 UTC"),
        # A non-UTC clock is converted, so the model always reasons in UTC
        (
            datetime(2026, 10, 22, 1, 0, tzinfo=timezone(timedelta(hours=2))),
            "Wednesday 2026-10-21 23:00 UTC",
        ),
    ],
)
@pytest.mark.asyncio
async def test_the_clock_goes_in_the_user_message(db_session, llm_key, scripted_llm, now, expected):
    scripted_llm.script = [submitted(ANSWER)]

    await query_analysis.analyze_query("what happened yesterday", now, credential(llm_key))

    request = scripted_llm.requests[0]
    assert expected in request["messages"][0].content[0].text
    assert expected not in request["system"]


@pytest.mark.parametrize(
    "start, end, expected_start, expected_end",
    [
        ("2026-10-19", "2026-10-22", date(2026, 10, 19), date(2026, 10, 22)),
        ("2026-10-19", None, date(2026, 10, 19), None),
        (None, "2026-01-31", None, date(2026, 1, 31)),
        (None, None, None, None),
        # Reversed bounds are swapped rather than matching nothing
        ("2026-10-22", "2026-10-19", date(2026, 10, 19), date(2026, 10, 22)),
        # An unparseable date is dropped rather than failing the whole analysis
        ("last monday", "2026-10-22", None, date(2026, 10, 22)),
    ],
)
@pytest.mark.asyncio
async def test_analysis_parses_the_date_range(
    db_session, llm_key, scripted_llm, start, end, expected_start, expected_end
):
    scripted_llm.script = [submitted(ANSWER | {"start_date": start, "end_date": end})]

    result = await query_analysis.analyze_query("q", NOW, credential(llm_key))

    assert (result.success, result.start_date, result.end_date) == (True, expected_start, expected_end)


@pytest.mark.asyncio
async def test_the_analysis_cache_is_per_day(db_session, llm_key, scripted_llm):
    scripted_llm.script = [submitted(ANSWER), submitted(ANSWER)]
    key = credential(llm_key)

    await query_analysis.analyze_query("what happened today", NOW, key)
    await query_analysis.analyze_query("what happened today", NOW + timedelta(hours=1), key)
    await query_analysis.analyze_query("what happened today", NOW + timedelta(days=1), key)

    assert len(scripted_llm.requests) == 2


@pytest.mark.asyncio
async def test_analysis_loads_modalities_off_the_event_loop(db_session, llm_key, scripted_llm, monkeypatch):
    load_threads = []

    def record_thread():
        load_threads.append(threading.get_ident())
        return frozenset()

    monkeypatch.setattr(query_analysis, "_get_available_modalities", record_thread)
    scripted_llm.script = [submitted(ANSWER)]

    await query_analysis.analyze_query("anything", NOW, credential(llm_key))

    assert len(load_threads) == 1
    assert threading.get_ident() not in load_threads


@pytest.mark.parametrize(
    "cache, age, refreshes",
    [
        (frozenset({"mail"}), 0, False),
        (frozenset({"mail"}), 7200, True),
        (frozenset(), 0, True),
    ],
)
def test_modality_cache_refreshes_when_stale_or_empty(monkeypatch, cache, age, refreshes):
    monkeypatch.setattr(query_analysis, "_modality_cache", cache)
    monkeypatch.setattr(query_analysis, "_cache_timestamp", time.time() - age)

    with patch.object(query_analysis, "_refresh_modality_cache") as refresh:
        load_modalities()

    assert refresh.called == refreshes
