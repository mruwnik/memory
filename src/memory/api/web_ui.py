"""Serves the built web UI at the root, behind every backend route."""

import pathlib
from collections.abc import Sequence

from starlette.datastructures import Headers, URL
from starlette.responses import FileResponse, RedirectResponse
from starlette.routing import BaseRoute, Match, Mount
from starlette.types import ASGIApp, Receive, Scope, Send

from memory.common import paths, settings

LEGACY_PREFIX = "/ui"
# Left at the start of a redirect target, these make it protocol-relative: another host.
UNSAFE_LEADING = "/\\" + "".join(map(chr, range(33)))


def is_backend_path(router: ASGIApp, scope: Scope) -> bool:
    """Whether any route answers ``scope``'s path, for any method."""
    return matches_any(getattr(router, "routes", []), scope)


def matches_any(routes: Sequence[BaseRoute], scope: Scope) -> bool:
    for route in routes:
        match, child = route.matches(scope)
        if match == Match.NONE:
            continue
        # The MCP app is mounted at the root, so a mount only counts if one of its routes matches.
        if isinstance(route, Mount) and route.routes:
            if matches_any(route.routes, {**scope, **child}):
                return True
            continue
        return True
    return False


def static_file(path: str) -> pathlib.Path | None:
    try:
        found = paths.validate_path_within_directory(settings.STATIC_DIR, path, require_exists=True)
    except ValueError:
        return None
    return found if found.is_file() else None


class WebUIMiddleware:
    """Gives unrouted GETs the UI's static files, and browser navigations its index.html.

    Runs outside authentication so logged-out visitors get the UI, which sends them
    to its login page. Anything a backend route answers, and every other request,
    passes through untouched, so API 401s and 404s stay as they are.
    """

    def __init__(self, app: ASGIApp, router: ASGIApp) -> None:
        self.app = app
        self.router = router

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in ("GET", "HEAD"):
            return await self.app(scope, receive, send)

        path = scope["path"]
        if path == LEGACY_PREFIX or path.startswith(LEGACY_PREFIX + "/"):
            # Temporary, so a rollback to a release that serves /ui isn't stuck behind cached redirects.
            rest = path.removeprefix(LEGACY_PREFIX).lstrip(UNSAFE_LEADING)
            target = URL(path="/" + rest, query=URL(scope=scope).query)
            return await RedirectResponse(str(target), status_code=307)(scope, receive, send)

        if is_backend_path(self.router, scope):
            return await self.app(scope, receive, send)

        if found := static_file(path):
            return await FileResponse(found)(scope, receive, send)
        if "text/html" in Headers(scope=scope).get("accept", ""):
            return await FileResponse(settings.STATIC_DIR / "index.html")(scope, receive, send)
        await self.app(scope, receive, send)
