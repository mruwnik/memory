"""Anthropic LLM provider implementation."""

import json
import logging
from decimal import Decimal
from typing import Any, Literal

import anthropic

from memory.common import settings
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
    ReasoningLevel,
    Replay,
    StopReason,
    Text,
    ToolCall,
    ToolResult,
)
from memory.common.llms.tools import ToolDefinition, is_strict_schema

logger = logging.getLogger(__name__)

NATIVE_TOOL_TYPES: dict[str, dict[str, str]] = {
    "claude-opus-5-5": {
        "web_search": "web_search_20260209",
        "web_fetch": "web_fetch_20260209",
    },
    "claude-sonnet-5-5": {
        "web_search": "web_search_20260209",
        "web_fetch": "web_fetch_20260209",
    },
    # Fable 5.1's documented tool surface includes web fetch but not web search.
    "claude-fable-5-1": {"web_fetch": "web_fetch_20260318"},
}
# Models that can turn thinking off, and how; the rest always think.
OFF_THINKING: dict[str, dict[str, str]] = {
    "claude-sonnet-5-5": {"type": "between_tools"},
    "claude-haiku-5-5": {"type": "disabled"},
}
STOP_REASONS: dict[str, StopReason] = {
    "end_turn": "end_turn",
    "stop_sequence": "end_turn",
    "tool_use": "tool_calls",
    "max_tokens": "max_output_tokens",
    "model_context_window_exceeded": "context_exceeded",
    "refusal": "refused",
    "pause_turn": "paused",
}
WEB_SEARCH_PER_1K = Decimal("10")


def anthropic_model(
    name: str, *, tool_choice_required: bool, pricing: Pricing
) -> ModelInfo:
    native = NATIVE_TOOL_TYPES.get(name, {})
    levels: set[ReasoningLevel] = {"low", "medium", "high"}
    if name in OFF_THINKING:
        levels.add("off")
    return ModelInfo(
        capabilities=Capabilities(
            tool_choice_required=tool_choice_required,
            strict_tools=True,
            reasoning_levels=frozenset(levels),
            sampling_params=False,
            native_web_search="web_search" in native,
            native_web_fetch="web_fetch" in native,
            max_output_tokens=128_000,
        ),
        pricing=pricing,
    )


ANTHROPIC_MODELS: dict[str, ModelInfo] = {
    "claude-opus-5-5": anthropic_model(
        "claude-opus-5-5",
        tool_choice_required=False,
        pricing=Pricing(
            input=Decimal("4"),
            output=Decimal("20"),
            cache_read_multiplier=Decimal("0.05"),
            web_search_per_1k=WEB_SEARCH_PER_1K,
        ),
    ),
    "claude-sonnet-5-5": anthropic_model(
        "claude-sonnet-5-5",
        tool_choice_required=False,
        pricing=Pricing(
            input=Decimal("2"),
            output=Decimal("10"),
            cache_read_multiplier=Decimal("0.05"),
            web_search_per_1k=WEB_SEARCH_PER_1K,
        ),
    ),
    "claude-haiku-5-5": anthropic_model(
        "claude-haiku-5-5",
        tool_choice_required=True,
        pricing=Pricing(
            input=Decimal("0.10"),
            output=Decimal("0.50"),
            long_context_threshold=100_000,
            long_input=Decimal("0.50"),
            long_output=Decimal("2.50"),
        ),
    ),
    "claude-fable-5-1": anthropic_model(
        "claude-fable-5-1",
        tool_choice_required=False,
        pricing=Pricing(
            input=Decimal("10"),
            output=Decimal("50"),
            cache_read_multiplier=Decimal("0.025"),
        ),
    ),
}


def render_server_result(block: dict[str, Any]) -> tuple[str, bool]:
    content = block.get("content")
    if isinstance(content, dict) and "error_code" in content:
        return f"error: {content['error_code']}", True
    match block["type"]:
        case "web_search_tool_result":
            lines = [
                f"- {r.get('title', '')} <{r.get('url', '')}>" for r in content or []
            ]
            return "\n".join(lines), False
        case "web_fetch_tool_result":
            return f"fetched <{(content or {}).get('url', '')}>", False
    return json.dumps(content, sort_keys=True), False


