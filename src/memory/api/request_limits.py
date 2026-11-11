"""Per-client request rate limits, applied to every HTTP request before routing.

Runs as ASGI middleware outside authentication, so it covers routes on routers
at any nesting depth, the mounted MCP app (OAuth endpoints, ``/mcp``) and
requests carrying bogus credentials alike. Clients are keyed by
``rate_limit_key`` (their address), except that the default limit counts a
request carrying a credential against that credential, so anonymous traffic
from a shared address doesn't lock out the users behind it. Credentials aren't
checked here; a per-address ceiling on credentialed requests keeps made-up
ones from escaping the limit. Counters live in this process's memory: the api
runs as one uvicorn process, and the limit holds even while Redis is down.
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

from memory.api.auth import get_token
from memory.common import settings
from memory.common.db.models.users import hash_token
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


def keyed_limits(path: str, address: str, credential: str | None) -> list[tuple[str, str, str]]:
    """The (bucket name, limit spec, client key) triples a request counts against:
    ``limit_specs`` by address, but the default bucket by credential when the
    request carries one, plus the address's ceiling on credentialed requests."""
    specs = limit_specs(path)
    if credential is None:
        return [(bucket, spec, address) for bucket, spec in specs]
    principal = "credential:" + hash_token(credential)
    keyed = [(bucket, spec, principal if bucket == "default" else address) for bucket, spec in specs]
    if any(bucket == "default" for bucket, _ in specs):
        keyed.append(("credentialed", settings.API_RATE_LIMIT_CREDENTIALED_ADDRESS, address))
    return keyed


def exceeded_limit(
    path: str, address: str, credential: str | None = None
) -> tuple[str, RateLimitItem, str] | None:
    """Count one request to ``path``; the first limit it exceeds and the key it
    was counted under, if any."""
    for bucket, spec, key in keyed_limits(path, address, credential):
        item = parse_spec(spec)
        if not limiter.hit(item, bucket, key):
            return bucket, item, key
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
        request = Request(scope)
        exceeded = exceeded_limit(scope["path"], rate_limit_key(request), get_token(request))
        if exceeded is None:
            return await self.app(scope, receive, send)
        await too_many_requests(*exceeded)(scope, receive, send)
