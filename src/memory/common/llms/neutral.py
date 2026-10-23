"""Provider-neutral agent message format and model capability descriptions.

Everything an agent run stores or branches on is expressed here; providers
translate to and from their own APIs.
"""

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, ValidationError

ReasoningLevel = Literal["off", "low", "medium", "high"]
# "paused": the provider stopped a long turn early; resend the conversation,
# partial turn included, to resume it.
StopReason = Literal[
    "end_turn", "tool_calls", "max_output_tokens", "context_exceeded", "refused", "paused"
]
# {"provider": str, "blocks": [...]}: opaque, same-provider resend only
Replay = dict[str, Any]
BUILTIN_TOOLS = frozenset({"web_search", "web_fetch"})
MTOK = Decimal(1_000_000)


class Text(BaseModel):
    type: Literal["text"] = "text"
    text: str


class Reasoning(BaseModel):
    type: Literal["reasoning"] = "reasoning"
    text: str


class ToolCall(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    input: dict[str, Any]
    executed_by: Literal["runner", "provider"] = "runner"


class ToolResult(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str
    name: str
    text: str
    is_error: bool = False
    duration_ms: int | None = None


Block = Annotated[Text | Reasoning | ToolCall | ToolResult, Field(discriminator="type")]


class NeutralMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: list[Block]

    def tool_calls(self) -> list[ToolCall]:
        """Calls the runner must execute; provider-executed calls arrive resolved."""
        return [
            b
            for b in self.content
            if isinstance(b, ToolCall) and b.executed_by == "runner"
        ]

    def text(self) -> str:
        return "".join(b.text for b in self.content if isinstance(b, Text))


@dataclass(frozen=True)
class Capabilities:
    tool_choice_required: bool
    strict_tools: bool
    reasoning_levels: frozenset[ReasoningLevel]
    sampling_params: bool
    native_web_search: bool
    native_web_fetch: bool
    max_output_tokens: int

    def native_builtins(self) -> frozenset[str]:
        return frozenset(
            name
            for name, native in (
                ("web_search", self.native_web_search),
                ("web_fetch", self.native_web_fetch),
            )
            if native
        )


@dataclass
class AgentUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    web_searches: int = 0
    web_fetches: int = 0

    def __add__(self, other: "AgentUsage") -> "AgentUsage":
        return AgentUsage(**{k: v + getattr(other, k) for k, v in asdict(self).items()})

    @property
    def context_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens

    def to_dict(self) -> dict[str, int]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, int] | None) -> "AgentUsage":
        return cls(**(data or {}))


@dataclass(frozen=True)
class Pricing:
    """USD per million tokens, plus per-1k fees for provider-executed tools.

    Cache reads and writes are priced as multiples of the input rate in force,
    so long-context pricing raises them too.
    """

    input: Decimal
    output: Decimal
    cache_read_multiplier: Decimal = Decimal("0.1")
    cache_write_multiplier: Decimal = Decimal("1.25")
    web_search_per_1k: Decimal = Decimal(0)
    web_fetch_per_1k: Decimal = Decimal(0)
    long_context_threshold: int | None = None
    long_input: Decimal | None = None
    long_output: Decimal | None = None

    def cost(self, usage: AgentUsage) -> Decimal:
        long = (
            self.long_context_threshold is not None
            and usage.context_tokens > self.long_context_threshold
        )
        input_rate = (
            self.long_input if long and self.long_input is not None else self.input
        )
        output_rate = (
            self.long_output if long and self.long_output is not None else self.output
        )
        return (
            Decimal(usage.input_tokens) * input_rate / MTOK
            + Decimal(usage.output_tokens) * output_rate / MTOK
            + Decimal(usage.cache_read_tokens)
            * input_rate
            * self.cache_read_multiplier
            / MTOK
            + Decimal(usage.cache_write_tokens)
            * input_rate
            * self.cache_write_multiplier
            / MTOK
            + Decimal(usage.web_searches) * self.web_search_per_1k / 1000
            + Decimal(usage.web_fetches) * self.web_fetch_per_1k / 1000
        )


@dataclass(frozen=True)
class ModelInfo:
    capabilities: Capabilities
    pricing: Pricing


@dataclass(frozen=True)
class GenerationParams:
    max_output_tokens: int
    reasoning: ReasoningLevel
    temperature: float | None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class Completion:
    message: NeutralMessage
    replay: Replay | None
    stop_reason: StopReason
    usage: AgentUsage
    cost: Decimal


def validate_generation(
    *,
    provider: str,
    model: str,
    info: ModelInfo,
    params: GenerationParams,
    extras_model: type[BaseModel],
    builtins: set[str],
    has_search_backend: bool,
) -> list[str]:
    """Every reason `params` can't run on this model, each naming the capability."""
    caps = info.capabilities
    name = f"{provider}/{model}"
    errors = []
    if params.reasoning not in caps.reasoning_levels:
        levels = ", ".join(sorted(caps.reasoning_levels))
        errors.append(
            f"reasoning '{params.reasoning}' is not supported by {name} (supports: {levels})"
        )
    if params.temperature is not None and not caps.sampling_params:
        errors.append(
            f"temperature: {name} does not accept sampling params (sampling_params=false)"
        )
    if params.max_output_tokens > caps.max_output_tokens:
        errors.append(
            f"max_output_tokens {params.max_output_tokens} > model limit {caps.max_output_tokens}"
        )
    try:
        extras_model.model_validate(params.extras)
    except ValidationError as e:
        errors += [
            f"provider_extras key '{err['loc'][0]}' is not supported by {provider}; remove it"
            if err["type"] == "extra_forbidden"
            else f"provider_extras.{'.'.join(map(str, err['loc']))}: {err['msg']}"
            for err in e.errors()
        ]
    if (
        "web_search" in builtins
        and not caps.native_web_search
        and not has_search_backend
    ):
        errors.append(
            f"web_search: {name} has no native web search and no local search backend is configured"
        )
    return errors
