import threading
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from pydantic import ValidationError

from memory.agents.spec import AgentSpec, result_errors, spec_errors
from memory.common import settings

BASE = {
    "system_prompt": "You are helpful.",
    "model": "anthropic/m",
    "reasoning": "high",
}
TREE = {
    "type": "object",
    "$defs": {
        "node": {
            "type": "object",
            "properties": {
                "kids": {"type": "array", "items": {"$ref": "#/$defs/node"}}
            },
        }
    },
    "properties": {"root": {"$ref": "#/$defs/node"}},
}


@pytest.fixture(autouse=True)
def ceilings(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_MAX_TIMEOUT_SEC", 1800)
    monkeypatch.setattr(settings, "AGENT_MAX_RUNS_PER_HOUR", 60)


@pytest.fixture
def schema_server():
    """A local HTTP server that records every request made to it."""
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            body = b'{"type": "string"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", hits
    server.shutdown()
    server.server_close()


def spec(**overrides) -> AgentSpec:
    return AgentSpec.model_validate(BASE | overrides)


@pytest.mark.parametrize("days", [1, 3650])
def test_retention_days_within_ten_years_is_accepted(days):
    assert AgentSpec.model_validate(BASE | {"retention_days": days}).retention_days == days


@pytest.mark.parametrize("days", [0, 3651])
def test_retention_days_outside_ten_years_is_refused(days):
    with pytest.raises(ValidationError):
        AgentSpec.model_validate(BASE | {"retention_days": days})


def test_defaults_are_valid_on_the_default_model():
    assert spec_errors(AgentSpec(system_prompt="hi")) == []


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"tools": ["agent_run"]}, "tool 'agent_run' can never be used inside a run"),
        *[
            ({"tools": [tool]}, f"tool '{tool}' can never be used inside a run")
            for tool in ("scheduler_upsert", "scheduler_delete", "scheduler_cancel")
        ],
        (
            {"mcp_servers": [{"server_id": 3, "tools": ["agent_run"]}]},
            "tool 'agent_run' on MCP server 3 can never be used inside a run",
        ),
        ({"tools": ["submit_result"]}, "'submit_result' is reserved"),
        (
            {"tools": ["core_search", "core_search"]},
            "tool 'core_search' is listed twice",
        ),
        (
            {
                "mcp_servers": [
                    {"server_id": 3, "tools": ["post"]},
                    {"server_id": 3, "tools": ["post"]},
                ]
            },
            "tool 'mcp_3_post' is listed twice",
        ),
        ({"tools": ["bad name!"]}, "tool name 'bad name!' is invalid"),
        (
            {"mcp_servers": [{"server_id": 3, "tools": ["x" * 130]}]},
            f"tool name 'mcp_3_{'x' * 130}' is invalid",
        ),
        (
            {"finish": {"mode": "tool", "tools": ["core_search"]}},
            "finish tool 'core_search' is not in tools",
        ),
        (
            {
                "tools": ["web_fetch"],
                "finish": {"mode": "tool", "tools": ["web_fetch"]},
            },
            "builtin 'web_fetch' cannot be a finish tool",
        ),
        (
            {"result_schema": {"type": "nonsense"}},
            "result_schema is not a valid JSON schema",
        ),
        (
            {"result_schema": {"type": "string"}},
            "result_schema must describe an object",
        ),
        (
            {
                "result_schema": {
                    "type": "object",
                    "properties": {"a": {"$ref": "https://x/s.json"}},
                }
            },
            "result_schema $ref 'https://x/s.json' must be local (start with '#')",
        ),
        (
            {
                "result_schema": {
                    "type": "object",
                    "$defs": {"a": {"$dynamicRef": "other.json"}},
                }
            },
            "result_schema $dynamicRef 'other.json' must be local (start with '#')",
        ),
        (
            {"result_schema": {"$id": "https://x/", "type": "object"}},
            "result_schema may not set $id",
        ),
        (
            {
                "result_schema": {
                    "type": "object",
                    "properties": {"a": {"$ref": "#/$defs/missing"}},
                }
            },
            "result_schema $ref '#/$defs/missing' cannot be resolved",
        ),
        (
            {
                "result_schema": {
                    "type": "object",
                    "properties": {"a": {"$ref": "#http://evil.example/s.json"}},
                }
            },
            "result_schema $ref '#http://evil.example/s.json' cannot be resolved",
        ),
        (
            {"result_schema": {"type": "object", "description": "x" * 70_000}},
            "result_schema is larger than 64 KB",
        ),
        (
            {"timeout_sec": 99999},
            "timeout_sec 99999 exceeds AGENT_MAX_TIMEOUT_SEC (1800)",
        ),
        (
            {"max_runs_per_hour": 61},
            "max_runs_per_hour 61 exceeds AGENT_MAX_RUNS_PER_HOUR (60)",
        ),
        (
            {"max_output_tokens": 64_000, "max_cost_usd": "0.10"},
            "max_output_tokens 64000 can cost $0.128 in one turn on anthropic/m, "
            "above the per-run cap of $0.10; lower max_output_tokens",
        ),
        ({"model": "nope/x"}, "unknown provider in model 'nope/x'"),
        (
            {"model": "openai/m", "reasoning": "medium"},
            "reasoning 'medium' is not supported by openai/m",
        ),
        (
            {"model": "openai/m", "tools": ["web_search"]},
            "web_search: openai/m has no native web search",
        ),
    ],
)
def test_spec_errors_name_the_problem(fake_providers, overrides, expected):
    errors = spec_errors(spec(**overrides))

    assert any(expected in e for e in errors), errors


