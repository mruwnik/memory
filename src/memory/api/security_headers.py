"""Baseline security headers on every HTTP response, the mounted MCP app included.

Pages (the web UI, the OAuth login page) refuse to be framed, so another site can't
overlay them and steer clicks or a password manager's autofill. Stored reports and
files may be framed by the web UI itself, so they allow same-origin framing and send
no referrer, keeping their URLs off any link a report author placed in them.
"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SAME_ORIGIN_FRAMED_PREFIXES = ("/reports/", "/files/")


def is_same_origin_framed(path: str) -> bool:
    return path.startswith(SAME_ORIGIN_FRAMED_PREFIXES)


def security_headers(path: str) -> dict[str, str]:
    """The headers a response to ``path`` carries."""
    if is_same_origin_framed(path):
        return {
            "X-Frame-Options": "SAMEORIGIN",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "frame-ancestors 'self'",
        }
    return {
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "same-origin",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "frame-ancestors 'none'",
    }


def apply_security_headers(headers: MutableHeaders, path: str) -> None:
    """Set the baseline headers, adding our directive to any CSP the route set itself."""
    wanted = security_headers(path)
    route_csp = headers.get("Content-Security-Policy")
    if route_csp:
        wanted["Content-Security-Policy"] = f"{route_csp}; {wanted['Content-Security-Policy']}"
    headers.update(wanted)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope["path"]

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                apply_security_headers(MutableHeaders(scope=message), path)
            await send(message)

        await self.app(scope, receive, send_with_headers)
