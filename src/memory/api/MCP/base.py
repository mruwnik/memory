import logging
import pathlib

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.templating import Jinja2Templates

from memory.api.MCP.oauth_provider import SimpleOAuthProvider
from memory.api.MCP.visibility_middleware import VisibilityMiddleware
from memory.api.MCP.metrics_middleware import MetricsMiddleware
from memory.api.MCP.servers.books import books_mcp
from memory.api.MCP.servers.core import core_mcp
from memory.api.MCP.servers.discord import discord_mcp
from memory.api.MCP.servers.email import email_mcp
from memory.api.MCP.servers.github import github_mcp
from memory.api.MCP.servers.meta import meta_mcp
from memory.api.MCP.servers.organizer import organizer_mcp
from memory.api.MCP.servers.people import people_mcp
from memory.api.MCP.servers.polling import polling_mcp
from memory.api.MCP.servers.schedule import schedule_mcp
from memory.api.MCP.servers.schedule import set_auth_provider as set_schedule_auth
from memory.common import settings
from memory.common.db.connection import make_session, get_engine
from memory.common.db.models import OAuthState, UserSession
from memory.common.db.models.users import HumanUser

logger = logging.getLogger(__name__)
engine = get_engine()


# Setup templates
template_dir = pathlib.Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=template_dir)


oauth_provider = SimpleOAuthProvider()

mcp = FastMCP(
    settings.APP_NAME,
    auth=oauth_provider,
)


# List of prefixes used when mounting subservers
# Used by middleware to strip prefixes when looking up visibility checkers
SUBSERVER_PREFIXES = ["core", "discord", "email", "github", "organizer", "people", "polling", "schedule", "books", "meta"]


def _get_user_info_for_middleware() -> dict:
    """Get the current user's info for visibility middleware.

    This is a lightweight version that doesn't do DB lookups - it uses
    the access token directly. For full user info, use get_current_user().
    """
    access_token = get_access_token()
    logger.debug(f"_get_user_info_for_middleware: access_token={access_token}")
    if not access_token:
        logger.debug("_get_user_info_for_middleware: No access token")
        return {"authenticated": False, "scopes": []}

    scopes = list(access_token.scopes) if access_token.scopes else ["read"]
    return {
        "authenticated": True,
        "scopes": scopes,
        "client_id": access_token.client_id,
        "token": access_token.token,
        # Note: user details are fetched lazily by get_current_user() if needed
        # For visibility checks, scopes are usually sufficient
        # Checkers needing user_id can look up UserSession via token
    }


# Add visibility-based tool filtering middleware
mcp.add_middleware(
    VisibilityMiddleware(_get_user_info_for_middleware, prefixes=SUBSERVER_PREFIXES)
)

# Add metrics middleware to record tool call timing
mcp.add_middleware(
    MetricsMiddleware(get_user_info=_get_user_info_for_middleware, prefixes=SUBSERVER_PREFIXES)
)


@mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
async def oauth_protected_resource(request: Request):
    """OAuth 2.0 Protected Resource Metadata."""
    metadata = oauth_provider.get_protected_resource_metadata()
    return JSONResponse(metadata)


def login_form(request: Request, form_data: dict, error: str | None = None):
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "form_data": form_data,
            "error": error,
            "action": "/oauth/login",
        },
    )


@mcp.custom_route("/oauth/login", methods=["GET"])
async def login_page(request: Request):
    """Display the login page."""
    form_data = dict(request.query_params)

    state = form_data.get("state")
    with make_session() as session:
        oauth_state = (
            session.query(OAuthState).filter(OAuthState.state == state).first()
        )
        if not oauth_state:
            logger.error(f"State {state} not found in database")
            raise ValueError("Invalid state parameter")

    return login_form(request, form_data, None)


@mcp.custom_route("/oauth/login", methods=["POST"])
async def handle_login(request: Request):
    """Handle login form submission."""
    form = await request.form()
    oauth_params = {
        key: value for key, value in form.items() if key not in ["email", "password"]
    }
    with make_session() as session:
        user = (
            session.query(HumanUser)
            .filter(HumanUser.email == form.get("email"))
            .first()
        )
        if not user or not user.is_valid_password(str(form.get("password", ""))):
            logger.warning("Login failed - invalid credentials")
            return login_form(request, oauth_params, "Invalid email or password")

        redirect_url = await oauth_provider.complete_authorization(oauth_params, user)
        if redirect_url.startswith("http://anysphere.cursor-retrieval"):
            redirect_url = redirect_url.replace("http://", "cursor://")
        return RedirectResponse(url=redirect_url, status_code=302)


def get_current_user() -> dict:
    access_token = get_access_token()

    if not access_token:
        return {"authenticated": False}

    with make_session() as session:
        user_session = session.get(UserSession, access_token.token)

        if user_session and user_session.user:
            user_info = user_session.user.serialize()
        else:
            user_info = {"error": "User not found"}

    return {
        "authenticated": True,
        "token_type": "Bearer",
        "scopes": access_token.scopes,
        "client_id": access_token.client_id,
        "user": user_info,
    }


def get_user_scopes() -> list[str]:
    """Get the current user's MCP tool scopes."""
    user_info = get_current_user()
    if not user_info.get("authenticated"):
        return []
    return user_info.get("user", {}).get("scopes", ["read"])


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request):
    """Health check endpoint that verifies all dependencies are accessible."""
    from sqlalchemy import text

    checks = {"mcp_oauth": "enabled"}
    all_healthy = True

    # Check database connection
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "healthy"
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        checks["database"] = "unhealthy"
        all_healthy = False

    # Check Qdrant connection
    try:
        from memory.common.qdrant import get_qdrant_client

        client = get_qdrant_client()
        client.get_collections()
        checks["qdrant"] = "healthy"
    except Exception as e:
        logger.error(f"Qdrant health check failed: {e}")
        checks["qdrant"] = "unhealthy"
        all_healthy = False

    checks["status"] = "healthy" if all_healthy else "degraded"
    status_code = 200 if all_healthy else 503
    return JSONResponse(checks, status_code=status_code)


# Inject auth provider into subservers that need it
set_schedule_auth(get_current_user)

# Mount all subservers onto the main MCP server
# Tools will be prefixed with their server name (e.g., core_search_knowledge_base)
mcp.mount(core_mcp, prefix="core")
mcp.mount(discord_mcp, prefix="discord")
mcp.mount(email_mcp, prefix="email")
mcp.mount(github_mcp, prefix="github")
mcp.mount(organizer_mcp, prefix="organizer")
mcp.mount(people_mcp, prefix="people")
mcp.mount(polling_mcp, prefix="polling")
mcp.mount(schedule_mcp, prefix="schedule")
mcp.mount(books_mcp, prefix="books")
mcp.mount(meta_mcp, prefix="meta")
