# pyright: reportFunctionMemberAccess=false
"""The agent_server_* MCP tools and the OAuth callback route."""

import pytest
from starlette.requests import Request

from memory.agents.external import fail, load_detached
from memory.api import mcp_servers
from memory.api.MCP.servers import agent
from memory.common import settings
from memory.common.db.models import ExternalMCPServer
from memory.common.mcp_oauth import MCPServerError, refresh_locked
from tests.conftest import mcp_auth_context
from tests.memory.agents.fake_mcp import add_server, connect_oauth, reload

SPEC = {"system_prompt": "x", "model": "anthropic/m", "reasoning": "high"}


def callback_request(query: str, session_token: str | None) -> Request:
    cookie = f"{settings.SESSION_COOKIE_NAME}={session_token}" if session_token else ""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/auth/mcp/callback",
        "headers": [(b"cookie", cookie.encode())],
        "query_string": query.encode(),
    }
    return Request(scope)


@pytest.mark.asyncio
@pytest.mark.parametrize("fake_mcp", [("none", "http")], indirect=True)
async def test_an_open_server_connects_and_appears_in_the_catalog(
    db_session, regular_user, user_session, fake_mcp
):
    with mcp_auth_context(user_session.id):
        result = await agent.server_upsert.fn(name="tools", url=fake_mcp.url)
        catalog = await agent.catalog.fn()

    assert result["authorize_url"] is None
    assert result["server"]["status"] == "connected"
    assert [
        (s["name"], [t["name"] for t in s["tools"]]) for s in catalog["mcp_servers"]
    ] == [("tools", ["lookup", "post"])]


@pytest.mark.asyncio
async def test_oauth_servers_connect_through_the_callback(
    db_session, regular_user, user_session, fake_mcp
):
    with mcp_auth_context(user_session.id):
        result = await agent.server_upsert.fn(name="linear", url=fake_mcp.url)
    params = await fake_mcp.approve(result["authorize_url"])

    response = await mcp_servers.mcp_oauth_callback(
        callback_request(f"code={params['code']}&state={params['state']}", user_session.id)
    )

    server_id = result["server"]["id"]
    assert response.status_code == 303
    assert response.headers["location"] == f"/ui/agents/servers/{server_id}?connected=1"
    assert reload(db_session, server_id).status == "connected"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token,query,status",
    [
        (None, "code=c&state=s", 401),  # logged out: log in, then reload
        ("other", "code=c&state=s", 403),  # someone else's flow
        ("owner", "code=c&state=gone", 400),  # expired or replayed
        ("owner", "error=access_denied", 400),
    ],
)
async def test_the_callback_refuses_what_it_cannot_complete(
    db_session, regular_user, user_session, admin_session, fake_mcp, token, query, status
):
    with mcp_auth_context(user_session.id):
        result = await agent.server_upsert.fn(name="linear", url=fake_mcp.url)
    params = await fake_mcp.approve(result["authorize_url"])
    sessions = {"owner": user_session.id, "other": admin_session.id, None: None}
    query = query.replace("state=s", f"state={params['state']}")

    response = await mcp_servers.mcp_oauth_callback(callback_request(query, sessions[token]))

    assert response.status_code == status
    assert reload(db_session, result["server"]["id"]).access_token is None


@pytest.mark.asyncio
async def test_disconnect_revokes_and_clears_the_tokens(
    db_session, regular_user, user_session, fake_mcp
):
    server = await connect_oauth(db_session, regular_user, fake_mcp)

    with mcp_auth_context(user_session.id):
        result = await agent.server_disconnect.fn(server_id=server.id)

    assert (result["status"], result["has_token"]) == ("pending_auth", False)
    assert fake_mcp.provider.access_tokens == {}
    assert fake_mcp.provider.refresh_tokens == {}


