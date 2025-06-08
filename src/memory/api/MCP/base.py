import logging
import os
import pathlib
from typing import cast

from mcp.server.auth.handlers.authorize import AuthorizationRequest
from mcp.server.auth.handlers.token import (
    AuthorizationCodeRequest,
    RefreshTokenRequest,
    TokenRequest,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.fastmcp import FastMCP
from mcp.shared.auth import OAuthClientMetadata
from memory.common.db.models.users import User
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.templating import Jinja2Templates

from memory.api.MCP.oauth_provider import SimpleOAuthProvider
from memory.common import settings
from memory.common.db.connection import make_session
from memory.common.db.models import OAuthState

logger = logging.getLogger(__name__)


def validate_metadata(klass: type):
    orig_validate = klass.model_validate

    def validate(data: dict):
        data = dict(data)
        if "redirect_uris" in data:
            data["redirect_uris"] = [
                str(uri).replace("cursor://", "http://")
                for uri in data["redirect_uris"]
            ]
        if "redirect_uri" in data:
            data["redirect_uri"] = str(data["redirect_uri"]).replace(
                "cursor://", "http://"
            )

        return orig_validate(data)

    klass.model_validate = validate


validate_metadata(OAuthClientMetadata)
validate_metadata(AuthorizationRequest)
validate_metadata(AuthorizationCodeRequest)
validate_metadata(RefreshTokenRequest)
validate_metadata(TokenRequest)


# Setup templates
template_dir = pathlib.Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=template_dir)


oauth_provider = SimpleOAuthProvider()
auth_settings = AuthSettings(
    issuer_url=cast(AnyHttpUrl, settings.SERVER_URL),
    client_registration_options=ClientRegistrationOptions(
        enabled=True,
        valid_scopes=["read", "write"],
        default_scopes=["read"],
    ),
    required_scopes=["read", "write"],
)

mcp = FastMCP(
    "memory",
    stateless_http=True,
    auth_server_provider=oauth_provider,
    auth=auth_settings,
)


@mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
async def oauth_protected_resource(request: Request):
    """OAuth 2.0 Protected Resource Metadata."""
    logger.info("Protected resource metadata requested")
    return JSONResponse(oauth_provider.get_protected_resource_metadata())


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
        user = session.query(User).filter(User.email == form.get("email")).first()
        if not user or not user.is_valid_password(str(form.get("password", ""))):
            logger.warning("Login failed - invalid credentials")
            return login_form(request, oauth_params, "Invalid email or password")

        redirect_url = await oauth_provider.complete_authorization(oauth_params, user)
        print("redirect_url", redirect_url)
        if redirect_url.startswith("http://anysphere.cursor-retrieval"):
            redirect_url = redirect_url.replace("http://", "cursor://")
        return RedirectResponse(url=redirect_url, status_code=302)
