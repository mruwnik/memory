"""Live checks of the Anthropic agent mapping. They cost money: run them by hand.

ANTHROPIC_API_KEY (or ANTHROPIC_API_KEY_FILE) must be set, plus --run-slow --run-live.
Runs pay with that key, stored as the test user's provider key.
"""

import os
from decimal import Decimal
from pathlib import Path

import pytest

from memory.agents.prebuilt import FACT_CHECK
from memory.agents.runner import execute_run
from memory.common import settings
from memory.common.db.models import AgentRun, AgentRunStep, LLMProviderKey
from memory.common.llms.anthropic_provider import ANTHROPIC_MODELS, AnthropicProvider
from memory.common.llms.neutral import (
    AgentUsage,
    Completion,
    GenerationParams,
    NeutralMessage,
    ReasoningLevel,
    Text,
    ToolCall,
    ToolResult,
)
from memory.common.llms.tools import ToolDefinition
from tests.memory.agents.fakes import FakeHub

KEY_FILE = os.getenv("ANTHROPIC_API_KEY_FILE")
LIVE_KEY = os.getenv("ANTHROPIC_API_KEY") or (
    Path(KEY_FILE).read_text().strip() if KEY_FILE else ""
)
# Spend so far, checked before each call so a run never passes the budget.
BUDGET_USD = Decimal("9")
SPENT: list[Decimal] = []

pytestmark = [
    pytest.mark.slow,
    pytest.mark.live,
    pytest.mark.skipif(not LIVE_KEY, reason="needs ANTHROPIC_API_KEY"),
]

ADD = ToolDefinition(
    name="add",
    description="Add two integers.",
    strict=True,
    input_schema={
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
        "additionalProperties": False,
    },
)
SEARCH = ToolDefinition(name="web_search", description="search", input_schema={})
FETCH = ToolDefinition(name="web_fetch", description="fetch", input_schema={})


@pytest.fixture(scope="module", autouse=True)
def spend_report():
    yield
    print(f"\nLIVE SPEND: ${sum(SPENT, Decimal(0))} over {len(SPENT)} model requests")


def provider(model: str) -> AnthropicProvider:
    return AnthropicProvider(api_key=LIVE_KEY, model=model)


def params(
    reasoning: ReasoningLevel = "low", max_output_tokens: int = 2000
) -> GenerationParams:
    return GenerationParams(
        max_output_tokens=max_output_tokens, reasoning=reasoning, temperature=None
    )


async def turn(p, messages, replay, tools, tool_choice="auto", **kw) -> Completion:
    assert sum(SPENT, Decimal(0)) < BUDGET_USD, "live test budget spent"
    completion = await p.acomplete(
        system="Be terse.",
        messages=messages,
        replay=replay,
        tools=tools,
        tool_choice=tool_choice,
        params=params(**kw),
    )
    SPENT.append(completion.cost)
    return completion


def user(text: str) -> NeutralMessage:
    return NeutralMessage(role="user", content=[Text(text=text)])


def priced_by_hand(model: str, usage: AgentUsage) -> Decimal:
    """The documented price list, applied independently of Pricing.cost."""
    rates = {  # input, output, cache-read multiplier, per web search
        "claude-opus-5-5": ("4", "20", "0.05", "0.01"),
        "claude-sonnet-5-5": ("2", "10", "0.05", "0.01"),
        "claude-haiku-5-5": ("0.10", "0.50", "0.1", "0"),
        "claude-fable-5-1": ("10", "50", "0.025", "0"),
    }
    inp, out, read, search = (Decimal(r) for r in rates[model])
    mtok = Decimal(1_000_000)
    return (
        usage.input_tokens * inp / mtok
        + usage.output_tokens * out / mtok
        + usage.cache_read_tokens * inp * read / mtok
        + usage.cache_write_tokens * inp * Decimal("1.25") / mtok
        + usage.web_searches * search
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model,reasoning",
    [
        ("claude-sonnet-5-5", "low"),
        ("claude-sonnet-5-5", "off"),
        ("claude-haiku-5-5", "low"),
        ("claude-haiku-5-5", "off"),
        ("claude-opus-5-5", "low"),
        ("claude-fable-5-1", "low"),
    ],
)
async def test_tool_loop_replays_thinking_and_prices_each_turn(model, reasoning):
    p = provider(model)
    messages = [
        user("Use the add tool to add 2 and 3, then reply with just the number.")
    ]

    first = await turn(p, messages, [None], [ADD], reasoning=reasoning)
    call = first.message.tool_calls()[0]
    messages += [
        first.message,
        NeutralMessage(
            role="user",
            content=[ToolResult(tool_call_id=call.id, name="add", text="5")],
        ),
    ]
    second = await turn(
        p, messages, [None, first.replay, None], [ADD], reasoning=reasoning
    )

    assert first.stop_reason == "tool_calls"
    assert "5" in second.message.text()
    assert first.cost == priced_by_hand(model, first.usage)
    assert second.cost == priced_by_hand(model, second.usage)


