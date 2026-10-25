"""What an agent is: the validated, provider-neutral spec shared by definitions,
ad-hoc runs and frozen run snapshots."""

import json
import re
from collections import Counter
from decimal import Decimal
from typing import Annotated, Any, Literal

import jsonschema
import referencing
import referencing.exceptions
import referencing.jsonschema
from pydantic import BaseModel, ConfigDict, Field, model_validator

from memory.agents.builtins import search_backend
from memory.agents.call import SUBMIT_RESULT
from memory.agents.caps import per_run_cap
from memory.common import settings
from memory.common.llms.neutral import (
    BUILTIN_TOOLS,
    MTOK,
    GenerationParams,
    ModelInfo,
    ReasoningLevel,
    validate_generation,
)
from memory.common.llms.registry import provider_class, split_model

DEFAULT_AGENT_MODEL = "anthropic/claude-sonnet-5-5"
DEFAULT_MAX_OUTPUT_TOKENS = 16_000
MAX_TEXT_CHARS = 200_000
MAX_NAME_CHARS = 100
# agent_* and the scheduler writes: a run must not start or manage runs or
# sessions, which would escape its caps (scheduled sessions can also receive the
# owner's stored secrets).
DENIED_TOOL_PREFIXES = ("agent_",)
DENIED_TOOLS = frozenset(
    {
        "scheduler_upsert",
        "scheduler_delete",
        "scheduler_cancel",
    }
)
# 64: the strictest provider limit on tool names (OpenAI function names).
TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
MAX_RESULT_SCHEMA_BYTES = 64 * 1024
Money = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=6)]


class FinishSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["answer", "tool"] = "answer"
    tools: list[str] = []

    @model_validator(mode="after")
    def tools_match_mode(self) -> "FinishSpec":
        if self.mode == "tool" and not self.tools:
            raise ValueError("finish mode 'tool' needs at least one finish tool")
        if self.mode == "answer" and self.tools:
            raise ValueError("finish mode 'answer' takes no finish tools")
        return self


