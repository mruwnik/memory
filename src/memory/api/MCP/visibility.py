"""
Tool visibility control system for MCP.

Tools can register visibility checkers that determine whether a tool is visible
and callable for a given user. This provides flexible, per-tool access control
beyond simple scope-based filtering.

Usage:
    from memory.api.MCP.visibility import visible_when, require_scopes

    # Simple scope-based access
    @mcp.tool()
    @visible_when(require_scopes("read"))
    async def my_tool(...):
        ...

    # Multiple checkers - all must pass
    @mcp.tool()
    @visible_when(require_scopes("admin"), custom_checker)
    async def admin_tool(...):
        ...

    # Custom checker with database access
    async def custom_checker(user_info: dict, session: Session) -> bool:
        user_id = user_info.get("user", {}).get("user_id")
        # Query database, etc.
        return some_condition

    @mcp.tool()
    @visible_when(custom_checker)
    async def restricted_tool(...):
        ...
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@runtime_checkable
class VisibilityChecker(Protocol):
    """Protocol for tool visibility checker functions.

    Checkers receive user information and a database session, and return
    True if the tool should be visible/callable, False otherwise.

    Args:
        user_info: Dict containing:
            - 'authenticated': bool
            - 'scopes': list[str] - user's access scopes
            - 'user': dict with 'user_id', 'email', etc.
            - 'client_id': OAuth client ID
        session: SQLAlchemy database session for queries

    Returns:
        True if the tool should be visible/callable, False otherwise
    """

    async def __call__(self, user_info: dict, session: Session) -> bool: ...


# Type alias for checker functions
VisibilityCheckerFunc = Callable[[dict, Session], Awaitable[bool]]

# Registry: function_name (without prefix) -> checker
_visibility_checkers: dict[str, VisibilityCheckerFunc] = {}


def register_visibility(tool_name: str, checker: VisibilityCheckerFunc) -> None:
    """Register a visibility checker for a tool by its function name.

    Args:
        tool_name: The function name of the tool (without any prefix)
        checker: Async function that determines tool visibility
    """
    logger.debug(f"Registering visibility checker for tool: {tool_name}")
    _visibility_checkers[tool_name] = checker


def get_visibility_checker(tool_name: str) -> VisibilityCheckerFunc | None:
    """Get the visibility checker for a tool, or None if unrestricted.

    Args:
        tool_name: The function name of the tool (without prefix)

    Returns:
        The registered checker, or None if no restrictions apply
    """
    return _visibility_checkers.get(tool_name)


def get_all_checkers() -> dict[str, VisibilityCheckerFunc]:
    """Get all registered visibility checkers (for debugging/testing)."""
    return _visibility_checkers.copy()


def clear_checkers() -> None:
    """Clear all registered checkers (for testing)."""
    _visibility_checkers.clear()


# --- Common checker factories ---


def require_scopes(*scopes: str) -> VisibilityCheckerFunc:
    """Create a checker that requires the user to have at least one of the specified scopes.

    This is the most common case - simple scope-based access control without
    needing database queries.

    Args:
        *scopes: One or more scope names. User must have at least one.

    Returns:
        A visibility checker function

    Example:
        @visible_when(require_scopes("read"))
        async def search_tool(...): ...

        @visible_when(require_scopes("admin", "superuser"))  # Either works
        async def admin_tool(...): ...
    """

    async def checker(user_info: dict, session: Session) -> bool:
        user_scopes = user_info.get("scopes", [])
        # Wildcard grants access to everything
        if "*" in user_scopes:
            return True
        return any(scope in user_scopes for scope in scopes)

    # Add metadata for debugging
    checker.__name__ = f"require_scopes({', '.join(scopes)})"
    return checker


def has_items(model_class: type) -> VisibilityCheckerFunc:
    """Create a checker that returns True only if items of this model exist.

    Use this to hide tools when there's no data to operate on.

    Args:
        model_class: SQLAlchemy model class to check for existence

    Returns:
        A visibility checker function

    Example:
        @visible_when(require_scopes("read"), has_items(Book))
        async def all_books(...): ...
    """

    def _sync_check(session: Session) -> bool:
        """Synchronous query - runs in thread pool to avoid blocking event loop."""
        return session.query(model_class).limit(1).count() > 0

    async def checker(user_info: dict, session: Session) -> bool:
        if session is None:
            # Can't check without session, default to visible
            return True
        # Run sync query in thread pool to avoid blocking event loop
        return await asyncio.to_thread(_sync_check, session)

    checker.__name__ = f"has_items({model_class.__name__})"
    return checker


# --- Decorator ---


def visible_when(*checkers: VisibilityCheckerFunc):
    """Decorator to register visibility checkers for a tool.

    Place this decorator AFTER @mcp.tool() (closer to the function definition)
    so that it sees the original function name.

    Args:
        *checkers: Zero or more visibility checker functions.
                   If empty, tool is unrestricted (visible to all).
                   If multiple, ALL must pass for the tool to be visible.

    Example:
        @mcp.tool()
        @visible_when(require_scopes("read"))
        async def my_tool(...):
            ...

        # Multiple checkers - all must pass
        @mcp.tool()
        @visible_when(require_scopes("admin"), custom_org_checker)
        async def admin_tool(...):
            ...

        # No checkers - unrestricted
        @mcp.tool()
        @visible_when()
        async def public_tool(...):
            ...
    """

    def decorator(func):
        if not checkers:
            # No checkers = unrestricted, don't register anything
            return func

        async def combined(user_info: dict, session: Session) -> bool:
            for checker in checkers:
                if not await checker(user_info, session):
                    return False
            return True

        register_visibility(func.__name__, combined)
        return func

    return decorator