@pytest.mark.asyncio
async def test_history_without_replay_is_accepted_after_a_trim():
    """A trim drops replay for every turn, the latest tool-calling one included."""
    p = provider("claude-sonnet-5-5")
    messages = [
        user("Use the add tool to add 2 and 3, then reply with just the number.")
    ]
    first = await turn(p, messages, [None], [ADD])
    call = first.message.tool_calls()[0]
    messages += [
        first.message,
        NeutralMessage(
            role="user",
            content=[
                ToolResult(
                    tool_call_id=call.id, name="add", text="[cleared: add result]"
                )
            ],
        ),
    ]

    second = await turn(p, messages, [None, None, None], [ADD])

    assert second.stop_reason in ("end_turn", "tool_calls")


@pytest.mark.asyncio
async def test_haiku_accepts_forced_tool_choice_with_thinking_on():
    p = provider("claude-haiku-5-5")

    c = await turn(p, [user("hello")], [None], [ADD], tool_choice="required")

    assert ANTHROPIC_MODELS["claude-haiku-5-5"].capabilities.tool_choice_required
    assert c.message.tool_calls()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "schema",
    [
        {  # strict-compatible: sent with strict: true
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "population": {"type": "integer"},
            },
            "required": ["city", "population"],
            "additionalProperties": False,
        },
        {  # an optional field: not strict-compatible, so sent without strict
            "type": "object",
            "properties": {"city": {"type": "string"}, "note": {"type": "string"}},
            "required": ["city"],
        },
    ],
)
async def test_submit_result_schemas_are_accepted(schema):
    p = provider("claude-haiku-5-5")
    submit = ToolDefinition(
        name="submit_result",
        description="Submit the answer.",
        input_schema=schema,
        strict=True,
    )

    c = await turn(
        p,
        [user("What is the capital of France? Submit it with submit_result.")],
        [None],
        [submit],
        tool_choice="required",
    )

    call = c.message.tool_calls()[0]
    assert call.name == "submit_result"
    assert "paris" in str(call.input.get("city", "")).lower()


@pytest.mark.asyncio
async def test_native_web_tools_leave_no_dangling_provider_calls():
    p = provider("claude-sonnet-5-5")

    c = await turn(
        p,
        [
            user(
                "Search the web for the population of Iceland and answer in one sentence."
            )
        ],
        [None],
        [SEARCH, FETCH],
        max_output_tokens=4000,
    )

    blocks = (c.replay or {}).get("blocks", [])
    print("\nNATIVE WEB BLOCK TYPES:", [b["type"] for b in blocks])
    print("NEUTRAL:", [(b.type, getattr(b, "name", "")) for b in c.message.content])
    calls = {
        b.id
        for b in c.message.content
        if isinstance(b, ToolCall) and b.executed_by == "provider"
    }
    answered = {b.tool_call_id for b in c.message.content if isinstance(b, ToolResult)}
    assert calls
    assert calls <= answered
    assert c.usage.web_searches >= 1
    assert c.cost == priced_by_hand("claude-sonnet-5-5", c.usage)


