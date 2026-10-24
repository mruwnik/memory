import json
from decimal import Decimal

import httpx
import openai
import pytest
from openai.types.responses import Response

from memory.common.llms.neutral import (
    AgentUsage,
    GenerationParams,
    NeutralMessage,
    Reasoning,
    Text,
    ToolCall,
    ToolResult,
)
from memory.common.llms.openai_provider import OPENAI_MODELS, OpenAIProvider
from memory.common.llms.tools import ToolDefinition

SEARCH = ToolDefinition(
    name="web_search", description="d", input_schema={"type": "object"}
)
CORE = ToolDefinition(
    name="core_search",
    description="Search memory",
    input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
)


def provider(model="gpt-6.1-sol"):
    return OpenAIProvider(api_key="k", model=model)


def params(**overrides):
    defaults = {
        "max_output_tokens": 16_000,
        "reasoning": "medium",
        "temperature": None,
        "extras": {},
    }
    return GenerationParams(**(defaults | overrides))


def user(text="hi"):
    return NeutralMessage(role="user", content=[Text(text=text)])


def request(p, tools=(), tool_choice="auto", **overrides):
    return p.build_agent_request(
        system="sys",
        messages=[user()],
        replay=[None],
        tools=list(tools),
        tool_choice=tool_choice,
        params=params(**overrides),
    )


@pytest.mark.parametrize(
    "model,reasoning,effort",
    [
        ("gpt-6-luna", "off", "none"),
        ("gpt-6-luna", "low", "low"),
        ("gpt-6.1-sol", "high", "high"),
        ("gpt-6-astra", "medium", "medium"),
    ],
)
def test_reasoning_maps_to_effort(model, reasoning, effort):
    assert request(provider(model), reasoning=reasoning)["reasoning"] == {
        "effort": effort,
        "summary": "auto",
    }


@pytest.mark.parametrize("asked,sent", [(1, 16), (16, 16), (4000, 4000)])
def test_output_limit_is_clamped_to_the_api_minimum(asked, sent):
    assert request(provider(), max_output_tokens=asked)["max_output_tokens"] == sent


def test_request_is_stateless_and_carries_encrypted_reasoning():
    kwargs = request(provider())

    assert kwargs["model"] == "gpt-6.1-sol"
    assert kwargs["instructions"] == "sys"
    assert kwargs["max_output_tokens"] == 16_000
    assert kwargs["store"] is False
    assert kwargs["include"] == ["reasoning.encrypted_content"]
    assert kwargs["input"] == [
        {"role": "user", "content": [{"type": "input_text", "text": "hi"}]}
    ]
    assert "temperature" not in kwargs
    assert "tools" not in kwargs
    assert "tool_choice" not in kwargs


@pytest.mark.parametrize("tool_choice", ["auto", "required"])
def test_tool_choice_is_sent_with_tools(tool_choice):
    assert request(provider(), tools=[CORE], tool_choice=tool_choice)[
        "tool_choice"
    ] == tool_choice


def test_runner_tools_are_functions_strict_only_when_schema_is_strict():
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

    tools = request(provider(), tools=[CORE, strict, loose, SEARCH])["tools"]

    assert tools == [
        {
            "type": "function",
            "name": "core_search",
            "description": "Search memory",
            "parameters": CORE.input_schema,
            "strict": False,
        },
        {
            "type": "function",
            "name": "submit_result",
            "description": "d",
            "parameters": strict.input_schema,
            "strict": True,
        },
        {
            "type": "function",
            "name": "loose",
            "description": "d",
            "parameters": {"type": "object"},
            "strict": False,
        },
        {"type": "web_search"},
    ]


def test_replay_is_sent_verbatim_for_assistant_turns():
    items = [
        {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "e"},
        {"type": "function_call", "id": "fc_1", "call_id": "c1", "name": "a", "arguments": "{}"},
    ]

    sent = provider().input_items(
        NeutralMessage(role="assistant", content=[ToolCall(id="c1", name="a", input={})]),
        {"provider": "openai", "blocks": items},
    )

    assert sent == items


def test_another_providers_replay_is_ignored():
    sent = provider().input_items(
        NeutralMessage(role="assistant", content=[Text(text="x")]),
        {"provider": "anthropic", "blocks": [{"type": "thinking"}]},
    )

    assert sent == [{"role": "assistant", "content": "x"}]