def test_independent_problems_are_all_reported(fake_providers):
    errors = spec_errors(spec(tools=["agent_run"], timeout_sec=99999))

    assert len(errors) == 2


def test_deeply_nested_result_schema_is_an_error_not_a_crash(fake_providers):
    schema: dict = {"type": "object"}
    for _ in range(5000):
        schema = {"type": "object", "properties": {"a": schema}}

    errors = spec_errors(spec(result_schema=schema))

    assert any("result_schema" in e for e in errors), errors


def test_switching_provider_with_extras_is_rejected(fake_providers):
    s = spec(
        model="openai/m", max_output_tokens=4000, provider_extras={"effort": "max"}
    )

    assert spec_errors(s) == [
        "provider_extras key 'effort' is not supported by openai; remove it"
    ]


def test_external_tools_can_finish(fake_providers):
    s = spec(
        mcp_servers=[{"server_id": 3, "tools": ["post"]}],
        finish={"mode": "tool", "tools": ["mcp_3_post"]},
    )

    assert spec_errors(s) == []


def test_local_recursive_refs_are_allowed_and_validate(fake_providers):
    assert spec_errors(spec(result_schema=TREE)) == []
    assert result_errors(TREE, {"root": {"kids": [{"kids": []}]}}) == []
    assert result_errors(TREE, {"root": {"kids": [1]}}) != []


def test_remote_refs_are_rejected_at_save_and_never_fetched(
    fake_providers, schema_server
):
    base, hits = schema_server
    remote = {"type": "object", "properties": {"a": {"$ref": f"{base}/s.json"}}}
    rebased = {
        "$id": f"{base}/root.json",
        "type": "object",
        "properties": {"a": {"$ref": "s.json"}},
    }

    save_errors = spec_errors(spec(result_schema=remote)) + spec_errors(
        spec(result_schema=rebased)
    )
    run_errors = result_errors(remote, {"a": "x"}) + result_errors(rebased, {"a": "x"})

    assert len(save_errors) >= 2
    assert all("cannot be resolved" in e for e in run_errors), run_errors
    assert hits == []


@pytest.mark.parametrize(
    "bad",
    [
        {"finish": {"mode": "tool", "tools": []}},
        {"finish": {"mode": "answer", "tools": ["x"]}},
        {"unknown_field": 1},
        {"max_turns": 0},
        {"system_prompt": ""},
        {"system_prompt": "x" * 200_001},
        {"max_cost_usd": "0.1234567"},
        {"max_cost_usd_per_day": "1234567.000001"},
    ],
)
def test_structural_errors_fail_model_validation(bad):
    with pytest.raises(ValidationError):
        AgentSpec.model_validate(BASE | bad)


def test_cost_values_parse_as_decimals():
    assert spec(max_cost_usd="0.50").max_cost_usd == Decimal("0.50")


def test_unset_max_output_tokens_fits_any_model(fake_providers):
    # openai/m allows 8000 output tokens; an explicit 16000 would be rejected.
    assert spec_errors(spec(model="openai/m")) == []
    assert spec_errors(spec(model="openai/m", max_output_tokens=16_000)) == [
        "max_output_tokens 16000 > model limit 8000"
    ]