@pytest.mark.asyncio
async def test_a_run_survives_trimming_on_a_live_model(
    db_session, regular_user, monkeypatch
):
    key = LLMProviderKey(
        user_id=regular_user.id, provider="anthropic", label="live", is_default=True
    )
    key.key = LIVE_KEY
    db_session.add(key)
    db_session.commit()
    monkeypatch.setattr(settings, "AGENT_CONTEXT_TRIM_AT_TOKENS", 0)
    monkeypatch.setattr(settings, "AGENT_CONTEXT_TRIM_TO_TOKENS", 0)
    run = AgentRun(
        user_id=regular_user.id,
        provider_key_id=key.id,
        scopes=["read"],
        spec={
            "system_prompt": "Follow the instructions exactly. Make one tool call per turn.",
            "model": "anthropic/claude-sonnet-5-5",
            "reasoning": "low",
            "max_output_tokens": 2000,
            "tools": ["echo"],
            "max_turns": 10,
        },
        input=(
            "Call the echo tool five times, one call per turn, with text 'one', 'two', "
            "'three', 'four', 'five'. Then reply with just the word done."
        ),
        max_cost_usd=Decimal("0.50"),
        trigger_kind="manual",
    )
    db_session.add(run)
    db_session.commit()
    hub = FakeHub({"echo": lambda i: (str(i.get("text", "")) * 50, False)})

    await execute_run(run.id, hub.factory())

    db_session.expire_all()
    finished = db_session.get(AgentRun, run.id)
    assert finished is not None
    SPENT.append(finished.cost_usd)
    steps = db_session.query(AgentRunStep).filter(AgentRunStep.run_id == run.id).all()
    assert finished.status == "done", finished.error
    assert "notice" in [s.kind for s in steps]


@pytest.mark.asyncio
async def test_the_fact_check_agent_answers_with_a_cited_verdict(db_session, regular_user):
    key = LLMProviderKey(
        user_id=regular_user.id, provider="anthropic", label="live", is_default=True
    )
    key.key = LIVE_KEY
    db_session.add(key)
    db_session.commit()
    spec = FACT_CHECK.spec("anthropic/claude-sonnet-5-5")
    run = AgentRun(
        user_id=regular_user.id,
        provider_key_id=key.id,
        scopes=["read"],
        spec=spec.model_copy(update={"max_output_tokens": 4000}).model_dump(mode="json"),
        input="Verify: the Eiffel Tower is in Berlin.",
        max_cost_usd=Decimal("0.15"),
        trigger_kind="manual",
    )
    db_session.add(run)
    db_session.commit()

    await execute_run(run.id, FakeHub({}, native=FACT_CHECK.tools).factory())

    db_session.expire_all()
    finished = db_session.get(AgentRun, run.id)
    assert finished is not None
    SPENT.append(finished.cost_usd)
    steps = db_session.query(AgentRunStep).filter(AgentRunStep.run_id == run.id).all()
    searches = [
        c
        for s in steps
        for c in s.content
        if c.get("type") == "tool_call" and c.get("name") == "web_search"
    ]
    assert finished.status == "done", finished.error
    assert searches
    assert finished.result is not None
    envelope = finished.result["data"]
    assert (envelope["verdict"], bool(envelope["sources"])) == ("refuted", True)


@pytest.mark.asyncio
async def test_cache_reads_are_priced_from_the_reported_usage():
    p = provider("claude-sonnet-5-5")
    # Long enough to pass the minimum cacheable prompt length.
    reference = " ".join(
        f"Fact {i}: the code word for item {i} is word{i}." for i in range(600)
    )
    messages = [
        user(f"{reference}\n\nWhat is the code word for item 7? Reply with just it.")
    ]

    first = await turn(p, messages, [None], [ADD], max_output_tokens=1000)
    second = await turn(p, messages, [None], [ADD], max_output_tokens=1000)

    print("\nCACHE USAGE:", first.usage, second.usage)
    assert first.usage.cache_write_tokens + first.usage.cache_read_tokens > 0
    assert second.usage.cache_read_tokens > 0
    assert second.cost == priced_by_hand("claude-sonnet-5-5", second.usage)
