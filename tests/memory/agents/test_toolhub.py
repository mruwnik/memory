from datetime import datetime, timedelta, timezone

import pytest

from memory.agents import toolhub
from memory.agents.builtins import web_fetch
from memory.agents.spec import AgentSpec, ExternalMCP
from memory.agents.toolhub import ToolHubError, open_toolhub
from memory.common.llms.neutral import ToolCall
from memory.common.ssrf import UnsafeURLError
from tests.memory.agents.fake_mcp import add_server, connect_oauth, reload
from tests.memory.agents.fakes import POOR, RICH


def spec(**overrides):
    return AgentSpec.model_validate({"system_prompt": "x"} | overrides)


def bypassed(**overrides):
    """A spec that skipped validation, e.g. one stored before a rule existed."""
    return AgentSpec.model_construct(**(spec().model_dump() | overrides))


def hub(spec, session, scopes, caps=RICH.capabilities):
    return open_toolhub(
        spec=spec,
        user_id=session.user_id,
        scopes=scopes,
        session_token=session.id,
        capabilities=caps,
    )


@pytest.mark.asyncio
async def test_memory_tools_are_listed_sorted_and_callable(db_session, admin_session):
    tools = ["meta_get_user", "meta_get_current_time"]
    async with hub(spec(tools=tools), admin_session, ["*"]) as h:
        names = [t.name for t in h.tools()]
        result = await h.call(ToolCall(id="c1", name="meta_get_user", input={}))

    assert names == ["meta_get_current_time", "meta_get_user"]
    assert "admin@example.com" in result.text
    assert not result.is_error


@pytest.mark.asyncio
async def test_tools_invisible_to_the_user_fail_fast(db_session, user_session):
    with pytest.raises(
        ToolHubError,
        match=r"meta_get_user \(not visible to this user - missing scope\?\)",
    ):
        async with hub(spec(tools=["meta_get_user"]), user_session, ["teams"]):
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"tools": ["meta_create_one_time_key"]}, "meta_create_one_time_key"),
        ({"mcp_servers": [ExternalMCP(server_id=1, tools=["agent_run"])]}, "agent_run"),
    ],
)
async def test_denied_tools_are_refused_even_if_validation_was_bypassed(
    db_session, admin_session, overrides, expected
):
    with pytest.raises(ToolHubError, match=expected):
        async with hub(bypassed(**overrides), admin_session, ["*"]):
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "caps,native", [(RICH.capabilities, True), (POOR.capabilities, False)]
)
async def test_web_fetch_is_native_or_local(db_session, admin_session, caps, native):
    async with hub(spec(tools=["web_fetch"]), admin_session, ["*"], caps) as h:
        names = [t.name for t in h.tools()]
        result = await h.call(
            ToolCall(id="c", name="web_fetch", input={"url": "http://127.0.0.1/"})
        )

    assert names == ["web_fetch"]
    assert result.is_error
    assert ("executed by the provider" in result.text) is native


@pytest.mark.asyncio
async def test_a_failing_tool_is_an_error_result_not_an_exception(
    db_session, admin_session, monkeypatch
):
    async def explode(url):
        raise ConnectionError("connection reset")

    monkeypatch.setattr(toolhub, "web_fetch", explode)
    async with hub(
        spec(tools=["web_fetch"]), admin_session, ["*"], POOR.capabilities
    ) as h:
        result = await h.call(
            ToolCall(id="c", name="web_fetch", input={"url": "https://a"})
        )

    assert result.is_error
    assert "connection reset" in result.text


@pytest.mark.asyncio
async def test_web_search_without_native_or_backend_fails_fast(
    db_session, admin_session
):
    with pytest.raises(ToolHubError, match="web_search"):
        async with hub(
            spec(tools=["web_search"]), admin_session, ["*"], POOR.capabilities
        ):
            pass


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
    ],
)
@pytest.mark.asyncio
async def test_local_web_fetch_refuses_internal_targets(url):
    with pytest.raises(UnsafeURLError):
        await web_fetch(url)


def external_spec(server_id, tools=("lookup",)):
    return spec(mcp_servers=[{"server_id": server_id, "tools": list(tools)}])


@pytest.mark.asyncio
async def test_external_tools_are_exposed_and_called_on_the_server(
    db_session, regular_user, user_session, fake_mcp
):
    server = await connect_oauth(db_session, regular_user, fake_mcp)
    exposed = f"mcp_{server.id}_lookup"

    async with hub(external_spec(server.id), user_session, ["teams"]) as h:
        tools = {t.name: t for t in h.tools()}
        result = await h.call(ToolCall(id="c", name=exposed, input={"query": "kiwi"}))

    assert list(tools) == [exposed]
    assert tools[exposed].description == "[fake] Look something up."
    assert (result.text, result.is_error) == ("found kiwi", False)
    assert fake_mcp.calls == [("lookup", {"query": "kiwi"})]


@pytest.mark.asyncio
async def test_another_users_server_is_refused(
    db_session, regular_user, admin_session, fake_mcp
):
    server = await connect_oauth(db_session, regular_user, fake_mcp)

    with pytest.raises(ToolHubError, match=f"MCP server {server.id} is not yours"):
        async with hub(external_spec(server.id), admin_session, ["*"]):
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending_auth", "needs_reauth", "error"])
async def test_a_server_that_isnt_connected_fails_before_any_call(
    db_session, regular_user, user_session, status
):
    server = add_server(db_session, regular_user, "https://mcp.example.com/mcp")
    server.status = status
    db_session.commit()

    with pytest.raises(ToolHubError, match=f"/ui/agents/servers/{server.id}"):
        async with hub(external_spec(server.id), user_session, ["teams"]):
            pass


@pytest.mark.asyncio
async def test_a_token_about_to_expire_is_refreshed_first(
    db_session, regular_user, user_session, fake_mcp
):
    server = await connect_oauth(db_session, regular_user, fake_mcp)
    old = server.access_token
    server.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=30)
    db_session.commit()

    async with hub(external_spec(server.id), user_session, ["teams"]) as h:
        result = await h.call(
            ToolCall(id="c", name=f"mcp_{server.id}_lookup", input={"query": "x"})
        )

    assert not result.is_error
    assert reload(db_session, server.id).access_token not in (old, None)


@pytest.mark.asyncio
async def test_a_tool_the_server_no_longer_offers_fails_fast(
    db_session, regular_user, user_session, fake_mcp
):
    server = await connect_oauth(db_session, regular_user, fake_mcp)

    with pytest.raises(ToolHubError, match="gone \\(not offered by MCP server fake\\)"):
        async with hub(external_spec(server.id, ["gone"]), user_session, ["teams"]):
            pass
