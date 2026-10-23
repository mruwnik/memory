"""Anthropic LLM provider implementation."""

import json
import logging
from decimal import Decimal
from typing import Any, AsyncIterator, Iterator, Literal

import anthropic

from memory.common import settings
from memory.common.llms.base import (
    BaseLLMProvider,
    ImageContent,
    LLMSettings,
    Message,
    MessageRole,
    StreamEvent,
    ToolDefinition,
    Usage,
)
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

logger = logging.getLogger(__name__)

# Models older than the 4.6 generation reject both `output_config.effort` and
# adaptive thinking with a 400; a denylist keeps new models working by default.
LEGACY_MODEL_PREFIXES = (
    "claude-3",
    "claude-opus-4-0",
    "claude-opus-4-1",
    "claude-opus-4-5",
    "claude-opus-4-2025",
    "claude-sonnet-4-0",
    "claude-sonnet-4-5",
    "claude-sonnet-4-2025",
    "claude-haiku-4-5",
)


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


def is_strict_schema(schema: dict[str, Any]) -> bool:
    """Whether Anthropic strict tool use accepts this schema."""
    if schema.get("type") == "object":
        props = schema.get("properties", {})
        return (
            schema.get("additionalProperties") is False
            and set(schema.get("required", [])) == set(props)
            and all(is_strict_schema(p) for p in props.values())
        )
    if schema.get("type") == "array":
        return is_strict_schema(schema.get("items", {}))
    return True


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
    """Anthropic LLM provider with streaming, tool support, and adaptive thinking."""

    provider = "anthropic"
    MODELS = ANTHROPIC_MODELS

    def __init__(self, api_key: str, model: str, enable_thinking: bool = False):
        """
        Initialize the Anthropic provider.

        Args:
            api_key: Anthropic API key
            model: Model identifier
            enable_thinking: Request adaptive thinking with summarized output.
                When off, effort is lowered instead, since current models think
                by default and some cannot disable it.
        """
        super().__init__(api_key, model)
        self.enable_thinking = enable_thinking

    def _initialize_client(self) -> anthropic.Anthropic:
        """Initialize the Anthropic client."""
        return anthropic.Anthropic(api_key=self.api_key, timeout=60.0)

    def new_async_client(self) -> anthropic.AsyncAnthropic:
        """A client to use as `async with`: providers are mostly built per call,
        and async clients left for GC were seen to hang concurrent requests."""
        return anthropic.AsyncAnthropic(api_key=self.api_key, timeout=60.0)

    def _convert_image_content(self, content: ImageContent) -> dict[str, Any]:
        """Convert ImageContent to Anthropic's base64 source format."""
        if content.image is None:
            raise ValueError("ImageContent.image must not be None")
        encoded_image = self.encode_image(content.image)
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": encoded_image,
            },
        }

    def _convert_message(self, message: Message) -> dict[str, Any]:
        # Handle string content directly
        if isinstance(message.content, str):
            return {"role": message.role.value, "content": message.content}

        # Convert content items, handling ImageContent specially
        content_list = []
        for item in message.content:
            if isinstance(item, ImageContent):
                content_list.append(self._convert_image_content(item))
            else:
                content_list.append(item.to_dict())

        # Sort assistant messages to put thinking last
        if message.role == MessageRole.ASSISTANT:
            content_list = sorted(content_list, key=lambda x: x["type"] != "thinking")

        return {"role": message.role.value, "content": content_list}

    def _should_include_message(self, message: Message) -> bool:
        """Filter out system messages (handled separately in Anthropic)."""
        return message.role != MessageRole.SYSTEM

    def _build_request_kwargs(
        self,
        messages: list[Message],
        system_prompt: str | None,
        tools: list[ToolDefinition] | None,
        settings: LLMSettings,
    ) -> dict[str, Any]:
        """Build common request kwargs for API calls."""
        anthropic_messages = self._convert_messages(messages)

        # Current models reject non-default temperature/top_p/top_k, so the
        # sampling fields of LLMSettings are not forwarded.
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": anthropic_messages,
            "max_tokens": settings.max_tokens,
        }

        if system_prompt and settings.cache_system_prompt:
            kwargs["system"] = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        elif system_prompt:
            kwargs["system"] = system_prompt

        if settings.stop_sequences:
            kwargs["stop_sequences"] = settings.stop_sequences

        if tool_defs := self._convert_tools(tools):
            kwargs["tools"] = tool_defs

        output_config: dict[str, Any] = {}
        if settings.json_schema:
            output_config["format"] = {
                "type": "json_schema",
                "schema": settings.json_schema,
            }
        # Thinking tokens count toward max_tokens, so callers that don't ask
        # for thinking get low effort to keep short-output budgets usable.
        if self.model.startswith(LEGACY_MODEL_PREFIXES):
            pass
        elif self.enable_thinking:
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
        else:
            output_config["effort"] = "low"

        if output_config:
            kwargs["output_config"] = output_config
        return kwargs

    def _handle_stream_event(
        self, event: Any, current_tool_use: dict[str, Any] | None
    ) -> tuple[StreamEvent | None, dict[str, Any] | None]:
        """
        Handle a streaming event and return StreamEvent and updated tool state.

        Returns:
            Tuple of (StreamEvent or None, updated current_tool_use or None)
        """
        event_type = getattr(event, "type", None)
        # Handle error events
        if event_type == "error":
            error = getattr(event, "error", None)
            error_msg = str(error) if error else "Unknown error"
            return StreamEvent(type="error", data=error_msg), current_tool_use

        if event_type == "content_block_start":
            block = getattr(event, "content_block", None)
            if not block:
                return None, current_tool_use

            block_type = getattr(block, "type", None)

            # Handle various tool types (tool_use, mcp_tool_use, server_tool_use)
            if block_type in ("tool_use", "mcp_tool_use", "server_tool_use"):
                # In content_block_start, input may already be present (empty dict)
                block_input = getattr(block, "input", None)
                current_tool_use = {
                    "id": getattr(block, "id", ""),
                    "name": getattr(block, "name", ""),
                    "input": block_input if block_input is not None else "",
                    "server_name": getattr(block, "server_name", None),
                    "is_server_call": block_type != "tool_use",
                }

            # Handle tool result blocks
            elif hasattr(block, "tool_use_id"):
                tool_result = {
                    "id": getattr(block, "tool_use_id", ""),
                    "result": getattr(block, "content", ""),
                }
                return StreamEvent(
                    type="tool_result", data=tool_result
                ), current_tool_use

            # For non-tool blocks (text, thinking), we don't need to track state
            return None, current_tool_use

        elif event_type == "content_block_delta":
            delta = getattr(event, "delta", None)
            if not delta:
                return None, current_tool_use

            delta_type = getattr(delta, "type", None)

            if delta_type == "text_delta":
                text = getattr(delta, "text", "")
                return StreamEvent(type="text", data=text), current_tool_use

            elif delta_type == "thinking_delta":
                thinking = getattr(delta, "thinking", "")
                return StreamEvent(type="thinking", data=thinking), current_tool_use

            elif delta_type == "signature_delta":
                # Handle thinking signature for extended thinking
                signature = getattr(delta, "signature", "")
                return StreamEvent(
                    type="thinking", signature=signature
                ), current_tool_use

            elif delta_type == "input_json_delta":
                if current_tool_use is None:
                    # Edge case: received input_json_delta without tool_use start
                    logger.warning("Received input_json_delta without tool_use context")
                    return None, None

                # Only accumulate if input is still a string (being built up)
                if isinstance(current_tool_use.get("input"), str):
                    partial_json = getattr(delta, "partial_json", "")
                    current_tool_use["input"] += partial_json
                # else: input was already set as a dict in content_block_start

                return None, current_tool_use

        elif event_type == "content_block_stop":
            if current_tool_use:
                # Use the parsed input from the content block if available
                # This handles empty inputs {} more reliably than parsing
                content_block = getattr(event, "content_block", None)
                if content_block and hasattr(content_block, "input"):
                    current_tool_use["input"] = content_block.input
                else:
                    # Fallback: parse accumulated JSON string
                    input_str = current_tool_use.get("input", "")
                    if isinstance(input_str, str):
                        # Need to parse the accumulated string
                        if not input_str or input_str.isspace():
                            # Empty or whitespace-only input
                            current_tool_use["input"] = {}
                        else:
                            try:
                                current_tool_use["input"] = json.loads(input_str)
                            except json.JSONDecodeError as e:
                                logger.warning(
                                    f"Failed to parse tool input '{input_str}': {e}"
                                )
                                current_tool_use["input"] = {}
                    # else: input is already parsed

                tool_data = {
                    "id": current_tool_use.get("id", ""),
                    "name": current_tool_use.get("name", ""),
                    "input": current_tool_use.get("input", {}),
                }
                # Include server info if present
                if current_tool_use.get("server_name"):
                    tool_data["server_name"] = current_tool_use["server_name"]
                if current_tool_use.get("is_server_call"):
                    tool_data["is_server_call"] = current_tool_use["is_server_call"]

                # Emit different event type for MCP server tools
                if current_tool_use.get("is_server_call"):
                    return StreamEvent(type="server_tool_use", data=tool_data), None
                return StreamEvent(type="tool_use", data=tool_data), None

        elif event_type == "message_delta":
            # Handle token usage information
            if usage := getattr(event, "usage", None):
                self.log_usage(
                    Usage(
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        total_tokens=usage.input_tokens + usage.output_tokens,
                    )
                )

            delta = getattr(event, "delta", None)
            if delta:
                stop_reason = getattr(delta, "stop_reason", None)
                if stop_reason == "max_tokens":
                    return StreamEvent(
                        type="error", data="Max tokens reached"
                    ), current_tool_use

            return None, current_tool_use

        elif event_type == "message_stop":
            # Final event - clean up any pending state
            if current_tool_use:
                logger.warning(
                    f"Message ended with incomplete tool use: {current_tool_use}"
                )
            return StreamEvent(type="done"), None

        # Unknown event type - log but don't fail
        if event_type and event_type not in (
            "message_start",
            "message_delta",
            "content_block_start",
            "content_block_delta",
            "content_block_stop",
            "message_stop",
        ):
            logger.debug(f"Unknown event type: {event_type}")

        return None, current_tool_use

    def batch_request(
        self,
        custom_id: str,
        messages: list[Message],
        system_prompt: str | None,
        settings: LLMSettings,
    ) -> dict[str, Any]:
        """A Message Batches API request equivalent to `generate` with the same arguments."""
        params = self._build_request_kwargs(messages, system_prompt, None, settings)
        return {"custom_id": custom_id, "params": params}

    def generate(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        tools: list[ToolDefinition] | None = None,
        settings: LLMSettings | None = None,
    ) -> str:
        """Generate a non-streaming response."""
        settings = settings or LLMSettings()
        kwargs = self._build_request_kwargs(
            messages, system_prompt, tools, settings
        )

        try:
            response = self.client.messages.create(**kwargs)
            return "".join(
                block.text for block in response.content if block.type == "text"
            )
        except Exception as e:
            logger.error(f"Anthropic API error: {e}")
            raise

    def stream(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        tools: list[ToolDefinition] | None = None,
        settings: LLMSettings | None = None,
    ) -> Iterator[StreamEvent]:
        """Generate a streaming response."""
        settings = settings or LLMSettings()
        kwargs = self._build_request_kwargs(
            messages, system_prompt, tools, settings
        )

        try:
            with self.client.messages.stream(**kwargs) as stream:
                current_tool_use: dict[str, Any] | None = None

                for event in stream:
                    stream_event, current_tool_use = self._handle_stream_event(
                        event, current_tool_use
                    )
                    if stream_event:
                        yield stream_event

        except Exception as e:
            logger.error(f"Anthropic streaming error: {e}")
            yield StreamEvent(type="error", data=str(e))

    async def agenerate(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        tools: list[ToolDefinition] | None = None,
        settings: LLMSettings | None = None,
    ) -> str:
        """Generate a non-streaming response asynchronously."""
        settings = settings or LLMSettings()
        kwargs = self._build_request_kwargs(
            messages, system_prompt, tools, settings
        )

        try:
            async with self.new_async_client() as client:
                response = await client.messages.create(**kwargs)
            return "".join(
                block.text for block in response.content if block.type == "text"
            )
        except Exception as e:
            logger.error(f"Anthropic API error: {e}")
            raise

    async def astream(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        tools: list[ToolDefinition] | None = None,
        settings: LLMSettings | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Generate a streaming response asynchronously."""
        settings = settings or LLMSettings()
        kwargs = self._build_request_kwargs(
            messages, system_prompt, tools, settings
        )

        try:
            async with (
                self.new_async_client() as client,
                client.messages.stream(**kwargs) as stream,
            ):
                current_tool_use: dict[str, Any] | None = None

                async for event in stream:
                    stream_event, current_tool_use = self._handle_stream_event(
                        event, current_tool_use
                    )
                    if stream_event:
                        yield stream_event

        except Exception as e:
            logger.error(f"Anthropic streaming error: {e}")
            yield StreamEvent(type="error", data=str(e))

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
        self.log_usage(
            Usage(
                input_tokens=usage.context_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.context_tokens + usage.output_tokens,
            )
        )
        blocks = [b.to_dict() for b in msg.content]
        return self.agent_completion(
            blocks, msg.stop_reason or "", usage, pricing.cost(usage)
        )
