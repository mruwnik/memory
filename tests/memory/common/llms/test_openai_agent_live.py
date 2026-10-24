"""A live agent run on OpenAI. It costs money: run it by hand.

OPENAI_API_KEY must be set, plus --run-slow --run-live. The run pays with that
key, stored as the test user's provider key.
"""

import os
from decimal import Decimal

import pytest

from memory.agents.call import Record, run_agent
from memory.agents.provider_keys import credential
from memory.agents.runner import execute_run
from memory.agents.system import SYSTEM_AGENTS
from memory.common.db.models import AgentRun, AgentRunStep, LLMProviderKey
from tests.memory.agents.fakes import FakeHub

LIVE_KEY = os.getenv("OPENAI_API_KEY", "")

pytestmark = [
    pytest.mark.slow,
    pytest.mark.live,
    pytest.mark.skipif(not LIVE_KEY, reason="needs OPENAI_API_KEY"),
]


def make_run(db_session, user, tools, input, max_cost):
    key = LLMProviderKey(user_id=user.id, provider="openai", label="live", is_default=True)
    key.key = LIVE_KEY
    db_session.add(key)
    db_session.commit()
    run = AgentRun(
        user_id=user.id,
        provider_key_id=key.id,
        scopes=["read"],
        spec={
            "system_prompt": "Follow the instructions exactly.",
            "model": "openai/gpt-6-luna",
            "reasoning": "low",
            "max_output_tokens": 2000,
            "tools": tools,
            "max_turns": 4,
        },
        input=input,
        max_cost_usd=max_cost,
        trigger_kind="manual",
    )
    db_session.add(run)
    db_session.commit()
    return run


def output_item_types(step: AgentRunStep) -> list[str]:
    return [b.get("type", "?") for b in ((step.replay or {}).get("blocks") or [])]


def finished_run(db_session, run):
    db_session.expire_all()
    finished = db_session.get(AgentRun, run.id)
    assert finished is not None
    steps = db_session.query(AgentRunStep).filter(AgentRunStep.run_id == run.id).all()
    print(
        f"\nLIVE RUN {finished.id}: {finished.status}, {finished.num_turns} turns, "
        f"usage {finished.usage}, cost ${finished.cost_usd}"
    )
    for s in sorted(steps, key=lambda s: s.seq):
        print(f"  step {s.seq} {s.kind}: {output_item_types(s)}")
    return finished, steps


@pytest.mark.asyncio
async def test_a_tool_calling_run_finishes_on_the_cheapest_model(db_session, regular_user):
    run = make_run(
        db_session,
        regular_user,
        ["echo"],
        "Call the echo tool once, then reply with exactly the text it returned.",
        Decimal("0.05"),
    )
    hub = FakeHub({"echo": lambda i: ("zebra-7", False)})

    await execute_run(run.id, hub.factory())

    finished, steps = finished_run(db_session, run)
    assert finished.status == "done", finished.error
    assert hub.calls == ["echo"]
    assert "zebra-7" in finished.result["text"]
    assert finished.usage["input_tokens"] >= 0
    assert finished.cost_usd > 0
    assert finished.cost_usd == sum(s.cost_usd or 0 for s in steps)


@pytest.mark.asyncio
async def test_a_native_web_search_run_is_billed_and_replays(db_session, regular_user):
    run = make_run(
        db_session,
        regular_user,
        ["web_search", "echo"],
        "Use web search once to find the current stable version of Python, "
        "then call the echo tool with that version, then reply with it.",
        Decimal("0.10"),
    )
    hub = FakeHub({"echo": lambda i: ("noted", False)}, native=("web_search",))

    await execute_run(run.id, hub.factory())

    finished, steps = finished_run(db_session, run)
    assert finished.status == "done", finished.error
    item_types = [output_item_types(s) for s in sorted(steps, key=lambda s: s.seq)]
    assert finished.usage["web_searches"] >= 1, item_types
    assert finished.usage["input_tokens"] >= 0
    assert finished.cost_usd >= Decimal("0.01")


SYSTEM_PROMPTS = {
    "system:summarise": "Summarise in one sentence and tag: Alice moved the launch to Friday.",
    "system:meeting-extract": "Alice: Bob, please send the deck by 2026-11-02. Bob: Will do.",
    "system:hyde": "Search query: when is the launch?",
    "system:query-analysis": "Content types: email, note. Today: 2026-10-25. Query: emails from last week about the launch",
    "system:chunk-score": "Query: launch date\n\nChunk: The launch moved to Friday.",
    "system:session-summary": "User: fix the failing login test. Assistant: fixed the expired fixture token.",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("slug", list(SYSTEM_AGENTS))
async def test_every_system_agent_answers_on_an_openai_key(db_session, regular_user, slug):
    key = LLMProviderKey(user_id=regular_user.id, provider="openai", label="live", is_default=True)
    key.key = LIVE_KEY
    db_session.add(key)
    db_session.commit()

    result = await run_agent(
        slug, SYSTEM_PROMPTS[slug], user=regular_user.id, key=credential(key), record=Record.FULL
    )

    run = db_session.get(AgentRun, result.run_id)
    print(f"\n{slug}: ${result.cost} {result.data or result.text!r}")
    assert run.spec["model"] == "openai/gpt-6-luna"
    assert (result.data is not None) == (SYSTEM_AGENTS[slug].result_schema is not None)
    assert result.text or result.data
