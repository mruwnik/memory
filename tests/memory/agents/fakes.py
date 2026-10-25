"""Two fake providers with deliberately different capabilities.

The runner suite runs against both to prove nothing provider-specific leaks
into the runner. Each fake asserts that requests respect its own capabilities,
as a real provider's API would reject them.
"""

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from decimal import Decimal

from memory.agents.builtins import BUILTIN_DEFS
from memory.common.llms.anthropic_provider import ANTHROPIC_MODELS
from memory.common.llms.openai_provider import OPENAI_MODELS
from memory.common.llms.base import BaseLLMProvider
from memory.common.llms.neutral import (
    AgentUsage,
    Capabilities,
    Completion,
    GenerationParams,
    ModelInfo,
    NeutralMessage,
    Pricing,
    Reasoning,
    Replay,
    Text,
    ToolCall,
    ToolResult,
)
from memory.common.llms.tools import ToolDefinition

PRICING = Pricing(input=Decimal("1"), output=Decimal("2"))
RICH = ModelInfo(
    Capabilities(
        tool_choice_required=True,
        strict_tools=True,
        reasoning_levels=frozenset({"off", "low", "medium", "high"}),
        sampling_params=True,
        native_web_search=True,
        native_web_fetch=True,
        max_output_tokens=64_000,
    ),
    PRICING,
)
POOR = ModelInfo(
    Capabilities(
        tool_choice_required=False,
        strict_tools=False,
        reasoning_levels=frozenset({"off", "high"}),
        sampling_params=False,
        native_web_search=False,
        native_web_fetch=False,
        max_output_tokens=8_000,
    ),
    PRICING,
)

ScriptItem = Completion | Callable[[], Awaitable[Completion]]


class FakeKeyRejected(Exception):
    """What a fake provider raises when its API key is refused."""


class FakeKeyForbidden(Exception):
    """What a fake provider raises when its API key may not make a request."""


async def key_rejected() -> Completion:
    raise FakeKeyRejected("401 invalid x-api-key")


class FakeModelUnavailable(Exception):
    """What a fake provider raises when it has no such model."""


async def model_unavailable() -> Completion:
    raise FakeModelUnavailable("404 model: claude-haiku-5-5")


async def key_forbidden() -> Completion:
    raise FakeKeyForbidden("403 model not allowed for this key")


class ScriptedProvider(BaseLLMProvider):
    """Returns queued completions in order and records every request."""

    KEY_REJECTED = (FakeKeyRejected,)
    KEY_FORBIDDEN = (FakeKeyForbidden,)
    MODEL_UNAVAILABLE = (FakeModelUnavailable,)
    script: list[ScriptItem] = []
    requests: list[dict] = []
    keys: dict = {}

    def check_request(
        self, tool_choice: str, params: GenerationParams, replay: list[Replay | None]
    ):
        caps = self.model_info(self.model).capabilities
        assert tool_choice == "auto" or caps.tool_choice_required
        assert params.temperature is None or caps.sampling_params
        assert params.reasoning in caps.reasoning_levels
        assert params.max_output_tokens <= caps.max_output_tokens
        assert all(r["provider"] == self.provider for r in replay if r is not None)

    def check_completion(self, completion: Completion):
        if completion.stop_reason == "paused":
            return
        content = completion.message.content
        provider_calls = {
            b.id
            for b in content
            if isinstance(b, ToolCall) and b.executed_by == "provider"
        }
        answered = {b.tool_call_id for b in content if isinstance(b, ToolResult)}
        assert provider_calls <= answered

    async def acomplete(self, **kwargs) -> Completion:
        self.check_request(kwargs["tool_choice"], kwargs["params"], kwargs["replay"])
        # Snapshot: the runner keeps appending to the lists it passed in.
        ScriptedProvider.requests.append(
            kwargs
            | {
                "messages": list(kwargs["messages"]),
                "replay": list(kwargs["replay"]),
                "api_key": self.api_key,
                "model": f"{self.provider}/{self.model}",
            }
        )
        item = ScriptedProvider.script.pop(0)
        completion = item if isinstance(item, Completion) else await item()
        self.check_completion(completion)
        if completion.replay is None:
            return completion
        return replace(
            completion, replay=completion.replay | {"provider": self.provider}
        )


