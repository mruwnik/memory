import logging
import pathlib
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from sqlalchemy import text
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.templating import Jinja2Templates

from memory.api.MCP.oauth_provider import SimpleOAuthProvider
from memory.api.MCP.visibility_middleware import VisibilityMiddleware
from memory.api.MCP.metrics_middleware import MetricsMiddleware
from memory.api.MCP.servers import (
    MCPServer,
    get_server_instance,
    unmounted_servers,
)
from memory.api.auth import authenticate_user, origin_of, web_ui_origins
from memory.api.password_reset import sender_account
from memory.common import settings
from memory.common.db.connection import make_session, get_engine
from memory.common.db.models import OAuthState
from memory.common.module_settings import boot_modules
from memory.common.qdrant import get_qdrant_client
from memory.common.rate_limit import (
    check_rate_limit_spec,
    rate_limit_key,
    rate_limit_reached,
    remember,
    remembered,
)
from memory.common.version import db_revision, version_info

logger = logging.getLogger(__name__)
engine = get_engine()
BOOT_ID = uuid.uuid4().hex

# OAuth parameters that are safe to pass through to the login form
ALLOWED_OAUTH_PARAMS = frozenset([
    "state",
    "client_id",
    "redirect_uri",
    "response_type",
    "code_challenge",
    "code_challenge_method",
    "scope",
    "nonce",
])


# Setup templates
template_dir = pathlib.Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=template_dir)


oauth_provider = SimpleOAuthProvider()

mcp = FastMCP(
    settings.APP_NAME,
    auth=oauth_provider,
)


# Only module flags unmount a server. The UI value hides tools live
# (VisibilityMiddleware), so turning a server off reaches the agents worker too, without
# restarting anything. Module flags are fixed here, since a module's servers, routes and
# loops can only change on restart.
boot_modules()
SUBSERVER_PREFIXES = [s.value for s in MCPServer if s.value not in unmounted_servers()]


def _get_user_info_for_middleware() -> dict:
    """The caller's token and credential scopes for the visibility middleware,
    without DB lookups."""
    access_token = get_access_token()
    logger.debug(f"_get_user_info_for_middleware: access_token={access_token}")
    if not access_token:
        logger.debug("_get_user_info_for_middleware: No access token")
        return {"authenticated": False, "scopes": []}

    scopes = list(access_token.scopes or [])
    return {
        "authenticated": True,
        "scopes": scopes,
        "client_id": access_token.client_id,
        "token": access_token.token,
    }


# Add visibility-based tool filtering middleware
mcp.add_middleware(VisibilityMiddleware(_get_user_info_for_middleware))

# Add metrics middleware to record tool call timing
mcp.add_middleware(
    MetricsMiddleware(
        get_user_info=_get_user_info_for_middleware, prefixes=SUBSERVER_PREFIXES
    )
)


@mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
async def oauth_protected_resource(request: Request):
    """OAuth 2.0 Protected Resource Metadata."""
    metadata = oauth_provider.get_protected_resource_metadata()
    return JSONResponse(metadata)


# Client names are chosen by whoever registers the client.
MAX_CLIENT_NAME_CHARS = 80


@dataclass(frozen=True)
class Requester:
    """The OAuth client a login is authorizing, as the login page names it."""

    name: str
    host: str


def requester_for(state: object) -> Requester | None:
    """The client behind the authorization ``state``, unless it is this server's
    own web UI (or there is no such authorization)."""
    if not isinstance(state, str) or not state:
        return None
    with make_session() as session:
        oauth_state = session.query(OAuthState).filter(OAuthState.state == state).first()
        if oauth_state is None or origin_of(oauth_state.redirect_uri) in web_ui_origins():
            return None
        client = oauth_state.client
        name = (client.client_name if client else None) or oauth_state.client_id
        host = urlsplit(oauth_state.redirect_uri).netloc or oauth_state.redirect_uri
        return Requester(name=name[:MAX_CLIENT_NAME_CHARS], host=host)


def login_form(request: Request, form_data: dict, error: str | None = None):
    with make_session() as session:
        reset_available = sender_account(session) is not None
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "form_data": form_data,
            "error": error,
            "action": "/oauth/login",
            "reset_available": reset_available,
            "requester": requester_for(form_data.get("state")),
        },
    )


@mcp.custom_route("/oauth/login", methods=["GET"])
async def login_page(request: Request):
    """Display the login page."""
    # Only pass through whitelisted OAuth parameters to prevent injection
    form_data = {
        k: v for k, v in request.query_params.items()
        if k in ALLOWED_OAUTH_PARAMS
    }

    state = form_data.get("state")
    with make_session() as session:
        oauth_state = (
            session.query(OAuthState).filter(OAuthState.state == state).first()
        )
        if not oauth_state:
            logger.warning("OAuth login_page: state %r not found in database", state)
            return login_form(
                request, form_data, "Session expired or invalid — please try again."
            )

    return login_form(request, form_data, None)


