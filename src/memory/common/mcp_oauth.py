"""OAuth for external MCP servers: discovery, client registration, both legs of
the authorization-code flow, refresh and revocation.

The SDK's `OAuthClientProvider` runs the whole flow in one coroutine that waits
for the browser callback, which can land on another API process minutes later.
So the flow is split in two (state in Redis between the legs), reusing the
SDK's spec-tracking helpers for discovery, metadata and registration.

Every request goes through `public_http_client`, so endpoints named by a
remote server's metadata are checked at dial time like the server itself.
Failures reaching or understanding a server are logged here and surface to
users as one generic message: distinguishing them would let a user probe hosts
on the instance's networks.
"""

import asyncio
import hashlib
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from urllib.parse import urlencode, urlparse

import httpx
import redis
from mcp.client.auth.exceptions import OAuthFlowError
from mcp.client.auth.oauth2 import PKCEParameters
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    create_oauth_metadata_request,
    extract_resource_metadata_from_www_auth,
    extract_scope_from_www_auth,
    get_client_metadata_scopes,
    handle_auth_metadata_response,
    handle_protected_resource_response,
    handle_registration_response,
    issuers_match,
    validate_metadata_issuer,
)
from mcp.shared.auth import OAuthClientMetadata, OAuthMetadata, OAuthToken
from mcp.shared.auth_utils import check_resource_allowed, resource_url_from_server_url
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from memory.common import settings
from memory.common.db.connection import make_session
from memory.common.db.models import ExternalMCPServer, ServerStatus
from memory.common.safe_http import public_http_client

logger = logging.getLogger(__name__)

UNREACHABLE = "couldn't reach an MCP server at that URL"
AUTHORIZATION_FAILED = "authorization with the MCP server failed; try again"
CALLBACK_PATH = "/auth/mcp/callback"
STATE_PREFIX = "mcp_oauth_state:"
TOKEN_TIMEOUT_SEC = 10.0
# A whole token request, below LOCK_TIMEOUT: a refresh holds the row lock
# and a DB connection across it, so a server dripping bytes mustn't pin them.
TOKEN_DEADLINE_SEC = 10.0
LOCK_TIMEOUT = "15s"
MAX_LOGGED_BODY = 500


class MCPServerError(ValueError):
    """Its message is safe to show the user."""


def unusable_message(server: ExternalMCPServer) -> str:
    """Why a run can't use a server that isn't connected, and where to fix it."""
    page = server_page(server.id)
    if server.status == ServerStatus.NEEDS_REAUTH:
        return f"MCP server '{server.name}' needs re-authorization: {page}"
    if server.status == ServerStatus.PENDING_AUTH:
        return f"MCP server '{server.name}' isn't authorized yet: {page}"
    return f"MCP server '{server.name}' has an error ({server.status_detail}): {page}"


class ReauthRequired(MCPServerError):
    """The server's authorization is gone; only the owner re-authorizing helps."""

    def __init__(self, server: ExternalMCPServer, transitioned: bool):
        super().__init__(
            f"MCP server '{server.name}' needs re-authorization: {server_page(server.id)}"
        )
        self.server_id = server.id
        self.user_id = server.user_id
        self.server_name = server.name
        # True only for the call that moved the row to needs_reauth: notify once.
        self.transitioned = transitioned


class StateError(ValueError):
    pass


class WrongUser(StateError):
    pass


class TokenRejected(Exception):
    """The token endpoint refused the grant: only a new authorization helps."""


def token_id(token: str) -> str:
    """A short, non-reversible correlation id for a token, for logs."""
    return hashlib.sha256(token.encode()).hexdigest()[:8]


def redirect_uri() -> str:
    return f"{settings.SERVER_URL.rstrip('/')}{CALLBACK_PATH}"


def server_page(server_id: int) -> str:
    return f"{settings.SERVER_URL.rstrip('/')}/ui/agents/servers/{server_id}"


def describe(error: BaseException | str) -> str:
    """Loggable detail; validation errors by type and location only, since
    their input values can hold tokens."""
    if isinstance(error, ValidationError):
        return str([(e["type"], e["loc"]) for e in error.errors()])
    return str(error)[:MAX_LOGGED_BODY]


def unreachable(what: str, url: str, error: BaseException | str) -> MCPServerError:
    logger.warning("%s for %s failed: %s", what, url, describe(error))
    return MCPServerError(UNREACHABLE)


