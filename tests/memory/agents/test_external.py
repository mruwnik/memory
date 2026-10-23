import asyncio
import time
import json
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from mcp.client.auth.exceptions import OAuthFlowError
from mcp.shared.auth import OAuthMetadata, OAuthToken
from pydantic import ValidationError
from mcp.types import Tool, ToolAnnotations

from memory.agents.external import (
    SAVED_COLUMNS,
    AuthInput,
    authorize,
    load_detached,
    save,
    apply_tools,
    configure,
    finish_authorization,
)
from memory.common import mcp_oauth, settings
from memory.common.db.models import ExternalMCPServer
from memory.common.mcp_oauth import (
    UNREACHABLE,
    MCPServerError,
    ReauthRequired,
    StateError,
    check_metadata,
    describe,
    refresh_locked,
)
from tests.memory.agents.fake_mcp import (
    MANUAL_CLIENT_ID,
    MANUAL_CLIENT_SECRET,
    STATIC_TOKEN,
    add_server,
    connect_oauth,
    register_manual_client,
    reload,
)


@pytest.mark.asyncio
async def test_oauth_with_dynamic_registration_connects(db_session, regular_user, fake_mcp):
    server = await connect_oauth(db_session, regular_user, fake_mcp)

    assert server.status == "connected"
    assert server.auth_type == "oauth"
    assert server.client_source == "dcr"
    assert server.resource == fake_mcp.url
    token = server.access_token or ""
    assert token in fake_mcp.provider.access_tokens
    assert token.encode() not in (server.access_token_encrypted or b"")
    assert [(t["name"], t["read_only"]) for t in server.tools] == [
        ("lookup", True),
        ("post", False),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("fake_mcp", [("oauth-no-dcr", "http")], indirect=True)
async def test_a_server_without_registration_needs_a_manual_client(
    db_session, regular_user, fake_mcp
):
    server = add_server(db_session, regular_user, fake_mcp.url)

    with pytest.raises(MCPServerError, match="/auth/mcp/callback"):
        await configure(regular_user.id, server.id, AuthInput(type="auto"))


@pytest.mark.asyncio
@pytest.mark.parametrize("fake_mcp", [("oauth-no-dcr", "http")], indirect=True)
async def test_a_manual_client_connects(db_session, regular_user, fake_mcp):
    register_manual_client(fake_mcp.provider, mcp_oauth.redirect_uri())
    auth = AuthInput(
        type="oauth", client_id=MANUAL_CLIENT_ID, client_secret=MANUAL_CLIENT_SECRET
    )

    server = add_server(db_session, regular_user, fake_mcp.url)

    setup = await configure(regular_user.id, server.id, auth)
    params = await fake_mcp.approve(setup.authorize_url or "")
    await finish_authorization(params["state"], params["code"], regular_user.id)

    server = reload(db_session, server.id)
    assert setup.secret_sent_to == "127.0.0.1"
    assert server.status == "connected"
    assert server.client_source == "manual"
    assert server.token_endpoint_auth_method == "client_secret_post"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fake_mcp,auth",
    [
        (("bearer", "http"), AuthInput(type="headers", headers={"Authorization": f"Bearer {STATIC_TOKEN}"})),
        (("none", "http"), AuthInput(type="none")),
        (("none", "http"), AuthInput(type="auto")),
        (("none", "sse"), AuthInput(type="auto")),
    ],
    indirect=["fake_mcp"],
)
async def test_static_and_open_servers_connect_at_once(
    db_session, regular_user, fake_mcp, auth
):
    server = add_server(db_session, regular_user, fake_mcp.url, fake_mcp.transport)

    assert (await configure(regular_user.id, server.id, auth)).authorize_url is None

    server = reload(db_session, server.id)
    assert server.status == "connected"
    assert [t["name"] for t in server.tools] == ["lookup", "post"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fake_mcp", [("bearer", "http")], indirect=True)
async def test_rejected_static_credentials_say_so(db_session, regular_user, fake_mcp):
    server = add_server(db_session, regular_user, fake_mcp.url)
    auth = AuthInput(type="headers", headers={"Authorization": "Bearer wrong"})

    with pytest.raises(MCPServerError, match="rejected these credentials"):
        await configure(regular_user.id, server.id, auth)

    assert reload(db_session, server.id).status == "error"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:1/mcp",  # nothing listening
        "http://169.254.169.254/latest/meta-data/",  # always blocked
        "http://postgres:5432/",  # a stack service
    ],
)
async def test_unreachable_and_blocked_servers_get_one_generic_error(
    db_session, regular_user, url, caplog
):
    server = add_server(db_session, regular_user, url)

    with pytest.raises(MCPServerError) as raised:
        await configure(regular_user.id, server.id, AuthInput(type="auto"))

    assert str(raised.value) == UNREACHABLE
    assert reload(db_session, server.id).status_detail == UNREACHABLE


