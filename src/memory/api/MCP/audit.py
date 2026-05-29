"""Audit logging for mutating MCP tool calls.

`@audit_call` records a tool invocation (name + arguments) to telemetry_events
so that destructive calls are recoverable post-hoc (issue #83). Recording is
best-effort and never blocks the wrapped tool.

Semantics: the call is logged *before* the tool body runs, so the row records
an *attempted* invocation (which may later fail or be denied by an in-tool
authorization check), not a completed one. Calls rejected earlier by FastMCP
visibility/scope middleware never reach the wrapper and are not logged.
"""

import functools
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

from memory.api.MCP.access import get_mcp_current_user
from memory.common.telemetry import record_event

logger = logging.getLogger(__name__)

P = ParamSpec("P")
T = TypeVar("T")

REDACTED = "***"
MAX_DEPTH = 32

# Deny-list backstop: argument/key names always masked, even if a call site
# forgets to pass `redact=`. Keeps the decorator fail-safe for future tools.
DEFAULT_REDACT: frozenset[str] = frozenset({
    "password",
    "passwd",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "client_secret",
    "private_key",
    "credential",
    "credentials",
    "authorization",
    "webhook_url",
})


def sanitize_value(
    value: Any, redact: frozenset[str], max_len: int, depth: int = 0, key: str | None = None
) -> Any:
    """Mask values whose name is redacted; otherwise recurse and truncate.

    `key` is the name `value` appeared under (a function-arg name or a dict
    key). If that name is in `redact`, the value is masked outright. Otherwise:
    dicts/lists recurse (bounded at `MAX_DEPTH`), and over-long strings are
    truncated to `max_len`. The string branch is truncation, not redaction —
    a different concern from the name-based masking above.
    """
    if key is not None and key in redact:
        return REDACTED
    if depth >= MAX_DEPTH:
        return "…(max depth)"
    if isinstance(value, dict):
        return {k: sanitize_value(v, redact, max_len, depth + 1, key=k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        capped = [sanitize_value(v, redact, max_len, depth + 1) for v in list(value)[:max_len]]
        if len(value) > max_len:
            capped.append(f"…(+{len(value) - max_len} more)")
        return capped
    if isinstance(value, str) and len(value) > max_len:
        return value[:max_len] + "…"
    return value


def build_attributes(
    func: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    redact: frozenset[str],
    max_value_len: int,
) -> dict[str, Any]:
    """Bind the *supplied* call args to parameter names, redacting/truncating.

    Defaults are intentionally not applied: the row reflects what the caller
    actually passed, not the function's default sentinels. The bound-arg dict
    is just another dict for `sanitize_value` to walk, so arg-name redaction
    falls out of the same recursion.
    """
    try:
        bound = inspect.signature(func).bind_partial(*args, **kwargs)
        raw = dict(bound.arguments)
    except TypeError:
        raw = dict(kwargs)
    return sanitize_value(raw, redact, max_value_len)


def audit_call(
    *,
    level: int | None = None,
    redact: tuple[str, ...] = (),
    max_value_len: int = 2000,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Decorator: log a mutating MCP tool call to telemetry_events.

    Apply closest to the function (below @mcp.tool() / @visible_when):

        @teams_mcp.tool()
        @visible_when(require_scopes(SCOPE_TEAMS_WRITE))
        @audit_call(level=logging.WARNING)
        async def upsert(...): ...

    Args:
        level: if set, also emit logger.log(level, ...) for the call.
        redact: extra argument (or nested dict key) names to mask, merged with
            the always-on DEFAULT_REDACT deny-list.
        max_value_len: per-value truncation threshold for strings/sequences.
    """
    redact_set = DEFAULT_REDACT | frozenset(redact)

    def decorator(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            try:
                attributes = build_attributes(func, args, kwargs, redact_set, max_value_len)
                actor = get_mcp_current_user()
                if actor and actor.id is not None:
                    record_event(
                        name="mcp.call",
                        user_id=actor.id,
                        event_type="log",
                        tool_name=func.__name__,
                        attributes=attributes,
                    )
                if level is not None:
                    logger.log(level, "MCP call: %s args=%s", func.__name__, attributes)
            except Exception:
                logger.exception("audit_call: failed to record %s", func.__name__)
            return await func(*args, **kwargs)

        return wrapper

    return decorator