def test_neutral_fallback_when_replay_is_missing():
    p = provider()
    assistant = NeutralMessage(
        role="assistant",
        content=[
            Reasoning(text="thinking..."),
            Text(text="let me look"),
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
            ToolResult(tool_call_id="t1", name="core_search", text="found"),
            ToolResult(tool_call_id="t2", name="other", text="boom", is_error=True),
            Text(text="keep going"),
        ],
    )

    assert p.input_items(assistant, None) == [
        {"role": "assistant", "content": "let me look"},
        {"role": "assistant", "content": '[web_search {"query": "q"}]'},
        {"role": "assistant", "content": "[web_search result]\n- a <https://a>"},
        {
            "type": "function_call",
            "call_id": "t1",
            "name": "core_search",
            "arguments": '{"q": "x"}',
        },
    ]
    assert p.input_items(results, None) == [
        {"type": "function_call_output", "call_id": "t1", "output": "found"},
        {"type": "function_call_output", "call_id": "t2", "output": "error: boom"},
        {"role": "user", "content": [{"type": "input_text", "text": "keep going"}]},
    ]


def test_completion_is_neutral_with_replay():
    items = [
        {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "e"},
        {
            "type": "web_search_call",
            "id": "ws_1",
            "status": "completed",
            "action": {"type": "search", "query": "q"},
        },
        {
            "type": "message",
            "id": "m1",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Looking.", "annotations": []}],
        },
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "c1",
            "name": "core_search",
            "arguments": '{"q": "x"}',
        },
    ]

    c = provider().agent_completion(items, "completed", None, AgentUsage(), Decimal(0))

    assert c.stop_reason == "tool_calls"
    assert c.replay == {"provider": "openai", "blocks": items}
    assert c.message.content == [
        ToolCall(
            id="ws_1",
            name="web_search",
            input={"type": "search", "query": "q"},
            executed_by="provider",
        ),
        ToolResult(tool_call_id="ws_1", name="web_search", text="search done"),
        Text(text="Looking."),
        ToolCall(id="c1", name="core_search", input={"q": "x"}),
    ]


def test_a_failed_web_search_is_an_error_result():
    items = [
        {
            "type": "web_search_call",
            "id": "ws_1",
            "status": "failed",
            "action": {"type": "search", "query": "q"},
        }
    ]

    c = provider().agent_completion(items, "completed", None, AgentUsage(), Decimal(0))

    assert c.message.content[1] == ToolResult(
        tool_call_id="ws_1", name="web_search", text="search failed", is_error=True
    )


def text_message(*parts):
    return {"type": "message", "id": "m", "role": "assistant", "content": list(parts)}


@pytest.mark.parametrize(
    "items,status,reason,neutral",
    [
        ([text_message({"type": "output_text", "text": "hi"})], "completed", None, "end_turn"),
        ([], "incomplete", "max_output_tokens", "max_output_tokens"),
        ([], "incomplete", "content_filter", "refused"),
        ([text_message({"type": "refusal", "refusal": "no"})], "completed", None, "refused"),
    ],
)
def test_every_stop_is_normalised(items, status, reason, neutral):
    c = provider().agent_completion(items, status, reason, AgentUsage(), Decimal(0))

    assert c.stop_reason == neutral


def test_a_failed_response_reports_its_reason():
    with pytest.raises(ValueError, match="'failed': server_error: boom"):
        provider().agent_completion(
            [], "failed", None, AgentUsage(), Decimal(0), "server_error: boom"
        )


def test_reasoning_summaries_become_reasoning_blocks():
    item = {
        "type": "reasoning",
        "id": "rs_1",
        "summary": [
            {"type": "summary_text", "text": "first"},
            {"type": "summary_text", "text": "second"},
        ],
    }

    assert provider().neutral_blocks(item) == [Reasoning(text="first\n\nsecond")]


def response(output, status="completed", incomplete_details=None, **usage):
    return Response.model_validate(
        {
            "id": "r",
            "object": "response",
            "created_at": 0,
            "model": "gpt-6.1-sol",
            "output": output,
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
            "status": status,
            "incomplete_details": incomplete_details,
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
                "input_tokens_details": {"cached_tokens": 30, "cache_write_tokens": 50},
                "output_tokens_details": {"reasoning_tokens": 10},
            }
            | usage,
        }
    )