@pytest.mark.asyncio
async def test_a_server_that_isnt_mcp_gets_the_generic_error(
    db_session, regular_user, fake_mcp
):
    server = add_server(db_session, regular_user, fake_mcp.base + "/not-here")

    with pytest.raises(MCPServerError) as raised:
        await configure(regular_user.id, server.id, AuthInput(type="none"))

    assert str(raised.value) == UNREACHABLE


@pytest.mark.asyncio
async def test_callback_from_another_user_is_refused(
    db_session, regular_user, admin_user, fake_mcp
):
    server = add_server(db_session, regular_user, fake_mcp.url)
    setup = await configure(regular_user.id, server.id, AuthInput(type="auto"))
    params = await fake_mcp.approve(setup.authorize_url or "")

    with pytest.raises(StateError, match="log in to memory as the user"):
        await finish_authorization(params["state"], params["code"], admin_user.id)
    assert reload(db_session, server.id).access_token is None

    # The refused attempt doesn't burn the owner's authorization.
    assert await finish_authorization(params["state"], params["code"], regular_user.id) == (
        server.id,
        True,
    )


@pytest.mark.asyncio
async def test_state_is_single_use(db_session, regular_user, fake_mcp):
    server = add_server(db_session, regular_user, fake_mcp.url)
    setup = await configure(regular_user.id, server.id, AuthInput(type="auto"))
    params = await fake_mcp.approve(setup.authorize_url or "")
    await finish_authorization(params["state"], params["code"], regular_user.id)

    with pytest.raises(StateError, match="expired or was already used"):
        await finish_authorization(params["state"], params["code"], regular_user.id)


@pytest.mark.asyncio
@pytest.mark.transactional_db  # two real connections, so the row lock is real
async def test_concurrent_refreshes_make_one_token_request(
    db_session, regular_user, fake_mcp
):
    server = await connect_oauth(db_session, regular_user, fake_mcp)
    stale = server.access_token
    requests = []
    exchange = fake_mcp.provider.exchange_refresh_token

    async def counted(*args, **kwargs):
        requests.append(args)
        return await exchange(*args, **kwargs)

    fake_mcp.provider.exchange_refresh_token = counted

    first, second = await asyncio.gather(
        refresh_locked(server.id, stale), refresh_locked(server.id, stale)
    )

    assert first == second != stale
    assert len(requests) == 1
    server = reload(db_session, server.id)
    assert server.access_token == first
    assert server.refresh_token in fake_mcp.provider.refresh_tokens


@pytest.mark.asyncio
async def test_a_revoked_refresh_token_needs_reauthorization(
    db_session, regular_user, fake_mcp
):
    server = await connect_oauth(db_session, regular_user, fake_mcp)
    fake_mcp.provider.refresh_tokens.clear()

    with pytest.raises(ReauthRequired):
        await refresh_locked(server.id, server.access_token)

    server = reload(db_session, server.id)
    assert server.status == "needs_reauth"
    assert server.access_token is None
    assert server.refresh_token is None


@pytest.mark.asyncio
async def test_a_failing_token_endpoint_leaves_the_row_alone(
    db_session, regular_user, fake_mcp
):
    server = await connect_oauth(db_session, regular_user, fake_mcp)
    token = server.access_token
    server.token_endpoint = "http://127.0.0.1:1/token"
    db_session.commit()

    with pytest.raises(MCPServerError):
        await refresh_locked(server.id, token)

    server = reload(db_session, server.id)
    assert (server.status, server.access_token) == ("connected", token)