@dataclass(frozen=True)
class Challenge:
    """What a server's 401 said about how to authorize."""

    resource_metadata: str | None = None
    scope: str | None = None

    @classmethod
    def from_response(cls, response: httpx.Response) -> "Challenge":
        return cls(
            resource_metadata=extract_resource_metadata_from_www_auth(response),
            scope=extract_scope_from_www_auth(response),
        )


@dataclass(frozen=True)
class AuthServer:
    issuer: str
    iss_supported: bool
    authorization_endpoint: str
    token_endpoint: str
    revocation_endpoint: str | None
    registration_endpoint: str | None
    auth_methods: tuple[str, ...]
    resource: str | None
    scopes: str | None


def origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def check_metadata(metadata: OAuthMetadata, expected_issuer: str) -> None:
    """Raise OAuthFlowError unless the metadata is the issuer's own and supports PKCE S256."""
    validate_metadata_issuer(metadata, expected_issuer)
    # The MCP authorization spec requires refusing servers that don't advertise S256.
    if "S256" not in (metadata.code_challenge_methods_supported or []):
        raise OAuthFlowError("authorization server does not support PKCE S256")


async def first_metadata(
    client: httpx.AsyncClient, urls: list[str]
) -> tuple[OAuthMetadata | None, bool]:
    """The first metadata found, and whether it promises `iss` on authorization
    responses (RFC 9207; the SDK's model drops that field)."""
    for url in urls:
        response = await client.send(create_oauth_metadata_request(url))
        keep_going, metadata = await handle_auth_metadata_response(response)
        if metadata is not None:
            raw = json.loads(response.content)
            return metadata, raw.get("authorization_response_iss_parameter_supported") is True
        if not keep_going:
            break
    return None, False


async def discover(client: httpx.AsyncClient, url: str, challenge: Challenge) -> AuthServer:
    try:
        resource_metadata = None
        for candidate in build_protected_resource_metadata_discovery_urls(
            challenge.resource_metadata, url
        ):
            response = await client.send(create_oauth_metadata_request(candidate))
            if resource_metadata := await handle_protected_resource_response(response):
                break
        if resource_metadata and not check_resource_allowed(
            resource_url_from_server_url(url), str(resource_metadata.resource)
        ):
            raise OAuthFlowError(
                f"protected resource {resource_metadata.resource} doesn't cover {url}"
            )
        auth_base = (
            str(resource_metadata.authorization_servers[0]) if resource_metadata else None
        )
        metadata, iss_supported = await first_metadata(
            client, build_oauth_authorization_server_metadata_discovery_urls(auth_base, url)
        )
        if metadata is None:
            raise OAuthFlowError("no authorization server metadata")
        check_metadata(metadata, auth_base or origin(url))
    except (httpx.HTTPError, OAuthFlowError, ValidationError, ValueError) as e:
        raise unreachable("OAuth discovery", url, e) from e
    return AuthServer(
        issuer=str(metadata.issuer),
        iss_supported=iss_supported,
        authorization_endpoint=str(metadata.authorization_endpoint),
        token_endpoint=str(metadata.token_endpoint),
        revocation_endpoint=str(metadata.revocation_endpoint or "") or None,
        registration_endpoint=str(metadata.registration_endpoint or "") or None,
        auth_methods=tuple(metadata.token_endpoint_auth_methods_supported or ()),
        resource=str(resource_metadata.resource) if resource_metadata else None,
        scopes=get_client_metadata_scopes(challenge.scope, resource_metadata, metadata)
        or None,
    )


def registration_auth_method(supported: tuple[str, ...]):
    """A confidential client where the server allows one: memory keeps the
    secret encrypted server-side, and some servers only revoke for clients
    that authenticate."""
    for method in ("client_secret_post", "client_secret_basic"):
        if method in supported:
            return method
    return "none"


async def register_client(
    client: httpx.AsyncClient, auth: AuthServer, scopes: str | None
) -> tuple[str, str | None, str]:
    """Dynamic client registration: (client_id, client_secret, token auth method)."""
    if not auth.registration_endpoint:
        raise MCPServerError(
            "this server doesn't support dynamic client registration; create an OAuth "
            f"app with redirect URI {redirect_uri()} and enter its client id and secret"
        )
    metadata = OAuthClientMetadata(
        redirect_uris=[redirect_uri()],  # type: ignore[list-item]
        token_endpoint_auth_method=registration_auth_method(auth.auth_methods),
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope=scopes,
        client_name="memory",
    )
    try:
        response = await client.post(
            auth.registration_endpoint,
            json=metadata.model_dump(by_alias=True, mode="json", exclude_none=True),
        )
        info = await handle_registration_response(response)
    except (httpx.HTTPError, OAuthFlowError, ValidationError) as e:
        raise unreachable("client registration", auth.registration_endpoint, e) from e
    if not info.client_id:
        raise unreachable("client registration", auth.registration_endpoint, "no client_id")
    method = info.token_endpoint_auth_method or (
        "client_secret_basic" if info.client_secret else "none"
    )
    return info.client_id, info.client_secret, method


