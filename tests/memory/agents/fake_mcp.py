"""A real MCP server on loopback, with real OAuth, for external-server tests."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import parse_qs, urlparse

import httpx
from fastmcp import FastMCP
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.utilities.http import find_available_port
from fastmcp.utilities.tests import run_server_async
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull
from mcp.types import ToolAnnotations

from memory.agents.external import AuthInput, configure, finish_authorization
from memory.common.db.models import ExternalMCPServer

Auth = Literal["oauth", "oauth-no-dcr", "bearer", "none"]
STATIC_TOKEN = "static-bearer-secret"
MANUAL_CLIENT_ID = "manual-client"
MANUAL_CLIENT_SECRET = "manual-client-secret"


@dataclass
class FakeMCP:
    url: str
    base: str
    auth: Auth
    transport: str
    provider: InMemoryOAuthProvider | None
    calls: list[tuple[str, dict]] = field(default_factory=list)

    async def approve(self, authorize_url: str) -> dict[str, str]:
        """Follow the authorize URL as the user would; the fake approves at once."""
        async with httpx.AsyncClient() as client:
            response = await client.get(authorize_url)
        location = response.headers["location"]
        return {k: v[0] for k, v in parse_qs(urlparse(location).query).items()}


def build_server(auth: Auth, base: str) -> tuple[FastMCP, InMemoryOAuthProvider | None]:
    provider = None
    verifier = None
    if auth in ("oauth", "oauth-no-dcr"):
        provider = InMemoryOAuthProvider(
            base_url=base,
            client_registration_options=ClientRegistrationOptions(
                enabled=auth == "oauth"
            ),
            revocation_options=RevocationOptions(enabled=True),
        )
    if auth == "bearer":
        verifier = StaticTokenVerifier(
            tokens={STATIC_TOKEN: {"client_id": "static", "scopes": []}}
        )
    return FastMCP("fake", auth=provider or verifier), provider


def register_manual_client(provider: InMemoryOAuthProvider, redirect_uri: str) -> None:
    provider.clients[MANUAL_CLIENT_ID] = OAuthClientInformationFull(
        client_id=MANUAL_CLIENT_ID,
        client_secret=MANUAL_CLIENT_SECRET,
        redirect_uris=[redirect_uri],  # type: ignore[list-item]
        token_endpoint_auth_method="client_secret_post",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
    )


@asynccontextmanager
async def run_fake_mcp(
    auth: Auth = "oauth", transport: Literal["http", "sse"] = "http"
) -> AsyncIterator[FakeMCP]:
    port = find_available_port()
    base = f"http://127.0.0.1:{port}"
    server, provider = build_server(auth, base)
    fake = FakeMCP(url="", base=base, auth=auth, transport=transport, provider=provider)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def lookup(query: str) -> str:
        """Look something up."""
        fake.calls.append(("lookup", {"query": query}))
        return f"found {query}"

    @server.tool
    def post(text: str) -> str:
        """Post a message."""
        fake.calls.append(("post", {"text": text}))
        return f"posted {text}"

    path = "/sse" if transport == "sse" else "/mcp"
    async with run_server_async(server, port=port, transport=transport, path=path) as url:
        fake.url = url
        yield fake


def add_server(db, user, url, transport="http") -> ExternalMCPServer:
    server = ExternalMCPServer(
        user_id=user.id,
        name="fake",
        url=url,
        transport=transport,
        auth_type="none",
        status="pending_auth",
    )
    db.add(server)
    db.commit()
    return server


def reload(db, server_id) -> ExternalMCPServer:
    db.expire_all()
    found = db.get(ExternalMCPServer, server_id)
    assert found is not None
    return found


async def connect_oauth(db, user, fake: FakeMCP, auth=AuthInput(type="auto")) -> ExternalMCPServer:
    """Register and authorize a server end to end, as the dashboard would."""
    server = add_server(db, user, fake.url)
    authorize_url = (await configure(user.id, server.id, auth)).authorize_url
    assert authorize_url is not None
    params = await fake.approve(authorize_url)
    await finish_authorization(params["state"], params["code"], user.id, params.get("iss"))
    return reload(db, server.id)



@asynccontextmanager
async def keepalive_only_sse() -> AsyncIterator[str]:
    """An SSE endpoint that sends keepalive comments and never its `endpoint` event."""

    async def handle(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n")
        try:
            while True:
                writer.write(b": keepalive\n\n")
                await writer.drain()
                await asyncio.sleep(0.05)
        except (ConnectionError, asyncio.CancelledError):
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    async with server:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/sse"
