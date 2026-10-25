"""
Tool visibility control system for MCP.

Tools register visibility checkers (via the @visible_when decorator) that
decide whether the tool is visible and callable for a given user. The
middleware *fails closed*: a tool with no decorator is invisible to
everyone. To expose a tool with no scope check, decorate it with the
empty form ``@visible_when()`` — that registers an "always public"
checker explicitly, so every public tool is grep-able.

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
    async def custom_checker(user_info: dict, session: DBSession) -> bool:
        user_id = user_info.get("user", {}).get("user_id")
        # Query database, etc.
        return some_condition

    @mcp.tool()
    @visible_when(custom_checker)
    async def restricted_tool(...):
        ...

    # Genuinely public utility — explicit opt-out of any scope check
    @mcp.tool()
    @visible_when()
    async def public_tool(...):
        ...
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence

from memory.common.db.connection import DBSession, make_session
from memory.common.scopes import SCOPE_ADMIN

logger = logging.getLogger(__name__)


# Type alias for checker functions
VisibilityCheckerFunc = Callable[[dict, DBSession | None], Awaitable[bool]]

# Registry: mounted tool name (e.g. "people_fetch") -> (listing checker, call checker)
_visibility_checkers: dict[str, tuple[VisibilityCheckerFunc, VisibilityCheckerFunc]] = {}

# Checkers that only decide whether a tool is worth listing, never who may call it.
_listing_only: set[VisibilityCheckerFunc] = set()


def tool_key(func: Callable) -> str:
    """The name ``func`` is exposed under once its subserver is mounted.

    Each subserver lives in a module named ``<prefix>.py`` and is mounted
    under ``<prefix>`` (see ``MCPServer``), so the module name is the prefix.
    Keying on the bare function name would let same-named tools in
    different subservers (``fetch``, ``upsert``, ...) share one checker.
    """
    return f"{func.__module__.rsplit('.', 1)[-1]}_{func.__name__}"


def register_visibility(
    tool_name: str,
    checker: VisibilityCheckerFunc,
    call_checker: VisibilityCheckerFunc | None = None,
) -> None:
    """Register a tool's listing checker, and its call checker if that differs.

    Raises ValueError on a second registration for the same name, so a
    collision fails at import instead of silently replacing a checker.
    """
    if tool_name in _visibility_checkers:
        raise ValueError(f"Visibility checker already registered for {tool_name}")
    logger.debug(f"Registering visibility checker for tool: {tool_name}")
    _visibility_checkers[tool_name] = (checker, call_checker or checker)


def get_visibility_checker(tool_name: str) -> VisibilityCheckerFunc | None:
    """The checker deciding whether a tool is listed, or None if none is registered."""
    entry = _visibility_checkers.get(tool_name)
    return entry and entry[0]


def get_call_checker(tool_name: str) -> VisibilityCheckerFunc | None:
    """The checker deciding whether a tool may be called, or None if none is registered."""
    entry = _visibility_checkers.get(tool_name)
    return entry and entry[1]


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

    async def checker(user_info: dict, session: DBSession | None) -> bool:
        user_scopes = user_info.get("scopes", [])
        # Wildcard grants access to everything
        if SCOPE_ADMIN in user_scopes:
            return True
        return any(scope in user_scopes for scope in scopes)

    # Add metadata for debugging
    checker.__name__ = f"require_scopes({', '.join(scopes)})"
    return checker


def has_items(model_class: type) -> VisibilityCheckerFunc:
    """Create a checker that returns True only if items of this model exist.

    Use this to hide tools when there's no data to operate on. It only
    affects listing: the table is global, not per-user, so an empty one
    says nothing about access, and a call still returns empty results.

    Args:
        model_class: SQLAlchemy model class to check for existence

    Returns:
        A visibility checker function

    Example:
        @visible_when(require_scopes("read"), has_items(Book))
        async def all_books(...): ...
    """

    def _sync_check() -> bool:
        """Synchronous query - runs in thread pool to avoid blocking event loop."""
        # Create our own session to avoid threading issues with passed session
        with make_session() as local_session:
            return local_session.query(model_class).limit(1).count() > 0

    async def checker(user_info: dict, session: DBSession | None) -> bool:
        # Run sync query in thread pool with its own session
        return await asyncio.to_thread(_sync_check)

    checker.__name__ = f"has_items({model_class.__name__})"
    _listing_only.add(checker)
    return checker


# --- Decorator ---


def combine(checkers: Sequence[VisibilityCheckerFunc]) -> VisibilityCheckerFunc:
    """A checker that passes when every one of ``checkers`` does, stopping at the first failure."""

    async def combined(user_info: dict, session: DBSession | None) -> bool:
        for checker in checkers:
            if not await checker(user_info, session):
                return False
        return True

    return combined


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
            # No checkers = explicitly public. Register an always-True
            # checker so the middleware (which fails closed on missing
            # registrations) treats this as deliberately exposed.
            async def always_public(
                user_info: dict, session: DBSession | None
            ) -> bool:
                return True

            always_public.__name__ = "always_public"
            register_visibility(tool_key(func), always_public)
            return func

        call_checkers = [c for c in checkers if c not in _listing_only]
        if not call_checkers:
            # Otherwise the call checker would pass everyone.
            raise ValueError(
                f"{tool_key(func)}: has_items only hides a tool; add a checker that authorizes calls"
            )
        register_visibility(
            tool_key(func), combine(checkers), combine(call_checkers)
        )
        return func

    return decorator