@pytest.mark.asyncio
async def test_no_secret_reaches_the_logs_or_serialized_row(
    db_session, regular_user, fake_mcp, caplog
):
    caplog.set_level(logging.DEBUG)
    server = await connect_oauth(db_session, regular_user, fake_mcp)
    await refresh_locked(server.id, server.access_token)
    server = reload(db_session, server.id)

    secrets = [server.access_token, server.refresh_token, server.client_secret]
    exposed = caplog.text + json.dumps(server.serialize())
    assert [s for s in secrets if s and s in exposed] == []


def tool(name, description="d", schema=None, read_only=None):
    return Tool(
        name=name,
        description=description,
        inputSchema=schema or {"type": "object"},
        annotations=ToolAnnotations(readOnlyHint=read_only) if read_only else None,
    )


def blank_server(tools=None):
    return SimpleNamespace(id=7, tools=tools or [], tools_refreshed_at=None, tools_changed_at=None)


def test_apply_tools_reports_the_diff():
    now = datetime.now(timezone.utc)
    server = blank_server()
    apply_tools(server, [tool("a"), tool("b")], now - timedelta(hours=1))

    diff = apply_tools(server, [tool("b", description="new"), tool("c")], now)

    assert diff == {"added": ["c"], "removed": ["a"], "changed": ["b"], "dropped": []}
    assert server.tools_changed_at == now
    assert [t["name"] for t in server.tools] == ["b", "c"]


@pytest.mark.parametrize(
    "tools,reason",
    [
        ([tool("big", schema={"type": "object", "description": "x" * 20_000})], "schema"),
        ([tool("x" * 70)], "name"),
        ([tool(f"t{i}") for i in range(3)], "limit"),
    ],
)
def test_apply_tools_drops_what_it_cannot_offer(monkeypatch, tools, reason):
    monkeypatch.setattr(settings, "MCP_MAX_TOOLS_PER_SERVER", 2)

    diff = apply_tools(blank_server(), tools, datetime.now(timezone.utc))

    assert len(diff["dropped"]) == 1
    assert reason in diff["dropped"][0]["reason"]


def test_apply_tools_truncates_descriptions(monkeypatch):
    monkeypatch.setattr(settings, "MCP_MAX_TOOL_DESCRIPTION_CHARS", 10)
    server = blank_server()

    apply_tools(server, [tool("a", description="y" * 50)], datetime.now(timezone.utc))

    assert server.tools[0]["description"] == "y" * 10


def test_an_unchanged_tool_list_keeps_tools_changed_at():
    then = datetime.now(timezone.utc) - timedelta(days=1)
    server = blank_server()
    apply_tools(server, [tool("a")], then)

    apply_tools(server, [tool("a")], datetime.now(timezone.utc))

    assert server.tools_changed_at == then


def metadata(**overrides):
    return OAuthMetadata.model_validate(
        {
            "issuer": "https://auth.example.com",
            "authorization_endpoint": "https://auth.example.com/authorize",
            "token_endpoint": "https://auth.example.com/token",
            "code_challenge_methods_supported": ["S256"],
        }
        | overrides
    )


@pytest.mark.parametrize(
    "overrides,issuer",
    [
        ({}, "https://evil.example.com"),
        ({"code_challenge_methods_supported": ["plain"]}, "https://auth.example.com"),
        ({"code_challenge_methods_supported": None}, "https://auth.example.com"),
    ],
)
def test_metadata_from_another_issuer_or_without_pkce_is_refused(overrides, issuer):
    with pytest.raises(OAuthFlowError):
        check_metadata(metadata(**overrides), issuer)