class FakeRich(ScriptedProvider):
    provider = "anthropic"
    MODELS = {"m": RICH}


class FakePoor(ScriptedProvider):
    provider = "openai"
    MODELS = {"m": POOR}


class FakeAnthropic(ScriptedProvider):
    """Real anthropic model info, scripted answers: for code that calls system agents."""

    provider = "anthropic"
    MODELS = ANTHROPIC_MODELS


class FakeOpenAI(ScriptedProvider):
    """Real openai model info, scripted answers: for code that calls system agents."""

    provider = "openai"
    MODELS = OPENAI_MODELS


FAKE_MODELS = ["anthropic/m", "openai/m"]
DEFAULT_USAGE = AgentUsage(input_tokens=100, output_tokens=10)


def reply(text: str, usage: AgentUsage | None = None) -> Completion:
    return Completion(
        message=NeutralMessage(role="assistant", content=[Text(text=text)]),
        replay={"blocks": [{"text": text}]},
        stop_reason="end_turn",
        usage=usage or DEFAULT_USAGE,
        cost=PRICING.cost(usage or DEFAULT_USAGE),
    )


def calls(
    *tool_calls: tuple[str, str, dict], usage: AgentUsage | None = None
) -> Completion:
    return Completion(
        message=NeutralMessage(
            role="assistant",
            content=[ToolCall(id=i, name=n, input=a) for i, n, a in tool_calls],
        ),
        replay={"blocks": []},
        stop_reason="tool_calls",
        usage=usage or DEFAULT_USAGE,
        cost=PRICING.cost(usage or DEFAULT_USAGE),
    )


def submitted(data: dict, usage: AgentUsage | None = None) -> Completion:
    """A turn that answers a result schema."""
    return Completion(
        message=NeutralMessage(
            role="assistant",
            content=[ToolCall(id="r1", name="submit_result", input=data)],
        ),
        replay={"blocks": []},
        stop_reason="tool_calls",
        usage=usage or DEFAULT_USAGE,
        cost=PRICING.cost(usage or DEFAULT_USAGE),
    )


def searched_reply(query: str, results: str, text: str) -> Completion:
    """A turn where the provider ran web_search itself."""
    return Completion(
        message=NeutralMessage(
            role="assistant",
            content=[
                Reasoning(text=f"searching for {query}"),
                ToolCall(
                    id="srv1",
                    name="web_search",
                    input={"query": query},
                    executed_by="provider",
                ),
                ToolResult(tool_call_id="srv1", name="web_search", text=results),
                Text(text=text),
            ],
        ),
        replay={"blocks": [{"signature": "opaque"}]},
        stop_reason="end_turn",
        usage=replace(DEFAULT_USAGE, web_searches=1),
        cost=PRICING.cost(replace(DEFAULT_USAGE, web_searches=1)),
    )


class FakeHub:
    """A ToolHub whose tools are plain functions; records what was called.

    `native` builtins are offered to the model but never called here, as the
    provider runs them.
    """

    def __init__(
        self,
        handlers: dict[str, Callable[[dict], tuple[str, bool]]],
        native: tuple[str, ...] = (),
    ):
        self.handlers = handlers
        self.native = native
        self.calls: list[str] = []
        self.opened_with: dict = {}

    def tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(name=n, description=n, input_schema={"type": "object"})
            for n in sorted(self.handlers)
        ] + [BUILTIN_DEFS[n] for n in self.native]

    def notices(self) -> list[str]:
        return []

    async def call(self, call: ToolCall) -> ToolResult:
        self.calls.append(call.name)
        text, is_error = self.handlers[call.name](call.input)
        return ToolResult(
            tool_call_id=call.id, name=call.name, text=text, is_error=is_error
        )

    def factory(self):
        @asynccontextmanager
        async def open_hub(**kwargs):
            self.opened_with = kwargs
            yield self

        return open_hub
