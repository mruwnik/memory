from dataclasses import dataclass
from typing import Any, Callable, TypedDict


ToolInput = str | dict[str, Any] | None
ToolHandler = Callable[[ToolInput], str]


class ToolCall(TypedDict):
    """A call to a tool."""

    name: str
    id: str
    input: ToolInput


class ToolResult(TypedDict):
    """A result from a tool call."""

    id: str
    name: str
    input: ToolInput
    output: str


@dataclass
class MCPServer:
    """An MCP server."""

    name: str
    url: str
    token: str
    allowed_tools: list[str] | None = None


@dataclass
class ToolDefinition:
    """Definition of a tool that can be called by the LLM."""

    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema for the tool's parameters
    function: ToolHandler

    def __call__(self, input: ToolInput) -> str:
        return self.function(input)

    def provider_format(self, provider: str) -> dict[str, Any] | None:
        return None