@mcp.custom_route("/oauth/login", methods=["POST"])
async def handle_login(request: Request):
    """Handle login form submission.

    Every attempt counts against the client IP's bucket. Failed attempts
    count against the account's bucket, which bounds guessing against one
    account when the attacker rotates source IPs; while it is full, only
    addresses that logged in to the account recently
    (``LOGIN_KNOWN_ADDRESS_DAYS``) may try, so failures sent from elsewhere
    can't lock the owner out. Uses authenticate_user so a missing user still
    incurs a dummy bcrypt check, defeating timing-based email enumeration.

    The IP bucket key is derived via ``rate_limit_key`` (NOT
    ``request.client.host`` directly): the latter blindly trusts
    ``X-Forwarded-For`` whenever Uvicorn is started with
    ``--proxy-headers`` and a wildcard / overshooting
    ``FORWARDED_ALLOW_IPS``. Under that misconfiguration a remote
    attacker could rotate XFF per request to mint a fresh IP bucket
    and defeat the credential-stuffing rate-limit. ``rate_limit_key``
    honors XFF only when the immediate TCP peer is in
    ``RATE_LIMIT_TRUSTED_PROXIES``, matching the pattern used by every
    other rate-limited endpoint in the codebase (polls, etc).
    """
    form = await request.form()
    # Only pass through whitelisted OAuth parameters to prevent injection
    oauth_params = {
        key: value for key, value in form.items()
        if key in ALLOWED_OAUTH_PARAMS
    }

    email_raw = str(form.get("email") or "").strip()
    password = str(form.get("password", ""))
    # Lower-case for the bucket key only; the DB query stays case-sensitive
    # because that's the existing behaviour and CI auth code paths key on it.
    email_key = email_raw.lower()
    ip_key = rate_limit_key(request)
    failures_key = f"login:email:{email_key}"
    known_key = f"login:known:{email_key}:{ip_key}"
    ip_limited = not check_rate_limit_spec(f"login:ip:{ip_key}", settings.API_RATE_LIMIT_AUTH)
    account_limited = (
        bool(email_key)
        and rate_limit_reached(failures_key, settings.API_RATE_LIMIT_AUTH)
        and not remembered(known_key)
    )
    if ip_limited or account_limited:
        logger.warning("Login rate-limit exceeded (ip=%s, account=%s)", ip_limited, account_limited)
        return login_form(
            request,
            oauth_params,
            "Too many login attempts. Please wait a minute and try again.",
        )

    with make_session() as session:
        user = authenticate_user(email_raw, password, session)
        if not user:
            logger.warning("Login failed - invalid credentials")
            if email_key:
                check_rate_limit_spec(failures_key, settings.API_RATE_LIMIT_AUTH)
            return login_form(request, oauth_params, "Invalid email or password")
        remember(known_key, settings.LOGIN_KNOWN_ADDRESS_DAYS * 86400)

        redirect_url = await oauth_provider.complete_authorization(oauth_params, user)
        if redirect_url.startswith("http://anysphere.cursor-retrieval"):
            redirect_url = redirect_url.replace("http://", "cursor://")
        return RedirectResponse(url=redirect_url, status_code=302)


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request):
    """Dependency health plus version info.

    Unauthenticated on purpose: container healthchecks and tools/update.sh
    poll it without credentials, and it exposes only the release tag and
    migration ids, never content.
    """
    checks = {"mcp_oauth": "enabled"}
    all_healthy = True
    db_rev = None

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            db_rev = db_revision(conn)
        checks["database"] = "healthy"
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        checks["database"] = "unhealthy"
        all_healthy = False

    # Check Qdrant connection
    try:
        client = get_qdrant_client()
        client.get_collections()
        checks["qdrant"] = "healthy"
    except Exception as e:
        logger.error(f"Qdrant health check failed: {e}")
        checks["qdrant"] = "unhealthy"
        all_healthy = False

    checks |= await version_info(db_rev)
    # The restart flow waits for a new one to know the api came back.
    checks["boot_id"] = BOOT_ID
    checks["status"] = "healthy" if all_healthy else "degraded"
    status_code = 200 if all_healthy else 503
    return JSONResponse(checks, status_code=status_code)


# Mount only enabled subservers onto the main MCP server
# Tools will be prefixed with their server name (e.g., core_search_knowledge_base)
for server in MCPServer:
    if server.value in SUBSERVER_PREFIXES:
        mcp.mount(get_server_instance(server), prefix=server.value)
        logger.info(f"Mounted MCP server: {server.value}")

if disabled := unmounted_servers():
    logger.info(f"Unmounted MCP servers: {sorted(disabled)}")


def mount_followed_modules() -> None:
    """Mount the servers of modules switched on since this process mounted, after follow_modules().

    Those of a module switched off stay mounted, but leave enabled_modules(), so
    VisibilityMiddleware hides and refuses them.
    """
    for server in MCPServer:
        if server.value not in SUBSERVER_PREFIXES and server.value not in unmounted_servers():
            mcp.mount(get_server_instance(server), prefix=server.value)
            SUBSERVER_PREFIXES.append(server.value)
            logger.info(f"Mounted MCP server: {server.value}")
