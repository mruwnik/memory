"""
Middleware for filtering MCP tools based on visibility checkers.

Tools register visibility checkers via the @visible_when decorator.
*The middleware fails closed*: a tool with no checker registered is
invisible to everyone. Genuinely public tools must opt in explicitly
via ``@visible_when()`` (which registers an always-True checker), so
every public tool is grep-able and adding a new tool without thinking
about authorization defaults to "denied" rather than "anonymous read".

A user can access a tool if:
- Its server isn't one the admin hid on the Server settings page, and
- The tool's visibility checker returns True for the user.
"""

import logging
from collections.abc import Callable, Sequence

import mcp.types as mt
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import Tool, ToolResult

from memory.api.MCP.servers import effective_disabled_servers, tool_server
from memory.api.MCP.visibility import get_call_checker, get_visibility_checker
from memory.common.db.connection import DBSession, make_session

logger = logging.getLogger(__name__)


class VisibilityMiddleware(Middleware):
    """Filters tools and checks permissions based on visibility checkers."""

    def __init__(self, get_user_info: Callable[[], dict]):
        """
        Args:
            get_user_info: Callable that returns the current user's info dict.
                          Should include 'authenticated', 'scopes', 'user', etc.
        """
        self.get_user_info = get_user_info

    async def _check_visibility(
        self, tool: Tool, user_info: dict, session: DBSession | None = None
    ) -> bool:
        """Check if a tool is visible to the current user.

        Args:
            tool: The FastMCP Tool object
            user_info: Current user's info dict
            session: Optional SQLAlchemy session. If not provided, checker
                    is responsible for creating its own session if needed.

        Returns:
            True if the tool should be visible, False otherwise
        """
        checker = get_visibility_checker(tool.key)

        if checker is None:
            # No checker registered = fail closed. Tools that should
            # be reachable by anyone must register an explicit
            # always-True checker via @visible_when(). This prevents
            # silent "every authenticated MCP client can call this"
            # exposure when a tool author forgets to decorate.
            logger.warning(
                "Tool %s has no visibility checker registered; denying. "
                "Decorate it with @visible_when(...) (or @visible_when() "
                "for genuinely public tools) to expose it.",
                tool.name,
            )
            return False

        try:
            return await checker(user_info, session)
        except Exception as e:
            # Rollback to clear the failed transaction state so subsequent
            # checks can proceed (prevents cascading InFailedSqlTransaction errors)
            if session is not None:
                try:
                    session.rollback()
                except Exception:
                    pass  # Rollback failed, nothing more we can do
            logger.error(
                f"Visibility checker failed for tool {tool.name}: {e}",
                exc_info=True,
            )
            # Fail closed: if checker errors, deny access
            return False

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        """Filter tool list to only show tools the user can access."""
        tools = await call_next(context)
        user_info = self.get_user_info()
        disabled = effective_disabled_servers()

        with make_session() as session:
            filtered = [
                tool
                for tool in tools
                if tool_server(tool.key) not in disabled
                and await self._check_visibility(tool, user_info, session)
            ]

        logger.debug(
            f"Filtered tools: {len(filtered)}/{len(tools)} "
            f"(user: {user_info.get('user', {}).get('user_id', 'anonymous')})"
        )
        return filtered

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Check visibility before allowing tool execution.

        This is a defense-in-depth check. Tools shouldn't be visible to users
        who can't access them, but we check again here in case of direct API calls
        or race conditions.
        """
        tool_name = context.message.name
        if tool_server(tool_name) in effective_disabled_servers():
            raise ToolError(f"Access denied: {tool_name}")
        checker = get_call_checker(tool_name)

        if checker is None:
            # Fail closed: refuse to invoke a tool that hasn't opted
            # in to the visibility system. See the docstring above for
            # rationale; this matches the on_list_tools default.
            logger.warning(
                "Refusing to call tool %s: no visibility checker registered",
                tool_name,
            )
            raise ToolError(f"Access denied: {tool_name}")

        user_info = self.get_user_info()

        try:
            with make_session() as session:
                allowed = await checker(user_info, session)
        except Exception as e:
            logger.error(
                f"Visibility checker failed during tool call for {tool_name}: {e}",
                exc_info=True,
            )
            raise ToolError(f"Access denied: {tool_name}")

        if not allowed:
            logger.warning(
                f"Access denied for tool {tool_name} "
                f"(user: {user_info.get('user', {}).get('user_id', 'anonymous')})"
            )
            raise ToolError(f"Access denied: {tool_name}")

        return await call_next(context)
