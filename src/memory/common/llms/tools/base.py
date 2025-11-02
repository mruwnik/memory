from typing import Any
from memory.common.llms.tools import ToolDefinition


class WebSearchTool(ToolDefinition):
    def __init__(self, **kwargs: Any):
        defaults = {
            "name": "web_search",
            "description": "Search the web for information",
            "input_schema": {},
            "function": lambda input: "result",
        }
        super().__init__(**(defaults | kwargs))

    def provider_format(self, provider: str) -> dict[str, Any] | None:
        if provider == "openai":
            return {"type": "web_search"}
        if provider == "anthropic":
            return {"type": "web_search_20250305", "name": "web_search", "max_uses": 10}
        return None


class WebFetchTool(ToolDefinition):
    def __init__(self, **kwargs: Any):
        defaults = {
            "name": "web_fetch",
            "description": "Fetch the contents of a web page",
            "input_schema": {},
            "function": lambda input: "result",
        }
        super().__init__(**(defaults | kwargs))

    def provider_format(self, provider: str) -> dict[str, Any] | None:
        if provider == "anthropic":
            return {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 10}
        return None