class AnthropicProvider(BaseLLMProvider):
    """Anthropic LLM provider."""

    provider = "anthropic"
    MODELS = ANTHROPIC_MODELS
    KEY_REJECTED = (anthropic.AuthenticationError,)
    KEY_FORBIDDEN = (anthropic.PermissionDeniedError,)
    MODEL_UNAVAILABLE = (anthropic.NotFoundError,)

    def agent_client(self) -> anthropic.AsyncAnthropic:
        # Agent turns can think for minutes; the 60s default for short calls is too low.
        return anthropic.AsyncAnthropic(
            api_key=self.api_key, timeout=settings.AGENT_LLM_TIMEOUT_SEC
        )

    def agent_block(
        self, block: Text | ToolCall | ToolResult, role: str
    ) -> dict[str, Any]:
        match block:
            case Text():
                return {"type": "text", "text": block.text}
            case ToolCall(executed_by="runner"):
                return {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                }
            case ToolCall():
                args = json.dumps(block.input, sort_keys=True)
                return {"type": "text", "text": f"[{block.name} {args}]"}
            case ToolResult() if role == "user":
                return {
                    "type": "tool_result",
                    "tool_use_id": block.tool_call_id,
                    "content": block.text,
                    "is_error": block.is_error,
                }
            case ToolResult():
                return {"type": "text", "text": f"[{block.name} result]\n{block.text}"}

    def agent_message(
        self, message: NeutralMessage, replay: Replay | None
    ) -> dict[str, Any]:
        if replay is not None and replay.get("provider") == self.provider:
            return {"role": message.role, "content": replay["blocks"]}
        # Reasoning without its signature can't be replayed, so it is dropped.
        content = [
            self.agent_block(b, message.role)
            for b in message.content
            if not isinstance(b, Reasoning)
        ]
        return {
            "role": message.role,
            "content": content or [{"type": "text", "text": "(reasoning omitted)"}],
        }

    def agent_tool(
        self, tool: ToolDefinition, native: dict[str, str]
    ) -> dict[str, Any]:
        if tool.name in native:
            return {"type": native[tool.name], "name": tool.name}
        spec: dict[str, Any] = {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
        }
        if tool.strict and is_strict_schema(tool.input_schema):
            spec["strict"] = True
        return spec

    def build_agent_request(
        self,
        *,
        system: str,
        messages: list[NeutralMessage],
        replay: list[Replay | None],
        tools: list[ToolDefinition],
        tool_choice: Literal["auto", "required"],
        params: GenerationParams,
    ) -> dict[str, Any]:
        native = NATIVE_TOOL_TYPES.get(self.model, {})
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": params.max_output_tokens,
            "system": system,
            "messages": [self.agent_message(m, r) for m, r in zip(messages, replay)],
            "cache_control": {"type": "ephemeral"},
        }
        if tools:
            kwargs["tools"] = [self.agent_tool(t, native) for t in tools]
        if tool_choice == "required":
            kwargs["tool_choice"] = {"type": "any"}
        if params.reasoning == "off":
            kwargs["thinking"] = dict(OFF_THINKING[self.model])
            kwargs["output_config"] = {"effort": "low"}
        else:
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
            kwargs["output_config"] = {"effort": params.reasoning}
        if params.temperature is not None:
            kwargs["temperature"] = params.temperature
        return kwargs

    @staticmethod
    def agent_usage(msg: anthropic.types.Message) -> AgentUsage:
        u = msg.usage
        server = u.server_tool_use
        return AgentUsage(
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_tokens=u.cache_read_input_tokens or 0,
            cache_write_tokens=u.cache_creation_input_tokens or 0,
            web_searches=(server.web_search_requests or 0) if server else 0,
            web_fetches=(server.web_fetch_requests or 0) if server else 0,
        )

    @staticmethod
    def neutral_block(
        block: dict[str, Any], names: dict[str, str]
    ) -> Text | Reasoning | ToolCall | ToolResult | None:
        match block["type"]:
            case "text":
                return Text(text=block["text"])
            case "thinking":
                return (
                    Reasoning(text=block["thinking"]) if block.get("thinking") else None
                )
            case "tool_use":
                return ToolCall(
                    id=block["id"], name=block["name"], input=block.get("input") or {}
                )
            case "server_tool_use":
                return ToolCall(
                    id=block["id"],
                    name=block["name"],
                    input=block.get("input") or {},
                    executed_by="provider",
                )
            # Results of provider calls, including those run via code execution.
            case kind if (
                kind.endswith("_tool_result") and block.get("tool_use_id") in names
            ):
                text, is_error = render_server_result(block)
                return ToolResult(
                    tool_call_id=block["tool_use_id"],
                    name=names[block["tool_use_id"]],
                    text=text,
                    is_error=is_error,
                )
        return None  # e.g. redacted_thinking: replay-only

    def agent_completion(
        self,
        blocks: list[dict[str, Any]],
        stop_reason: str,
        usage: AgentUsage,
        cost: Decimal,
    ) -> Completion:
        if stop_reason not in STOP_REASONS:
            raise ValueError(f"unexpected anthropic stop_reason {stop_reason!r}")
        names = {b["id"]: b["name"] for b in blocks if b["type"] == "server_tool_use"}
        content = [
            c for c in (self.neutral_block(b, names) for b in blocks) if c is not None
        ]
        return Completion(
            message=NeutralMessage(role="assistant", content=content),
            replay={"provider": self.provider, "blocks": blocks},
            stop_reason=STOP_REASONS[stop_reason],
            usage=usage,
            cost=cost,
        )

    async def acomplete(
        self,
        *,
        system: str,
        messages: list[NeutralMessage],
        replay: list[Replay | None],
        tools: list[ToolDefinition],
        tool_choice: Literal["auto", "required"],
        params: GenerationParams,
    ) -> Completion:
        pricing = self.model_info(self.model).pricing
        kwargs = self.build_agent_request(
            system=system,
            messages=messages,
            replay=replay,
            tools=tools,
            tool_choice=tool_choice,
            params=params,
        )
        async with self.agent_client() as client:
            async with client.messages.stream(**kwargs) as stream:
                msg = await stream.get_final_message()
        usage = self.agent_usage(msg)
        blocks = [b.to_dict() for b in msg.content]
        return self.agent_completion(
            blocks, msg.stop_reason or "", usage, pricing.cost(usage)
        )