def manual_auth_method(client_secret: str | None, supported: tuple[str, ...]) -> str:
    if not client_secret:
        return "none"
    # RFC 8414: when unadvertised, client_secret_basic is the default.
    return "client_secret_post" if "client_secret_post" in supported else "client_secret_basic"


def redis_client() -> redis.Redis:
    return redis.from_url(settings.REDIS_URL)


def start_authorization(server: ExternalMCPServer) -> str:
    """Mint a single-use state for this server and owner; the authorize URL."""
    if not (server.authorization_endpoint and server.client_id):
        raise MCPServerError("this server isn't set up for OAuth")
    pkce = PKCEParameters.generate()
    state = secrets.token_urlsafe(32)
    redis_client().setex(
        f"{STATE_PREFIX}{state}",
        settings.MCP_OAUTH_STATE_TTL_SEC,
        json.dumps(
            {
                "server_id": server.id,
                "user_id": server.user_id,
                "issuer": server.issuer,
                "code_verifier": pkce.code_verifier,
            }
        ),
    )
    params = {
        "response_type": "code",
        "client_id": server.client_id,
        "redirect_uri": redirect_uri(),
        "state": state,
        "code_challenge": pkce.code_challenge,
        "code_challenge_method": "S256",
    } | {k: v for k, v in (("resource", server.resource), ("scope", server.scopes)) if v}
    separator = "&" if "?" in server.authorization_endpoint else "?"
    return f"{server.authorization_endpoint}{separator}{urlencode(params)}"


def check_issuer(flow: dict[str, Any], iss: str | None, required: bool) -> None:
    """RFC 9207 mix-up defence: the response must come from the issuer we sent
    the user to, when the response names one or the issuer promised to."""
    if iss is None and not required:
        return
    if iss is None or not flow.get("issuer") or not issuers_match(iss, flow["issuer"]):
        raise StateError("the authorization response came from an unexpected server")


def take_state(state: str, user_id: int) -> dict[str, Any]:
    """Consume a flow's state, unless it belongs to another user: whoever holds
    a state value must not be able to burn someone else's authorization."""
    client, key = redis_client(), f"{STATE_PREFIX}{state}"
    raw = client.get(key)
    if raw is None:
        raise StateError("this authorization link expired or was already used")
    flow = json.loads(cast(bytes, raw))
    if flow["user_id"] != user_id:
        raise WrongUser(
            "log in to memory as the user who started this authorization, then retry it"
        )
    # Only one of two racing callbacks deletes the key.
    if not client.delete(key):
        raise StateError("this authorization link expired or was already used")
    return flow


def with_client_auth(
    server: ExternalMCPServer, data: dict[str, str]
) -> tuple[dict[str, str], httpx.Auth | None]:
    client_id = server.client_id or ""
    secret = server.client_secret
    if server.token_endpoint_auth_method == "client_secret_basic" and secret:
        return data, httpx.BasicAuth(client_id, secret)
    if server.token_endpoint_auth_method == "client_secret_post" and secret:
        return data | {"client_id": client_id, "client_secret": secret}, None
    return data | {"client_id": client_id}, None


async def token_request(server: ExternalMCPServer, data: dict[str, str]) -> OAuthToken:
    """TokenRejected when the server refuses the grant; MCPServerError when it
    can't be reached or answers nonsense (transient: the row isn't touched)."""
    endpoint = server.token_endpoint
    if not endpoint:
        raise MCPServerError("this server isn't set up for OAuth")
    body, auth = with_client_auth(
        server, data | ({"resource": server.resource} if server.resource else {})
    )
    try:
        async with (
            asyncio.timeout(TOKEN_DEADLINE_SEC),
            public_http_client(timeout=TOKEN_TIMEOUT_SEC) as client,
        ):
            response = await client.post(endpoint, data=body, auth=auth or httpx.USE_CLIENT_DEFAULT)
            await response.aread()
    except (httpx.HTTPError, TimeoutError) as e:
        raise unreachable("token request", endpoint, repr(e)) from e
    if response.status_code in (400, 401):
        logger.info(
            "token endpoint %s refused the grant: %s",
            endpoint,
            response.text[:MAX_LOGGED_BODY],
        )
        raise TokenRejected()
    if response.status_code != 200:
        raise unreachable("token request", endpoint, f"status {response.status_code}")
    try:
        return OAuthToken.model_validate_json(response.content)
    except ValidationError as e:
        raise unreachable("token request", endpoint, e) from e


