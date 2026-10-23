from decimal import Decimal

import pytest

from memory.common.llms.base import NoExtras
from memory.common.llms.neutral import (
    AgentUsage,
    Capabilities,
    GenerationParams,
    ModelInfo,
    NeutralMessage,
    Pricing,
    Text,
    ToolCall,
    ToolResult,
    validate_generation,
)

CAPS = Capabilities(
    tool_choice_required=False,
    strict_tools=True,
    reasoning_levels=frozenset({"low", "medium", "high"}),
    sampling_params=False,
    native_web_search=False,
    native_web_fetch=True,
    max_output_tokens=32_000,
)
INFO = ModelInfo(
    capabilities=CAPS,
    pricing=Pricing(
        input=Decimal("4"), output=Decimal("20"), web_search_per_1k=Decimal("10")
    ),
)


def params(**overrides):
    defaults = {
        "max_output_tokens": 16_000,
        "reasoning": "medium",
        "temperature": None,
        "extras": {},
    }
    return GenerationParams(**(defaults | overrides))


def test_message_round_trips_through_json():
    msg = NeutralMessage(
        role="assistant",
        content=[
            Text(text="hi"),
            ToolCall(
                id="t1", name="web_search", input={"query": "x"}, executed_by="provider"
            ),
            ToolResult(tool_call_id="t1", name="web_search", text="- a <https://a>"),
            ToolCall(id="t2", name="core_search", input={"query": "y"}),
        ],
    )

    assert NeutralMessage.model_validate_json(msg.model_dump_json()) == msg
    assert [c.id for c in msg.tool_calls()] == ["t2"]
    assert msg.text() == "hi"


def test_cost_sums_every_usage_component():
    usage = AgentUsage(
        input_tokens=1_000_000,
        output_tokens=100_000,
        cache_read_tokens=1_000_000,
        cache_write_tokens=200_000,
        web_searches=3,
    )

    # cache reads at 0.1x input, cache writes at 1.25x input
    expected = (
        Decimal("4") + Decimal("2") + Decimal("0.4") + Decimal("1") + Decimal("0.03")
    )
    assert INFO.pricing.cost(usage) == expected


LONG = Pricing(
    input=Decimal("0.10"),
    output=Decimal("0.50"),
    long_context_threshold=100_000,
    long_input=Decimal("0.50"),
    long_output=Decimal("2.50"),
)


@pytest.mark.parametrize(
    "usage,expected",
    [
        (
            AgentUsage(
                input_tokens=10_000,
                output_tokens=1_000_000,
                cache_read_tokens=50_000,
                cache_write_tokens=40_000,
            ),
            Decimal("0.001") + Decimal("0.5") + Decimal("0.0005") + Decimal("0.005"),
        ),
        (
            AgentUsage(
                input_tokens=200_000,
                output_tokens=1_000_000,
                cache_read_tokens=1_000_000,
                cache_write_tokens=1_000_000,
            ),
            Decimal("0.1") + Decimal("2.5") + Decimal("0.05") + Decimal("0.625"),
        ),
    ],
)
def test_long_context_rates_apply_to_every_input_kind_above_threshold(usage, expected):
    assert LONG.cost(usage) == expected


def test_usage_adds_and_round_trips():
    total = AgentUsage(input_tokens=1, output_tokens=2) + AgentUsage(
        cache_read_tokens=3, web_fetches=1
    )

    assert AgentUsage.from_dict(total.to_dict()) == total
    assert total.context_tokens == 4


@pytest.mark.parametrize(
    "overrides,builtins,has_backend,expected",
    [
        ({}, set(), False, []),
        (
            {"reasoning": "off"},
            set(),
            False,
            ["reasoning 'off' is not supported by p/m (supports: high, low, medium)"],
        ),
        (
            {"temperature": 0.2},
            set(),
            False,
            [
                "temperature: p/m does not accept sampling params (sampling_params=false)"
            ],
        ),
        (
            {"max_output_tokens": 64_000},
            set(),
            False,
            ["max_output_tokens 64000 > model limit 32000"],
        ),
        (
            {"extras": {"effort": "max"}},
            set(),
            False,
            ["provider_extras key 'effort' is not supported by p; remove it"],
        ),
        (
            {},
            {"web_search"},
            False,
            [
                "web_search: p/m has no native web search and no local search backend is configured"
            ],
        ),
        ({}, {"web_search"}, True, []),
        ({}, {"web_fetch"}, False, []),
    ],
)
def test_validate_generation_names_the_missing_capability(
    overrides, builtins, has_backend, expected
):
    errors = validate_generation(
        provider="p",
        model="m",
        info=INFO,
        params=params(**overrides),
        extras_model=NoExtras,
        builtins=builtins,
        has_search_backend=has_backend,
    )

    assert errors == expected
