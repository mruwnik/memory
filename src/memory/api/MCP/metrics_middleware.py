"""
Middleware for recording MCP tool call metrics.

Records timing and status for all MCP tool invocations to the metrics system.
"""

import logging
import time
from collections.abc import Callable
from typing import Any

import mcp.types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult

from memory.common.metrics import record_metric

logger = logging.getLogger(__name__)


class MetricsMiddleware(Middleware):
    """Records timing and status metrics for MCP tool calls."""

    def __init__(
        self,
        get_user_info: Callable[[], dict[str, Any]] | None = None,
        prefixes: list[str] | None = None,
    ):
        """
        Args:
            get_user_info: Optional callable that returns current user info dict.
                          Used to add user_id to metrics labels.
                          MUST be lightweight (no DB queries) as it's called synchronously.
            prefixes: List of known tool name prefixes from mounted subservers.
        """
        self.get_user_info = get_user_info
        self.prefixes = prefixes or []

    def _get_base_tool_name(self, tool_name: str) -> str:
        """Strip the prefix from a mounted tool name."""
        for prefix in self.prefixes:
            if tool_name.startswith(f"{prefix}_"):
                return tool_name[len(prefix) + 1 :]
        return tool_name

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Record metrics for tool execution."""
        tool_name = context.message.name
        base_name = self._get_base_tool_name(tool_name)
        start_time = time.perf_counter()
        status = "success"

        try:
            result = await call_next(context)
            # Check if the tool returned an error (use getattr for compatibility)
            if getattr(result, "isError", False):
                status = "error"
            return result
        except Exception:
            status = "failure"
            raise
        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000

            # Build labels
            labels: dict = {
                "full_name": tool_name,
            }

            # Add user info if available
            if self.get_user_info:
                try:
                    user_info = self.get_user_info()
                    user = user_info.get("user", {})
                    if user.get("user_id"):
                        labels["user_id"] = user["user_id"]
                except Exception:
                    pass  # Don't fail metrics recording due to user info issues

            record_metric(
                metric_type="mcp_call",
                name=base_name,
                duration_ms=duration_ms,
                status=status,
                labels=labels,
            )

            logger.debug(
                f"MCP tool {tool_name} completed in {duration_ms:.2f}ms (status={status})"
            )