@pytest.mark.asyncio
async def test_editing_without_auth_keeps_the_credentials(
    db_session, regular_user, user_session, fake_mcp
):
    server = await connect_oauth(db_session, regular_user, fake_mcp)

    with mcp_auth_context(user_session.id):
        result = await agent.server_upsert.fn(
            name="renamed", url=server.url, transport=server.transport, server_id=server.id
        )

    kept = reload(db_session, server.id)
    assert (result["server"]["name"], result["server"]["status"]) == ("renamed", "connected")
    assert (kept.access_token, kept.refresh_token) == (server.access_token, server.refresh_token)


@pytest.mark.asyncio
async def test_refresh_tools_reports_the_diff(
    db_session, regular_user, user_session, fake_mcp
):
    server = await connect_oauth(db_session, regular_user, fake_mcp)
    server.tools = [t for t in server.tools if t["name"] == "lookup"]
    db_session.commit()

    with mcp_auth_context(user_session.id):
        result = await agent.server_refresh_tools.fn(server_id=server.id)

    assert result["diff"]["added"] == ["post"]
    assert [t["name"] for t in result["server"]["tools"]] == ["lookup", "post"]


@pytest.mark.asyncio
async def test_a_server_in_use_cannot_be_deleted(
    db_session, regular_user, user_session, fake_providers
):
    server = add_server(db_session, regular_user, "https://mcp.example.com/mcp")
    spec = SPEC | {"mcp_servers": [{"server_id": server.id, "tools": ["lookup"]}]}

    with mcp_auth_context(user_session.id):
        await agent.upsert.fn(name="digest", spec=spec)
        with pytest.raises(ValueError, match="used by digest"):
            await agent.server_delete.fn(server_id=server.id)
        await agent.delete.fn(definition_id=(await agent.list_all.fn())["definitions"][0]["id"])
        await agent.server_delete.fn(server_id=server.id)

    assert db_session.query(ExternalMCPServer).count() == 0


@pytest.mark.asyncio
async def test_definitions_cannot_use_another_users_server(
    db_session, admin_user, user_session, fake_providers
):
    server = add_server(db_session, admin_user, "https://mcp.example.com/mcp")
    spec = SPEC | {"mcp_servers": [{"server_id": server.id, "tools": ["lookup"]}]}

    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match=f"MCP server {server.id} is not yours"):
            await agent.upsert.fn(name="digest", spec=spec)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,kwargs",
    [
        ("server_authorize", {}),
        ("server_refresh_tools", {}),
        ("server_disconnect", {}),
        ("server_delete", {}),
        ("server_upsert", {"name": "x", "url": "https://mcp.example.com/mcp"}),
    ],
)
async def test_another_users_server_is_unknown(
    db_session, admin_user, user_session, tool, kwargs
):
    server = add_server(db_session, admin_user, "https://mcp.example.com/mcp")

    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match="unknown MCP server"):
            await getattr(agent, tool).fn(server_id=server.id, **kwargs)


@pytest.mark.asyncio
async def test_the_per_user_server_cap_holds(
    db_session, regular_user, user_session, monkeypatch
):
    monkeypatch.setattr(settings, "MCP_MAX_SERVERS_PER_USER", 1)
    add_server(db_session, regular_user, "https://mcp.example.com/mcp")

    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match="MCP_MAX_SERVERS_PER_USER"):
            await agent.server_upsert.fn(name="second", url="https://mcp.example.com/b")


@pytest.mark.asyncio
async def test_server_changes_are_rate_limited(
    db_session, regular_user, user_session, monkeypatch
):
    monkeypatch.setattr(settings, "API_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "MCP_SERVER_CHANGES_PER_HOUR", 1)
    server = add_server(db_session, regular_user, "https://mcp.example.com/mcp")

    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match="isn.t set up for OAuth"):
            await agent.server_authorize.fn(server_id=server.id)
        with pytest.raises(ValueError, match="too many"):
            await agent.server_authorize.fn(server_id=server.id)


