from datetime import datetime, timedelta, timezone

import pytest

from memory.agents import toolhub
from memory.agents.builtins import web_fetch
from memory.agents.spec import AgentSpec, ExternalMCP
from memory.agents.toolhub import ToolHubError, open_toolhub
from memory.common import oauth
from memory.common.db.models import MCPServer, MCPServerAssignment
from memory.common.llms.neutral import ToolCall
from memory.common.ssrf import UnsafeURLError
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


def make_server(
    # A public IP literal: validating it needs no DNS lookup.
    db,
    user,
    url="https://8.8.8.8/mcp",
    assigned=True,
    expires=None,
    token="t",
):
    server = MCPServer(
        name="ext",
        mcp_server_url=url,
        client_id="c",
        access_token=token,
        token_expires_at=expires,
    )
    db.add(server)
    db.commit()
    if assigned:
        db.add(
            MCPServerAssignment(
                mcp_server_id=server.id, entity_type="User", entity_id=user.id
            )
        )
        db.commit()
    return server


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"assigned": False}, "is not assigned to you"),
        ({"url": "http://127.0.0.1:8000/mcp"}, "is not a public URL"),
        (
            {"expires": datetime.now(timezone.utc) - timedelta(hours=1)},
            "token expired and refresh failed \\(no refresh token stored\\); re-authorize",
        ),
        ({"token": None}, "is not authorized; authorize it"),
    ],
)
async def test_external_servers_are_checked_before_connecting(
    db_session, admin_user, admin_session, kwargs, expected
):
    server = make_server(db_session, admin_user, **kwargs)
    s = spec(mcp_servers=[{"server_id": server.id, "tools": ["post"]}])

    with pytest.raises(ToolHubError, match=expected):
        async with hub(s, admin_session, ["*"]):
            pass


class FakeTool:
    def __init__(self, name):
        self.name = name
        self.description = f"{name} tool"
        self.inputSchema = {"type": "object"}


class FakeClient:
    """Stands in for fastmcp.Client over HTTP; records the token it was given."""

    tokens: list = []
    fail_with: Exception | None = None

    def __init__(self, url, auth=None):
        FakeClient.tokens.append(auth)

    async def __aenter__(self):
        if FakeClient.fail_with is not None:
            raise FakeClient.fail_with
        return self

    async def __aexit__(self, *exc):
        return False

    async def list_tools(self):
        return [FakeTool("post")]


@pytest.fixture
def fake_mcp_http(monkeypatch):
    FakeClient.tokens = []
    FakeClient.fail_with = None
    monkeypatch.setattr(toolhub, "Client", FakeClient)
    # example.com hosts would otherwise be resolved over the network.
    monkeypatch.setattr(toolhub, "validate_public_url", lambda url: None)
    monkeypatch.setattr(oauth, "validate_public_url", lambda url: None)
    return FakeClient


@pytest.mark.asyncio
async def test_expired_tokens_are_refreshed_before_connecting(
    db_session, admin_user, admin_session, fake_mcp_http, monkeypatch
):
    async def endpoints(url):
        return oauth.OAuthEndpoints(
            authorization_endpoint="https://auth.example.com/authorize",
            registration_endpoint="https://auth.example.com/register",
            token_endpoint="https://auth.example.com/token",
            redirect_uri="https://memory.example.com/callback",
        )

    sent = []

    async def post_token_request(url, data):
        sent.append((url, data))
        return {"access_token": "fresh", "refresh_token": "r2", "expires_in": 3600}

    monkeypatch.setattr(oauth, "get_endpoints", endpoints)
    monkeypatch.setattr(oauth, "post_token_request", post_token_request)
    server = make_server(
        db_session, admin_user, expires=datetime.now(timezone.utc) - timedelta(hours=1)
    )
    server.refresh_token = "r1"
    db_session.commit()
    s = spec(mcp_servers=[{"server_id": server.id, "tools": ["post"]}])

    async with hub(s, admin_session, ["*"]) as h:
        names = [t.name for t in h.tools()]

    db_session.refresh(server)
    assert names == [f"mcp_{server.id}_post"]
    assert fake_mcp_http.tokens == ["fresh"]
    assert sent == [
        (
            "https://auth.example.com/token",
            {"grant_type": "refresh_token", "refresh_token": "r1", "client_id": "c"},
        )
    ]
    assert (server.access_token, server.refresh_token) == ("fresh", "r2")


@pytest.mark.asyncio
async def test_a_rejected_token_names_the_server(
    db_session, admin_user, admin_session, fake_mcp_http
):
    fake_mcp_http.fail_with = RuntimeError("401 Unauthorized")
    server = make_server(db_session, admin_user)
    s = spec(mcp_servers=[{"server_id": server.id, "tools": ["post"]}])

    with pytest.raises(ToolHubError, match=r"MCP server ext: could not connect \(401"):
        async with hub(s, admin_session, ["*"]):
            pass


@pytest.mark.asyncio
async def test_internal_servers_are_refused_before_any_token_refresh(
    db_session, admin_user, admin_session, monkeypatch
):
    async def no_discovery(url):
        raise AssertionError("discovery must not reach an internal server")

    monkeypatch.setattr(oauth, "get_endpoints", no_discovery)
    server = make_server(
        db_session,
        admin_user,
        url="http://127.0.0.1:8000/mcp",
        expires=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    server.refresh_token = "r1"
    db_session.commit()
    s = spec(mcp_servers=[{"server_id": server.id, "tools": ["post"]}])

    with pytest.raises(ToolHubError, match="is not a public URL"):
        async with hub(s, admin_session, ["*"]):
            pass