def store_token(server: ExternalMCPServer, token: OAuthToken) -> None:
    server.access_token = token.access_token
    if token.refresh_token:
        server.refresh_token = token.refresh_token
    server.token_expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=token.expires_in)
        if token.expires_in
        else None
    )
    server.status = ServerStatus.CONNECTED.value
    server.status_detail = None
    logger.info(
        "stored a token for MCP server %s (id=%s)", server.id, token_id(token.access_token)
    )


async def exchange_code(server: ExternalMCPServer, code: str, code_verifier: str) -> None:
    try:
        token = await token_request(
            server,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri(),
                "code_verifier": code_verifier,
            },
        )
    except TokenRejected as e:
        raise MCPServerError(AUTHORIZATION_FAILED) from e
    store_token(server, token)


def mark_needs_reauth(server: ExternalMCPServer) -> bool:
    """Clear the tokens; True when this is the transition (so notify once)."""
    transitioned = server.status != ServerStatus.NEEDS_REAUTH.value
    server.clear_tokens()
    server.status = ServerStatus.NEEDS_REAUTH.value
    server.status_detail = "authorization expired or was revoked; re-authorize this server"
    return transitioned


def locked_row(db: Any, server_id: int) -> ExternalMCPServer | None:
    db.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
    try:
        return db.get(ExternalMCPServer, server_id, with_for_update=True)
    except OperationalError as e:
        logger.warning("locking MCP server %s failed: %s", server_id, e.orig)
        raise MCPServerError("couldn't refresh the MCP server's authorization; try again") from e


async def refresh_locked(server_id: int, stale_token: str | None) -> str:
    """A fresh access token for the server, refreshing at most once across workers.

    The row lock is held across one bounded token request: rotating refresh
    tokens mean a second, racing refresh would invalidate the first.
    """
    with make_session() as db:
        # In a thread: waiting on another worker's lock mustn't block the loop.
        server = await asyncio.to_thread(locked_row, db, server_id)
        if server is None:
            raise MCPServerError(f"unknown MCP server {server_id}")
        current = server.access_token
        if current and current != stale_token:
            return current
        refresh_token = server.refresh_token
        if not refresh_token:
            transitioned = mark_needs_reauth(server)
            db.commit()
            raise ReauthRequired(server, transitioned)
        try:
            token = await token_request(
                server, {"grant_type": "refresh_token", "refresh_token": refresh_token}
            )
        except TokenRejected:
            transitioned = mark_needs_reauth(server)
            db.commit()
            raise ReauthRequired(server, transitioned)
        store_token(server, token)
        return token.access_token


async def reject_token(server_id: int, token: str | None) -> ReauthRequired:
    """The server refused a token it just issued: needs re-authorization."""
    with make_session() as db:
        server = await asyncio.to_thread(locked_row, db, server_id)
        if server is None:
            raise MCPServerError(f"unknown MCP server {server_id}")
        # Another worker may already have replaced the token or marked the row.
        transitioned = server.access_token == token and mark_needs_reauth(server)
        return ReauthRequired(server, transitioned)


async def revoke(server: ExternalMCPServer) -> None:
    """Best-effort RFC 7009 revocation of the server's stored tokens."""
    endpoint = server.revocation_endpoint
    tokens = [
        (t, hint)
        for t, hint in ((server.refresh_token, "refresh_token"), (server.access_token, "access_token"))
        if t
    ]
    if not endpoint or not tokens:
        return
    try:
        async with (
            asyncio.timeout(TOKEN_DEADLINE_SEC),
            public_http_client(timeout=TOKEN_TIMEOUT_SEC) as client,
        ):
            for value, hint in tokens:
                body, auth = with_client_auth(server, {"token": value, "token_type_hint": hint})
                response = await client.post(
                    endpoint, data=body, auth=auth or httpx.USE_CLIENT_DEFAULT
                )
                if not response.is_success:
                    logger.info(
                        "revoking a %s at %s failed: %s %s",
                        hint,
                        endpoint,
                        response.status_code,
                        response.text[:MAX_LOGGED_BODY],
                    )
    except (httpx.HTTPError, TimeoutError) as e:
        logger.info("revoking tokens at %s failed: %r", endpoint, e)