class ExternalMCP(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server_id: int
    tools: list[str] = Field(min_length=1)


class AgentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_prompt: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    model: str = DEFAULT_AGENT_MODEL
    # None: the most the run can afford, up to DEFAULT_MAX_OUTPUT_TOKENS (set at enqueue).
    max_output_tokens: int | None = Field(default=None, ge=1)
    reasoning: ReasoningLevel = "medium"
    temperature: float | None = Field(default=None, ge=0, le=2)
    provider_extras: dict[str, Any] = {}
    tools: list[str] = []
    mcp_servers: list[ExternalMCP] = []
    finish: FinishSpec = FinishSpec()
    result_schema: dict[str, Any] | None = None
    max_turns: int = Field(default=20, ge=1, le=200)
    max_cost_usd: Money | None = None
    max_cost_usd_per_day: Money | None = None
    # Runs this definition may start per rolling hour, whatever starts them.
    max_runs_per_hour: int | None = Field(default=None, ge=1)
    timeout_sec: int = Field(default=600, ge=1)
    # A key id of yours, or None for your default key for the model's provider.
    provider_key: Annotated[int, Field(ge=1)] | None = None


def is_denied(tool: str) -> bool:
    """Tools a run may never call, whichever server exposes them."""
    return tool.startswith(DENIED_TOOL_PREFIXES) or tool in DENIED_TOOLS


def external_tool_name(server_id: int, tool: str) -> str:
    """How an external MCP tool is exposed to the model (unique across servers)."""
    return f"mcp_{server_id}_{tool}"


def exposed_tools(spec: AgentSpec) -> list[str]:
    return spec.tools + [
        external_tool_name(s.server_id, t) for s in spec.mcp_servers for t in s.tools
    ]


def finish_tool_names(spec: AgentSpec) -> set[str]:
    return set(spec.finish.tools) | ({SUBMIT_RESULT} if spec.result_schema else set())


def model_info(model: str) -> ModelInfo:
    provider, name = split_model(model)
    return provider_class(model).model_info(name)


def output_rate(info: ModelInfo) -> Decimal:
    """USD per MTok of output at the dearest rate card."""
    return max(info.pricing.output, info.pricing.long_output or info.pricing.output)


def resolve_max_output_tokens(
    spec: AgentSpec, info: ModelInfo, run_cap: Decimal
) -> int:
    if spec.max_output_tokens is not None:
        return spec.max_output_tokens
    affordable = int(run_cap * MTOK / output_rate(info))
    return min(
        info.capabilities.max_output_tokens, DEFAULT_MAX_OUTPUT_TOKENS, affordable
    )


def generation_params(spec: AgentSpec) -> GenerationParams:
    """For a run's snapshot, whose max_output_tokens enqueue has resolved."""
    if spec.max_output_tokens is None:
        raise ValueError("max_output_tokens must be resolved before a run starts")
    return GenerationParams(
        max_output_tokens=spec.max_output_tokens,
        reasoning=spec.reasoning,
        temperature=spec.temperature,
        extras=spec.provider_extras,
    )


def result_errors(schema: dict[str, Any], data: Any) -> list[str]:
    """Why `data` doesn't match a result_schema; the only way to validate one.

    The empty registry means a $ref can never be fetched over the network.
    """
    validator = jsonschema.Draft202012Validator(schema, registry=referencing.Registry())
    try:
        return [
            f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
            for e in validator.iter_errors(data)
        ]
    except referencing.exceptions.Unresolvable as e:
        return [f"result_schema reference cannot be resolved: {e}"]


def tool_errors(spec: AgentSpec) -> list[str]:
    exposed = exposed_tools(spec)
    errors = [
        f"tool '{t}' can never be used inside a run" for t in spec.tools if is_denied(t)
    ]
    errors += [
        f"tool '{t}' on MCP server {s.server_id} can never be used inside a run"
        for s in spec.mcp_servers
        for t in s.tools
        if is_denied(t)
    ]
    errors += [
        f"tool '{t}' is listed twice" for t, n in Counter(exposed).items() if n > 1
    ]
    errors += [
        f"tool name '{t}' is invalid (letters, digits, _ and - only, at most 128)"
        for t in exposed
        if not TOOL_NAME.match(t)
    ]
    if SUBMIT_RESULT in exposed:
        errors.append(f"'{SUBMIT_RESULT}' is reserved for result_schema runs")
    for t in spec.finish.tools:
        if t in BUILTIN_TOOLS:
            errors.append(f"builtin '{t}' cannot be a finish tool")
        elif t not in exposed:
            errors.append(
                f"finish tool '{t}' is not in tools "
                "(external tools are named mcp_<server_id>_<tool>)"
            )
    return errors


def reference_errors(node: Any) -> list[str]:
    """Non-local references would make validation fetch URLs; $id rebases local ones."""
    if isinstance(node, list):
        return [e for item in node for e in reference_errors(item)]
    if not isinstance(node, dict):
        return []
    errors = ["result_schema may not set $id"] if "$id" in node else []
    errors += [
        f"result_schema {key} {node[key]!r} must be local (start with '#')"
        for key in ("$ref", "$dynamicRef")
        if key in node and not str(node[key]).startswith("#")
    ]
    return errors + [e for value in node.values() for e in reference_errors(value)]


def local_refs(node: Any) -> list[str]:
    if isinstance(node, list):
        return [r for item in node for r in local_refs(item)]
    if not isinstance(node, dict):
        return []
    own = [str(node[k]) for k in ("$ref", "$dynamicRef") if k in node]
    return own + [r for value in node.values() for r in local_refs(value)]


def unresolvable_ref_errors(schema: dict[str, Any]) -> list[str]:
    """Resolve every ref now, offline, so a broken one fails at save, not mid-run."""
    resource = referencing.Resource.from_contents(
        schema, default_specification=referencing.jsonschema.DRAFT202012
    )
    resolver = referencing.Registry().with_resource("", resource).resolver(base_uri="")
    errors = []
    for ref in local_refs(schema):
        try:
            resolver.lookup(ref)
        except referencing.exceptions.Unresolvable:
            errors.append(f"result_schema $ref {ref!r} cannot be resolved")
    return errors


def checked_schema_errors(schema: dict[str, Any]) -> list[str]:
    if len(json.dumps(schema)) > MAX_RESULT_SCHEMA_BYTES:
        return ["result_schema is larger than 64 KB"]
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as e:
        return [f"result_schema is not a valid JSON schema: {e.message}"]
    if schema.get("type") != "object":
        return ["result_schema must describe an object (type: object)"]
    return reference_errors(schema) or unresolvable_ref_errors(schema)


def schema_errors(schema: dict[str, Any] | None) -> list[str]:
    if schema is None:
        return []
    try:
        return checked_schema_errors(schema)
    except RecursionError:
        return ["result_schema is nested too deeply"]


def budget_errors(spec: AgentSpec) -> list[str]:
    errors = []
    if (spec.max_runs_per_hour or 0) > settings.AGENT_MAX_RUNS_PER_HOUR:
        errors.append(
            f"max_runs_per_hour {spec.max_runs_per_hour} exceeds AGENT_MAX_RUNS_PER_HOUR "
            f"({settings.AGENT_MAX_RUNS_PER_HOUR})"
        )
    if spec.timeout_sec > settings.AGENT_MAX_TIMEOUT_SEC:
        errors.append(
            f"timeout_sec {spec.timeout_sec} exceeds AGENT_MAX_TIMEOUT_SEC "
            f"({settings.AGENT_MAX_TIMEOUT_SEC})"
        )
    return errors


def turn_cost_errors(spec: AgentSpec, info: ModelInfo) -> list[str]:
    """One turn's output alone must fit the run's cap, or the cap can't hold."""
    if spec.max_output_tokens is None:
        return []
    worst = Decimal(spec.max_output_tokens) * output_rate(info) / MTOK
    cap = per_run_cap(spec.max_cost_usd)
    if worst <= cap:
        return []
    return [
        f"max_output_tokens {spec.max_output_tokens} can cost ${worst} in one turn "
        f"on {spec.model}, above the per-run cap of ${cap}; lower max_output_tokens"
    ]


def model_errors(spec: AgentSpec) -> list[str]:
    try:
        provider, model = split_model(spec.model)
        cls = provider_class(spec.model)
        info = cls.model_info(model)
    except ValueError as e:
        return [str(e)]
    return turn_cost_errors(spec, info) + validate_generation(
        provider=provider,
        model=model,
        info=info,
        params=generation_params(
            spec.model_copy(
                update={
                    "max_output_tokens": spec.max_output_tokens
                    or min(
                        info.capabilities.max_output_tokens, DEFAULT_MAX_OUTPUT_TOKENS
                    )
                }
            )
        ),
        extras_model=cls.Extras,
        builtins=set(spec.tools) & BUILTIN_TOOLS,
        has_search_backend=search_backend() is not None,
    )


def spec_errors(spec: AgentSpec) -> list[str]:
    """Everything wrong with `spec` that can be known without the DB or MCP.

    Tool visibility to the owner and its provider key are checked separately
    (they need the owner).
    """
    return (
        tool_errors(spec)
        + schema_errors(spec.result_schema)
        + budget_errors(spec)
        + model_errors(spec)
    )