class FakeClient:
    def __init__(self, responses):
        self.queue = list(responses)
        self.sent = []
        self.responses = self

    async def create(self, **kwargs):
        self.sent.append(kwargs)
        return self.queue.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_acomplete_reports_uncached_input_cache_use_and_searches(monkeypatch):
    p = provider()
    client = FakeClient(
        [
            response(
                [
                    {
                        "type": "web_search_call",
                        "id": "ws_1",
                        "status": "completed",
                        "action": {"type": "search", "query": "q"},
                    },
                    {
                        "type": "function_call",
                        "id": "fc_1",
                        "call_id": "c1",
                        "name": "core_search",
                        "arguments": json.dumps({"q": "x"}),
                        "status": "completed",
                    },
                ]
            )
        ]
    )
    monkeypatch.setattr(p, "agent_client", lambda: client)

    c = await p.acomplete(
        system="s",
        messages=[user()],
        replay=[None],
        tools=[CORE],
        tool_choice="auto",
        params=params(),
    )

    usage = AgentUsage(
        input_tokens=20,
        output_tokens=20,
        cache_read_tokens=30,
        cache_write_tokens=50,
        web_searches=1,
    )
    assert c.usage == usage
    assert c.cost == OPENAI_MODELS["gpt-6.1-sol"].pricing.cost(usage)
    assert c.stop_reason == "tool_calls"
    assert c.message.tool_calls() == [ToolCall(id="c1", name="core_search", input={"q": "x"})]
    assert client.sent[0]["store"] is False


@pytest.mark.parametrize(
    "model,usage,expected",
    [
        (
            "gpt-6-astra",
            AgentUsage(input_tokens=100_000, output_tokens=100_000),
            Decimal("6"),
        ),
        (
            "gpt-6.1-sol",
            AgentUsage(cache_read_tokens=100_000, cache_write_tokens=100_000),
            Decimal("0.26"),
        ),
        (
            "gpt-6-luna",
            AgentUsage(input_tokens=100_000, web_searches=1000),
            Decimal("10.01"),
        ),
        (  # past 272k input: 2x input and cache rates, 1.5x output
            "gpt-6-luna",
            AgentUsage(input_tokens=300_000, output_tokens=100_000),
            Decimal("0.135"),
        ),
    ],
)
def test_documented_prices(model, usage, expected):
    assert OPENAI_MODELS[model].pricing.cost(usage) == expected


@pytest.mark.parametrize(
    "input_tokens,cached,written,uncached",
    [
        (1200, 0, 0, 1200),  # cold call
        (1200, 1024, 0, 176),  # cache hit
        (1200, 0, 1024, 176),  # cache write
        (5000, 1024, 3500, 476),  # both in one call
        (1200, 1200, 0, 0),  # entire prompt cached
    ],
)
def test_uncached_input_is_what_is_left_after_cache_reads_and_writes(
    input_tokens, cached, written, uncached
):
    r = response(
        [],
        input_tokens=input_tokens,
        input_tokens_details={"cached_tokens": cached, "cache_write_tokens": written},
    )

    assert r.usage is not None
    usage = OpenAIProvider.agent_usage(r.usage, [])

    assert (usage.input_tokens, usage.cache_read_tokens, usage.cache_write_tokens) == (
        uncached,
        cached,
        written,
    )
    assert usage.context_tokens == input_tokens


@pytest.mark.asyncio
async def test_an_incomplete_response_carries_its_reason(monkeypatch):
    p = provider()
    client = FakeClient(
        [response([], "incomplete", {"reason": "max_output_tokens"})]
    )
    monkeypatch.setattr(p, "agent_client", lambda: client)

    c = await p.acomplete(
        system="s",
        messages=[user()],
        replay=[None],
        tools=[],
        tool_choice="auto",
        params=params(),
    )

    assert c.stop_reason == "max_output_tokens"


@pytest.mark.asyncio
async def test_context_overflow_is_a_stop_not_an_exception(monkeypatch):
    p = provider()

    class Overflowing(FakeClient):
        async def create(self, **kwargs):
            raise openai.BadRequestError(
                "too long",
                response=httpx.Response(400, request=httpx.Request("POST", "http://x")),
                body={"code": "context_length_exceeded", "message": "too long"},
            )

    monkeypatch.setattr(p, "agent_client", lambda: Overflowing([]))

    c = await p.acomplete(
        system="s",
        messages=[user()],
        replay=[None],
        tools=[],
        tool_choice="auto",
        params=params(),
    )

    assert c.stop_reason == "context_exceeded"
