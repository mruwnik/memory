"""Per-client request rate limits, applied to every HTTP request before routing.

Runs as ASGI middleware outside authentication, so it covers routes on routers
at any nesting depth, the mounted MCP app (OAuth endpoints, ``/mcp``) and
requests carrying bogus credentials alike. Clients are keyed by
``rate_limit_key``. Counters live in this process's memory: the api runs as one
uvicorn process, and the limit holds even while Redis is down.
"""

import re
import time
from functools import cache

from limits import RateLimitItem, parse
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from memory.common import settings
from memory.common.rate_limit import rate_limit_key

# The container healthcheck polls it every 15 s.
EXEMPT_PATHS = frozenset({"/health"})
WEBHOOK_PATH = re.compile(
    r"/(github/webhook|slack/events|discord/events|transcripts/ingest)/[^/]+"
)

storage = MemoryStorage()
limiter = FixedWindowRateLimiter(storage)


@cache
def parse_spec(spec: str) -> RateLimitItem:
    return parse(spec)


def limit_specs(path: str) -> list[tuple[str, str]]:
    """The (bucket name, limit spec) pairs a request to ``path`` counts against."""
    if path in EXEMPT_PATHS:
        return []
    if WEBHOOK_PATH.fullmatch(path):
        return [("webhooks", settings.API_RATE_LIMIT_WEBHOOKS)]
    if path == "/mcp" or path.startswith("/mcp/"):
        return [("mcp", settings.API_RATE_LIMIT_MCP)]
    extra = {
        "/register": settings.API_RATE_LIMIT_OAUTH_REGISTER,
        "/authorize": settings.API_RATE_LIMIT_OAUTH_AUTHORIZE,
    }.get(path)
    default = [("default", settings.API_RATE_LIMIT_DEFAULT)]
    return default + [(path, extra)] if extra else default


def exceeded_limit(path: str, key: str) -> tuple[str, RateLimitItem] | None:
    """Count one request from ``key`` to ``path``; the first limit it exceeds, if any."""
    for bucket, spec in limit_specs(path):
        item = parse_spec(spec)
        if not limiter.hit(item, bucket, key):
            return bucket, item
    return None


def reset() -> None:
    """Forget every counter."""
    storage.reset()


def too_many_requests(bucket: str, item: RateLimitItem, key: str) -> JSONResponse:
    reset_at = limiter.get_window_stats(item, bucket, key).reset_time
    retry_after = max(1, int(reset_at - time.time()))
    return JSONResponse(
        status_code=429,
        content={
            "error": "Rate limit exceeded",
            "detail": str(item),
            "retry_after": retry_after,
        },
        headers={"Retry-After": str(retry_after)},
    )


class RequestRateLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not settings.API_RATE_LIMIT_ENABLED:
            return await self.app(scope, receive, send)
        key = rate_limit_key(Request(scope))
        exceeded = exceeded_limit(scope["path"], key)
        if exceeded is None:
            return await self.app(scope, receive, send)
        await too_many_requests(*exceeded, key)(scope, receive, send)
