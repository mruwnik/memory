from decimal import Decimal

import pytest
from anthropic.types import Message

from memory.common.llms.anthropic_provider import ANTHROPIC_MODELS, AnthropicProvider
from memory.common.llms.neutral import (
    AgentUsage,
    GenerationParams,
    NeutralMessage,
    Reasoning,
    Text,
    ToolCall,
    ToolResult,
)
from memory.common.llms.tools import ToolDefinition

SEARCH = ToolDefinition(
    name="web_search", description="d", input_schema={"type": "object"}
)
CORE = ToolDefinition(
    name="core_search",
    description="Search memory",
    input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
)


def provider(model="claude-sonnet-5-5"):
    return AnthropicProvider(api_key="k", model=model)


def params(**overrides):
    defaults = {
        "max_output_tokens": 16_000,
        "reasoning": "medium",
        "temperature": None,
        "extras": {},
    }
    return GenerationParams(**(defaults | overrides))


def request(p, tools=(), tool_choice="auto", **overrides):
    return p.build_agent_request(
        system="sys",
        messages=[NeutralMessage(role="user", content=[Text(text="hi")])],
        replay=[None],
        tools=list(tools),
        tool_choice=tool_choice,
        params=params(**overrides),
    )


@pytest.mark.parametrize(
    "model,reasoning,thinking,effort",
    [
        ("claude-sonnet-5-5", "off", {"type": "between_tools"}, "low"),
        ("claude-haiku-5-5", "off", {"type": "disabled"}, "low"),
        (
            "claude-opus-5-5",
            "high",
            {"type": "adaptive", "display": "summarized"},
            "high",
        ),
        (
            "claude-fable-5-1",
            "low",
            {"type": "adaptive", "display": "summarized"},
            "low",
        ),
    ],
)
def test_reasoning_maps_to_thinking_and_effort(model, reasoning, thinking, effort):
    kwargs = request(provider(model), reasoning=reasoning)

    assert kwargs["thinking"] == thinking
    assert kwargs["output_config"] == {"effort": effort}


@pytest.mark.parametrize(
    "model,expected",
    [
        ("claude-opus-5-5", [{"type": "web_search_20260209", "name": "web_search"}]),
        ("claude-sonnet-5-5", [{"type": "web_search_20260209", "name": "web_search"}]),
    ],
)
def test_native_builtins_use_server_tool_types(model, expected):
    assert request(provider(model), tools=[SEARCH])["tools"] == expected


def test_runner_tools_send_schema_and_strict_only_when_schema_is_strict():
    strict = ToolDefinition(
        name="submit_result",
        description="d",
        strict=True,
        input_schema={
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": ["a"],
            "additionalProperties": False,
        },
    )
    loose = ToolDefinition(
        name="loose", description="d", strict=True, input_schema={"type": "object"}
    )

    tools = request(provider(), tools=[CORE, strict, loose])["tools"]

    assert tools == [
        {
            "name": "core_search",
            "description": "Search memory",
            "input_schema": CORE.input_schema,
        },
        {
            "name": "submit_result",
            "description": "d",
            "input_schema": strict.input_schema,
            "strict": True,
        },
        {"name": "loose", "description": "d", "input_schema": {"type": "object"}},
    ]


@pytest.mark.parametrize(
    "tool_choice,expected", [("auto", None), ("required", {"type": "any"})]
)
def test_tool_choice(tool_choice, expected):
    assert (
        request(provider("claude-haiku-5-5"), tool_choice=tool_choice).get(
            "tool_choice"
        )
        == expected
    )


def test_request_caches_and_never_sends_temperature_when_unset():
    kwargs = request(provider())

    assert kwargs["cache_control"] == {"type": "ephemeral"}
    assert "temperature" not in kwargs


def test_replay_is_sent_verbatim_for_assistant_turns():
    p = provider()
    blocks = [
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "text", "text": "x"},
    ]

    msg = p.agent_message(
        NeutralMessage(role="assistant", content=[Text(text="x")]),
        {"provider": "anthropic", "blocks": blocks},
    )

    assert msg == {"role": "assistant", "content": blocks}


def test_neutral_fallback_when_replay_is_missing():
    p = provider()
    assistant = NeutralMessage(
        role="assistant",
        content=[
            Reasoning(text="thinking..."),
            ToolCall(
                id="s1", name="web_search", input={"query": "q"}, executed_by="provider"
            ),
            ToolResult(tool_call_id="s1", name="web_search", text="- a <https://a>"),
            ToolCall(id="t1", name="core_search", input={"q": "x"}),
        ],
    )
    results = NeutralMessage(
        role="user",
        content=[
            ToolResult(
                tool_call_id="t1", name="core_search", text="found", is_error=False
            )
        ],
    )

    assert p.agent_message(assistant, None)["content"] == [
        {"type": "text", "text": '[web_search {"query": "q"}]'},
        {"type": "text", "text": "[web_search result]\n- a <https://a>"},
        {"type": "tool_use", "id": "t1", "name": "core_search", "input": {"q": "x"}},
    ]
    assert p.agent_message(results, None)["content"] == [
        {
            "type": "tool_result",
            "tool_use_id": "t1",
            "content": "found",
            "is_error": False,
        },
    ]


