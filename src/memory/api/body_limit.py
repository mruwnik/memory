"""Request body size caps, enforced before authentication.

Every request body is counted as it arrives: a declared Content-Length over
the route's cap is refused before anything is read, and a body that streams
past it is cut off with 413. Routes that skip the auth middleware get a small
cap, since anyone can reach them; uploads and webhooks get the caps they need.
"""

from collections.abc import Callable

from fastapi import HTTPException
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from memory.api.auth import is_whitelisted_path
from memory.common import ingest_routing, settings

# Room for multipart boundaries and part headers around an uploaded file.
MULTIPART_SLACK_BYTES = 1024 * 1024


class BodyTooLarge(HTTPException):
    """Raised from `receive` once a body passes its cap. An HTTPException, so
    FastAPI's body parsing and handlers that read the stream answer 413."""

    def __init__(self, cap: int):
        super().__init__(status_code=413, detail=f"Request body too large (limit {cap} bytes)")


def route_caps() -> list[tuple[str, int]]:
    """(path prefix, cap) for routes whose cap differs from the default."""
    return [
        ("/ingest/upload", ingest_routing.max_ingest_bytes()),
        ("/books/upload", settings.MAX_BOOK_UPLOAD_BYTES + MULTIPART_SLACK_BYTES),
        ("/photos/upload", settings.MAX_PHOTO_UPLOAD_BYTES + MULTIPART_SLACK_BYTES),
        ("/reports/upload", settings.MAX_REPORT_UPLOAD_BYTES + MULTIPART_SLACK_BYTES),
        ("/github/webhook/", settings.GITHUB_WEBHOOK_MAX_BODY_BYTES),
        ("/slack/events/", settings.WEBHOOK_MAX_BODY_BYTES),
        ("/discord/events/", settings.WEBHOOK_MAX_BODY_BYTES),
        ("/transcripts/ingest/", settings.WEBHOOK_MAX_BODY_BYTES),
        # MCP authenticates inside its own app, and tool calls carry inline content.
        ("/mcp", settings.REQUEST_BODY_MAX_BYTES),
    ]


def body_cap_for(path: str) -> int:
    """The most bytes a request to `path` may send."""
    for prefix, cap in route_caps():
        if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
            return cap
    if is_whitelisted_path(path):
        return settings.PUBLIC_REQUEST_BODY_MAX_BYTES
    return settings.REQUEST_BODY_MAX_BYTES


def declared_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name == b"content-length" and value.isdigit():
            return int(value)
    return None


class BodySizeLimitMiddleware:
    """Refuse request bodies larger than their route's cap with 413."""

    def __init__(self, app: ASGIApp, cap_for: Callable[[str], int] = body_cap_for):
        self.app = app
        self.cap_for = cap_for

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        cap = self.cap_for(scope["path"])
        declared = declared_length(scope)
        if declared is not None and declared > cap:
            await too_large_response(cap)(scope, receive, send)
            return

        received = 0
        over = False
        started = False

        async def counted_receive() -> Message:
            nonlocal received, over
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > cap:
                    over = True
                    raise BodyTooLarge(cap)
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            # Middleware between here and the endpoint may turn BodyTooLarge
            # into another error response; once over the cap, the answer is 413.
            if over and not started:
                return
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counted_receive, guarded_send)
        except Exception:
            # Whatever the cut-off read became on its way out (a wrapped
            # BodyTooLarge, a parse error); any other failure propagates.
            if not over or started:
                raise
        if over and not started:
            await too_large_response(cap)(scope, receive, send)


def too_large_response(cap: int) -> PlainTextResponse:
    # The client is still sending; closing tells it to stop.
    return PlainTextResponse(
        f"Request body too large (limit {cap} bytes)",
        status_code=413,
        headers={"Connection": "close"},
    )
