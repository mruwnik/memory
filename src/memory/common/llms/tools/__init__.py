from dataclasses import dataclass
from typing import Any


@dataclass
class MCPServer:
    """An MCP server."""

    name: str
    url: str
    token: str
    allowed_tools: list[str] | None = None
    disabled_tools: list[str] | None = None

    @classmethod
    def from_model(cls, model) -> "MCPServer":
        return cls(
            name=model.name,
            url=model.mcp_server_url,
            token=model.access_token,
            disabled_tools=model.disabled_tools or None,
        )


@dataclass(frozen=True)
class ToolDefinition:
    """Definition of a tool that can be called by the LLM."""

    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema for the tool's parameters
    strict: bool = False