def test_matching_metadata_is_accepted():
    check_metadata(metadata(), "https://auth.example.com/")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url,auth,message",
    [
        ("https://memory.example.com:9000/mcp", AuthInput(), "memory itself"),
        ("ftp://mcp.example.com/", AuthInput(), "http"),
        ("http://8.8.8.8/mcp", AuthInput(), "use https"),
        (
            "https://8.8.8.8/mcp",
            AuthInput(type="headers", headers={"Cookie": "x"}),
            "'Cookie'",
        ),
        (
            "https://8.8.8.8/mcp",
            AuthInput(type="headers", headers={"X-A": "1\r\nX-B: 2"}),
            "printable ASCII",
        ),
    ],
)
async def test_urls_and_headers_no_server_may_have_are_refused(
    db_session, regular_user, monkeypatch, url, auth, message
):
    monkeypatch.setattr(settings, "SERVER_URL", "https://memory.example.com")
    server = add_server(db_session, regular_user, url)

    with pytest.raises(MCPServerError, match=message):
        await configure(regular_user.id, server.id, auth)



async def serve_drip():
    """A token endpoint that answers one byte at a time, forever."""

    async def handle(reader, writer):
        await reader.read(65536)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n")
        try:
            while True:
                writer.write(b"x")
                await writer.drain()
                await asyncio.sleep(0.1)
        except (ConnectionError, asyncio.CancelledError):
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


@pytest.mark.asyncio
async def test_a_dripping_token_endpoint_hits_the_deadline(
    db_session, regular_user, fake_mcp, monkeypatch
):
    server = await connect_oauth(db_session, regular_user, fake_mcp)
    token = server.access_token
    monkeypatch.setattr(mcp_oauth, "TOKEN_DEADLINE_SEC", 0.5)
    drip, port = await serve_drip()
    server.token_endpoint = f"http://127.0.0.1:{port}/token"
    db_session.commit()

    started = time.monotonic()
    async with drip:
        with pytest.raises(MCPServerError, match=UNREACHABLE):
            await refresh_locked(server.id, token)

    assert time.monotonic() - started < 3
    assert reload(db_session, server.id).access_token == token


@pytest.mark.asyncio
async def test_only_the_owner_can_configure_or_authorize(
    db_session, regular_user, admin_user, fake_mcp
):
    server = await connect_oauth(db_session, regular_user, fake_mcp)

    with pytest.raises(MCPServerError, match="unknown MCP server"):
        await configure(admin_user.id, server.id, AuthInput(type="auto"))
    with pytest.raises(MCPServerError, match="unknown MCP server"):
        await authorize(admin_user.id, server.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "iss,required",
    [("https://evil.example.com/", False), (None, True)],
)
async def test_authorization_responses_from_another_issuer_are_refused(
    db_session, regular_user, fake_mcp, iss, required
):
    server = add_server(db_session, regular_user, fake_mcp.url)
    setup = await configure(regular_user.id, server.id, AuthInput(type="auto"))
    reload(db_session, server.id).iss_parameter_supported = required
    db_session.commit()
    params = await fake_mcp.approve(setup.authorize_url or "")

    with pytest.raises(StateError, match="unexpected server"):
        await finish_authorization(params["state"], params["code"], regular_user.id, iss)


@pytest.mark.asyncio
async def test_the_issuer_named_in_the_response_is_accepted(
    db_session, regular_user, fake_mcp
):
    server = add_server(db_session, regular_user, fake_mcp.url)
    setup = await configure(regular_user.id, server.id, AuthInput(type="auto"))
    params = await fake_mcp.approve(setup.authorize_url or "")

    await finish_authorization(
        params["state"], params["code"], regular_user.id, fake_mcp.base + "/"
    )

    assert reload(db_session, server.id).status == "connected"


def test_saving_does_not_resurrect_a_deleted_server(db_session, regular_user):
    server = add_server(db_session, regular_user, "https://mcp.example.com/mcp")
    detached = load_detached(server.id)
    db_session.delete(server)
    db_session.commit()

    save(detached, SAVED_COLUMNS)

    assert db_session.query(ExternalMCPServer).count() == 0


def test_validation_errors_are_logged_without_their_input():
    with pytest.raises(ValidationError) as raised:
        OAuthToken.model_validate({"access_token": ["leaked-SECRET"]})

    assert "leaked-SECRET" not in describe(raised.value)
