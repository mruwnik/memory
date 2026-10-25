"""OpenAI LLM provider: agent turns on the Responses API.

Requests are stateless (`store=False`): every turn resends the history, with
each earlier turn's output items, encrypted reasoning included, as its replay.
"""

import json
from decimal import Decimal
from typing import Any, Literal

import openai
from openai.types.responses import ResponseUsage

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

INCOMPLETE: dict[str, StopReason] = {
    "max_output_tokens": "max_output_tokens",
    "content_filter": "refused",
}
WEB_SEARCH_PER_1K = Decimal("10")
# The API rejects max_output_tokens below this with a 400.
MIN_OUTPUT_TOKENS = 16
# Past this many input tokens the whole request costs 2x input (so cache too) and 1.5x output.
LONG_CONTEXT = 272_000


def openai_model(*, input: str, output: str, cache_read: str, off: bool) -> ModelInfo:
    levels: set[ReasoningLevel] = {"low", "medium", "high"}
    if off:
        levels.add("off")
    return ModelInfo(
        capabilities=Capabilities(
            tool_choice_required=True,
            strict_tools=True,
            reasoning_levels=frozenset(levels),
            sampling_params=False,
            native_web_search=True,
            native_web_fetch=False,
            max_output_tokens=128_000,
        ),
        pricing=Pricing(
            input=Decimal(input),
            output=Decimal(output),
            cache_read_multiplier=Decimal(cache_read),
            web_search_per_1k=WEB_SEARCH_PER_1K,
            long_context_threshold=LONG_CONTEXT,
            long_input=Decimal(input) * 2,
            long_output=Decimal(output) * Decimal("1.5"),
        ),
    )


OPENAI_MODELS: dict[str, ModelInfo] = {
    "gpt-6-astra": openai_model(input="10", output="50", cache_read="0.1", off=False),
    "gpt-6.1-sol": openai_model(input="2", output="10", cache_read="0.05", off=False),
    # The only one with effort "none", so the only one that can turn reasoning off.
    "gpt-6-luna": openai_model(input="0.10", output="0.50", cache_read="0.1", off=True),
}


def user_text(text: str) -> dict[str, Any]:
    return {"role": "user", "content": [{"type": "input_text", "text": text}]}


def message_text(item: dict[str, Any]) -> tuple[str, bool]:
    """An output message's text, and whether the model refused instead."""
    parts = item.get("content") or []
    text = "".join(p.get("text", "") for p in parts if p["type"] == "output_text")
    return text, any(p["type"] == "refusal" for p in parts)