def test_completion_is_neutral_with_replay_and_normalised_stop():
    blocks = [
        {"type": "thinking", "thinking": "hmm", "signature": "sig"},
        {
            "type": "server_tool_use",
            "id": "s1",
            "name": "web_search",
            "input": {"query": "q"},
        },
        {
            "type": "web_search_tool_result",
            "tool_use_id": "s1",
            "content": [
                {"type": "web_search_result", "title": "A", "url": "https://a"}
            ],
        },
        {"type": "tool_use", "id": "t1", "name": "core_search", "input": {"q": "x"}},
    ]

    c = provider().agent_completion(
        blocks, "tool_use", AgentUsage(input_tokens=5), Decimal(0)
    )

    assert c.stop_reason == "tool_calls"
    assert c.replay == {"provider": "anthropic", "blocks": blocks}
    assert c.message.content == [
        Reasoning(text="hmm"),
        ToolCall(
            id="s1", name="web_search", input={"query": "q"}, executed_by="provider"
        ),
        ToolResult(tool_call_id="s1", name="web_search", text="- A <https://a>"),
        ToolCall(id="t1", name="core_search", input={"q": "x"}),
    ]


def test_server_tool_error_is_an_error_result():
    blocks = [
        {
            "type": "server_tool_use",
            "id": "s1",
            "name": "web_fetch",
            "input": {"url": "u"},
        },
        {
            "type": "web_fetch_tool_result",
            "tool_use_id": "s1",
            "content": {
                "type": "web_fetch_tool_error",
                "error_code": "url_not_accessible",
            },
        },
    ]

    c = provider().agent_completion(blocks, "end_turn", AgentUsage(), Decimal(0))

    assert c.message.content[1] == ToolResult(
        tool_call_id="s1",
        name="web_fetch",
        text="error: url_not_accessible",
        is_error=True,
    )


def message(content, stop_reason, **usage):
    return Message.model_validate(
        {
            "id": "m",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5-5",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5} | usage,
        }
    )


class FakeStream:
    def __init__(self, msg):
        self.msg = msg

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_final_message(self):
        return self.msg


class FakeClient:
    def __init__(self, msgs):
        self.msgs = list(msgs)
        self.sent = []
        self.messages = self

    def stream(self, **kwargs):
        self.sent.append(kwargs)
        return FakeStream(self.msgs.pop(0))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def complete(p, msgs, monkeypatch):
    client = FakeClient(msgs)
    monkeypatch.setattr(p, "agent_client", lambda: client)
    completion = await p.acomplete(
        system="s",
        messages=[NeutralMessage(role="user", content=[Text(text="hi")])],
        replay=[None],
        tools=[],
        tool_choice="auto",
        params=params(),
    )
    return completion, client


@pytest.mark.asyncio
async def test_a_paused_turn_is_returned_to_the_caller(monkeypatch):
    paused = message(
        [
            {
                "type": "server_tool_use",
                "id": "s1",
                "name": "web_search",
                "input": {"query": "q"},
            }
        ],
        "pause_turn",
        server_tool_use={"web_search_requests": 1, "web_fetch_requests": 0},
    )

    c, client = await complete(provider(), [paused], monkeypatch)

    assert c.stop_reason == "paused"
    assert c.usage == AgentUsage(input_tokens=10, output_tokens=5, web_searches=1)
    assert [b["type"] for b in c.replay["blocks"]] == ["server_tool_use"]
    assert len(client.sent) == 1


def test_pricing_table_covers_the_documented_models():
    assert set(ANTHROPIC_MODELS) == {
        "claude-opus-5-5",
        "claude-sonnet-5-5",
        "claude-haiku-5-5",
        "claude-fable-5-1",
    }
    assert (
        "off" not in ANTHROPIC_MODELS["claude-opus-5-5"].capabilities.reasoning_levels
    )
    assert ANTHROPIC_MODELS["claude-haiku-5-5"].capabilities.tool_choice_required


@pytest.mark.parametrize(
    "model,usage,expected",
    [
        (
            "claude-opus-5-5",
            AgentUsage(input_tokens=1_000_000, cache_read_tokens=1_000_000),
            Decimal("4.20"),
        ),
        (
            "claude-sonnet-5-5",
            AgentUsage(cache_read_tokens=1_000_000, cache_write_tokens=1_000_000),
            Decimal("2.60"),
        ),
        (
            "claude-fable-5-1",
            AgentUsage(output_tokens=1_000_000, cache_read_tokens=1_000_000),
            Decimal("50.25"),
        ),
        (
            "claude-haiku-5-5",
            AgentUsage(input_tokens=50_000, cache_read_tokens=200_000),
            Decimal("0.035"),
        ),
    ],
)
def test_documented_prices(model, usage, expected):
    assert ANTHROPIC_MODELS[model].pricing.cost(usage) == expected


def test_any_result_for_a_provider_call_is_a_tool_result():
    blocks = [
        {
            "type": "server_tool_use",
            "id": "s1",
            "name": "code_execution",
            "input": {"code": "1"},
        },
        {
            "type": "bash_code_execution_tool_result",
            "tool_use_id": "s1",
            "content": {"stdout": "1", "stderr": "", "return_code": 0},
        },
    ]

    c = provider().agent_completion(blocks, "end_turn", AgentUsage(), Decimal(0))

    assert c.message.content[1] == ToolResult(
        tool_call_id="s1",
        name="code_execution",
        text='{"return_code": 0, "stderr": "", "stdout": "1"}',
    )


def test_off_thinking_is_not_shared_with_the_request():
    kwargs = request(provider("claude-sonnet-5-5"), reasoning="off")
    kwargs["thinking"]["type"] = "mutated"

    assert request(provider("claude-sonnet-5-5"), reasoning="off")["thinking"] == {
        "type": "between_tools"
    }
