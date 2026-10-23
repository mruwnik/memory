"""The browser leg of authorizing an external MCP server.

Everything else about external servers is an MCP tool; a redirect from the
server's authorization page can't be.
"""

from html import escape

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse

from memory.agents.external import finish_authorization
from memory.api.auth import get_session_user
from memory.common.db.connection import make_session
from memory.common.mcp_oauth import StateError, WrongUser

router = APIRouter(prefix="/auth/mcp", tags=["mcp-servers"])


def page(title: str, message: str, status_code: int) -> Response:
    return Response(
        content=(
            f"<html><body><h1>{escape(title)}</h1><p>{escape(message)}</p>"
            '<p><a href="/ui/agents/servers">Your MCP servers</a></p></body></html>'
        ),
        media_type="text/html",
        status_code=status_code,
    )


@router.get("/callback")
async def mcp_oauth_callback(request: Request) -> Response:
    # Authenticated here rather than by the middleware, so a logged-out user
    # gets a page to act on and the state survives until they log in.
    with make_session() as db:
        user = get_session_user(request, db)
        user_id = user.id if user else None
    if user_id is None:
        return page(
            "Log in to finish",
            "Log in to memory in this browser, then reload this page.",
            401,
        )
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state:
        return page(
            "Authorization failed",
            "The server didn't authorize memory. Start again from your MCP servers page.",
            400,
        )
    try:
        server_id, connected = await finish_authorization(
            state, code, user_id, request.query_params.get("iss")
        )
    except WrongUser as e:
        return page("Wrong account", str(e), 403)
    except StateError as e:
        return page("Authorization failed", str(e), 400)
    outcome = "connected=1" if connected else "failed=1"
    return RedirectResponse(f"/ui/agents/servers/{server_id}?{outcome}", status_code=303)