class OpenAIProvider(BaseLLMProvider):
    provider = "openai"
    MODELS = OPENAI_MODELS
    KEY_REJECTED = (openai.AuthenticationError,)
    KEY_FORBIDDEN = (openai.PermissionDeniedError,)
    MODEL_UNAVAILABLE = (openai.NotFoundError,)

    def agent_client(self) -> openai.AsyncOpenAI:
        return openai.AsyncOpenAI(
            api_key=self.api_key, timeout=settings.AGENT_LLM_TIMEOUT_SEC
        )

    def input_items(
        self, message: NeutralMessage, replay: Replay | None
    ) -> list[dict[str, Any]]:
        if replay is not None and replay.get("provider") == self.provider:
            return replay["blocks"]
        if message.role == "user":
            outputs = [
                {
                    "type": "function_call_output",
                    "call_id": b.tool_call_id,
                    "output": f"error: {b.text}" if b.is_error else b.text,
                }
                for b in message.content
                if isinstance(b, ToolResult)
            ]
            text = message.text()
            return outputs + ([user_text(text)] if text else [])
        # Reasoning can only be resent as the encrypted item kept in replay.
        items: list[dict[str, Any]] = []
        for b in message.content:
            match b:
                case Text():
                    items.append({"role": "assistant", "content": b.text})
                case ToolCall(executed_by="runner"):
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": b.id,
                            "name": b.name,
                            "arguments": json.dumps(b.input),
                        }
                    )
                case ToolCall():
                    args = json.dumps(b.input, sort_keys=True)
                    items.append({"role": "assistant", "content": f"[{b.name} {args}]"})
                case ToolResult():
                    items.append(
                        {"role": "assistant", "content": f"[{b.name} result]\n{b.text}"}
                    )
        return items

    @staticmethod
    def agent_tool(tool: ToolDefinition) -> dict[str, Any]:
        if tool.name == "web_search":
            return {"type": "web_search"}
        return {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
            # Sent either way: the API defaults to strict, which rejects open schemas.
            "strict": tool.strict and is_strict_schema(tool.input_schema),
        }

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
        kwargs: dict[str, Any] = {
            "model": self.model,
            "instructions": system,
            "input": [
                item for m, r in zip(messages, replay) for item in self.input_items(m, r)
            ],
            "max_output_tokens": max(params.max_output_tokens, MIN_OUTPUT_TOKENS),
            "reasoning": {
                "effort": "none" if params.reasoning == "off" else params.reasoning,
                "summary": "auto",
            },
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        if tools:
            kwargs["tools"] = [self.agent_tool(t) for t in tools]
            kwargs["tool_choice"] = tool_choice
        if params.temperature is not None:
            kwargs["temperature"] = params.temperature
        return kwargs

    @staticmethod
    def agent_usage(u: ResponseUsage, items: list[dict[str, Any]]) -> AgentUsage:
        # Both cache counts are breakdowns of input_tokens, which counts all input.
        cached = u.input_tokens_details.cached_tokens
        written = u.input_tokens_details.cache_write_tokens
        return AgentUsage(
            input_tokens=u.input_tokens - cached - written,
            output_tokens=u.output_tokens,
            cache_read_tokens=cached,
            cache_write_tokens=written,
            # Every web_search_call counts, open_page and find_in_page included: over-counting is
            # the safe direction for a cost cap.
            web_searches=sum(i["type"] == "web_search_call" for i in items),
        )

    @staticmethod
    def neutral_blocks(item: dict[str, Any]) -> list[Text | Reasoning | ToolCall | ToolResult]:
        match item["type"]:
            case "message":
                text, _ = message_text(item)
                return [Text(text=text)] if text else []
            case "reasoning":
                summary = "\n\n".join(s["text"] for s in item.get("summary") or [])
                return [Reasoning(text=summary)] if summary else []
            case "function_call":
                return [
                    ToolCall(
                        id=item["call_id"],
                        name=item["name"],
                        input=json.loads(item["arguments"] or "{}"),
                    )
                ]
            case "web_search_call":
                failed = item.get("status") == "failed"
                return [
                    ToolCall(
                        id=item["id"],
                        name="web_search",
                        input=item.get("action") or {},
                        executed_by="provider",
                    ),
                    ToolResult(
                        tool_call_id=item["id"],
                        name="web_search",
                        text="search failed" if failed else "search done",
                        is_error=failed,
                    ),
                ]
        return []

    def agent_completion(
        self,
        items: list[dict[str, Any]],
        status: str,
        incomplete_reason: str | None,
        usage: AgentUsage,
        cost: Decimal,
        error: str | None = None,
    ) -> Completion:
        message = NeutralMessage(
            role="assistant",
            content=[b for item in items for b in self.neutral_blocks(item)],
        )
        refused = any(message_text(i)[1] for i in items if i["type"] == "message")
        if status == "incomplete" and incomplete_reason in INCOMPLETE:
            stop = INCOMPLETE[incomplete_reason]
        elif status != "completed":
            detail = f": {error}" if error else ""
            raise ValueError(f"unexpected openai response status {status!r}{detail}")
        elif refused:
            stop = "refused"
        else:
            stop = "tool_calls" if message.tool_calls() else "end_turn"
        return Completion(
            message=message,
            replay={"provider": self.provider, "blocks": items},
            stop_reason=stop,
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
        try:
            async with self.agent_client() as client:
                response = await client.responses.create(**kwargs)
        except openai.BadRequestError as e:
            if e.code != "context_length_exceeded":
                raise
            return Completion(
                message=NeutralMessage(role="assistant", content=[]),
                replay=None,
                stop_reason="context_exceeded",
                usage=AgentUsage(),
                cost=Decimal(0),
            )
        if response.usage is None:
            raise ValueError("openai returned a response without usage")
        items = [i.to_dict() for i in response.output]
        usage = self.agent_usage(response.usage, items)
        details = response.incomplete_details
        return self.agent_completion(
            items,
            response.status or "",
            details.reason if details else None,
            usage,
            pricing.cost(usage),
            f"{response.error.code}: {response.error.message}" if response.error else None,
        )