@pytest.mark.asyncio
async def test_a_failed_setup_returns_the_row_with_the_generic_error(
    db_session, regular_user, user_session
):
    with mcp_auth_context(user_session.id):
        result = await agent.server_upsert.fn(name="lan", url="http://127.0.0.1:1/mcp")

    assert result["error"] == "couldn't reach an MCP server at that URL"
    assert result["server"]["status"] == "error"


@pytest.mark.asyncio
async def test_definitions_flag_tools_that_changed_since_they_were_saved(
    db_session, regular_user, user_session, fake_providers, fake_mcp
):
    server = await connect_oauth(db_session, regular_user, fake_mcp)
    spec = SPEC | {"mcp_servers": [{"server_id": server.id, "tools": ["lookup", "post"]}]}
    with mcp_auth_context(user_session.id):
        saved = await agent.upsert.fn(name="digest", spec=spec)
    changed_at = {"lookup": "2000-01-01T00:00:00+00:00", "post": "2999-01-01T00:00:00+00:00"}
    server.tools = [t | {"changed_at": changed_at[t["name"]]} for t in server.tools]
    db_session.commit()

    with mcp_auth_context(user_session.id):
        fetched = await agent.fetch.fn(definition_id=saved["id"])
        listed = (await agent.list_all.fn())["definitions"]

    assert fetched["changed_tools"] == [f"mcp_{server.id}_post"]
    assert listed[0]["changed_tools"] == [f"mcp_{server.id}_post"]


@pytest.mark.asyncio
async def test_trigger_warnings_name_external_tools_that_write(
    db_session, regular_user, user_session, fake_mcp
):
    server = await connect_oauth(db_session, regular_user, fake_mcp)
    spec = agent.parse_spec(
        SPEC | {"mcp_servers": [{"server_id": server.id, "tools": ["lookup", "post"]}]}
    )

    with mcp_auth_context(user_session.id):
        [warning] = await agent.exposure_warnings(spec)

    assert f"mcp_{server.id}_post" in warning
    assert f"mcp_{server.id}_lookup" not in warning


@pytest.mark.asyncio
async def test_a_token_rotated_during_a_tool_refresh_survives_it(
    db_session, regular_user, user_session, fake_mcp, monkeypatch
):
    server = await connect_oauth(db_session, regular_user, fake_mcp)
    list_tools = agent.refresh_tool_cache

    async def rotate_meanwhile(snapshot):
        diff = await list_tools(snapshot)
        # A run refreshes the token before this refresh writes its result back.
        await refresh_locked(server.id, snapshot.access_token)
        return diff

    monkeypatch.setattr(agent, "refresh_tool_cache", rotate_meanwhile)

    with mcp_auth_context(user_session.id):
        await agent.server_refresh_tools.fn(server_id=server.id)

    rotated = reload(db_session, server.id)
    assert rotated.refresh_token in fake_mcp.provider.refresh_tokens
    assert rotated.access_token in fake_mcp.provider.access_tokens


@pytest.mark.asyncio
async def test_a_failed_setup_leaves_rotated_tokens_alone(
    db_session, regular_user, fake_mcp
):
    server = await connect_oauth(db_session, regular_user, fake_mcp)
    snapshot = load_detached(server.id)
    await refresh_locked(server.id, server.access_token)

    fail(snapshot, MCPServerError("boom"))

    rotated = reload(db_session, server.id)
    assert rotated.status == "error"
    assert rotated.refresh_token in fake_mcp.provider.refresh_tokens


@pytest.mark.asyncio
async def test_invalid_auth_errors_do_not_echo_their_values(
    db_session, regular_user, user_session
):
    auth = {"type": "headers", "client_secrt": "leaked-SECRET"}

    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError) as raised:
            await agent.server_upsert.fn(name="x", url="https://mcp.example.com/mcp", auth=auth)

    assert "leaked-SECRET" not in str(raised.value)
    assert "client_secrt" in str(raised.value)
