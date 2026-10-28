"""External MCP servers at run time: tokens that expire or are revoked mid-run."""

import time
from datetime import datetime, timedelta, timezone

import pytest

from memory.agents import run_auth
from memory.agents.enqueue import SpecInvalid, TriggerInfo, enqueue_run
from memory.agents.runner import execute_run
from memory.agents.spec import AgentSpec
from memory.agents.external import AuthInput, configure
from memory.agents.toolhub import ToolHubError, open_toolhub
from memory.common import settings
from memory.common.db.models import AgentDefinition, AgentRun
from memory.common.llms.neutral import ToolCall
from memory.common.mcp_oauth import UNREACHABLE, MCPServerError, ReauthRequired
from tests.memory.agents.fake_mcp import (
    add_server,
    connect_oauth,
    keepalive_only_sse,
    reload,
)
from tests.memory.agents.fakes import RICH, calls, reply
from tests.memory.agents.test_runner import load, make_run


def external_spec(server_id, **extra):
    return {"mcp_servers": [{"server_id": server_id, "tools": ["lookup"]}]} | extra


def lookup(server_id, call_id="c1"):
    return ToolCall(id=call_id, name=f"mcp_{server_id}_lookup", input={"query": "kiwi"})


def hub(server, session, saved_at=None):
    return open_toolhub(
        spec=AgentSpec.model_validate({"system_prompt": "x"} | external_spec(server.id)),
        user_id=session.user_id,
        scopes=["teams"],
        session_token=session.token,
        capabilities=RICH.capabilities,
        saved_at=saved_at,
    )


def revoke_access(fake):
    fake.provider.access_tokens.clear()


def revoke_everything(fake):
    fake.provider.access_tokens.clear()
    fake.provider.refresh_tokens.clear()


@pytest.fixture
def notifications(monkeypatch):
    sent = []
    monkeypatch.setattr(
        run_auth, "notify_user_now", lambda db, user, *args, **kw: sent.append((user.id, args))
    )
    return sent


@pytest.mark.asyncio
async def test_a_token_revoked_mid_run_is_refreshed_transparently(
    db_session, regular_user, user_session, fake_mcp
):
    server = await connect_oauth(db_session, regular_user, fake_mcp)
    old = server.access_token

    async with hub(server, user_session) as h:
        revoke_access(fake_mcp)
        result = await h.call(lookup(server.id))

    assert (result.text, result.is_error) == ("found kiwi", False)
    assert reload(db_session, server.id).access_token not in (old, None)


@pytest.mark.asyncio
async def test_a_dead_refresh_token_mid_run_needs_reauthorization(
    db_session, regular_user, user_session, fake_mcp, notifications
):
    server = await connect_oauth(db_session, regular_user, fake_mcp)

    async with hub(server, user_session) as h:
        revoke_everything(fake_mcp)
        with pytest.raises(ReauthRequired, match="MCP server 'fake' needs re-authorization"):
            await h.call(lookup(server.id))

    server = reload(db_session, server.id)
    assert (server.status, server.access_token) == ("needs_reauth", None)
    assert len(notifications) == 1


@pytest.mark.asyncio
async def test_runs_fail_naming_the_server_and_notify_once(
    db_session, regular_user, fake_providers, fake_mcp, notifications
):
    server = await connect_oauth(db_session, regular_user, fake_mcp)

    async def revoke_then_call():
        revoke_everything(fake_mcp)
        return calls(("c1", f"mcp_{server.id}_lookup", {"query": "kiwi"}))

    first = make_run(db_session, regular_user, "anthropic/m", **external_spec(server.id))
    second = make_run(db_session, regular_user, "anthropic/m", **external_spec(server.id))
    fake_providers.script = [revoke_then_call, reply("never reached")]

    await execute_run(first)
    await execute_run(second)

    runs = [load(db_session, r)[0] for r in (first, second)]
    assert [r.status for r in runs] == ["failed", "failed"]
    assert all(
        "MCP server 'fake' needs re-authorization" in (r.error or "") for r in runs
    )
    assert f"/ui/agents/servers/{server.id}" in (runs[0].error or "")
    assert len(notifications) == 1


@pytest.mark.parametrize(
    "status,owned,expected",
    [
        ("needs_reauth", True, "needs re-authorization"),
        ("connected", False, "is not yours"),
    ],
)
def test_enqueue_refuses_servers_a_run_cannot_use(
    db_session, regular_user, admin_user, fake_providers, status, owned, expected
):
    owner = regular_user if owned else admin_user
    server = add_server(db_session, owner, "https://mcp.example.com/mcp")
    server.status = status
    db_session.commit()
    spec = AgentSpec.model_validate(
        {"system_prompt": "x", "model": "anthropic/m"} | external_spec(server.id)
    )

    with pytest.raises(SpecInvalid, match=expected):
        enqueue_run(
            db_session,
            user_id=regular_user.id,
            spec=spec,
            input="go",
            trigger=TriggerInfo(kind="cron"),
        )

    assert db_session.query(AgentRun).count() == 0



@pytest.mark.asyncio
async def test_a_server_that_never_finishes_connecting_hits_the_deadline(
    db_session, regular_user, user_session, loopback_allowed, monkeypatch
):
    monkeypatch.setattr(settings, "MCP_CONNECT_TIMEOUT_SEC", 0.5)
    async with keepalive_only_sse() as url:
        server = add_server(db_session, regular_user, url, transport="sse")
        server.status = "connected"
        db_session.commit()
        started = time.monotonic()

        with pytest.raises(ToolHubError, match=UNREACHABLE):
            async with hub(server, user_session):
                pass
        with pytest.raises(MCPServerError, match=UNREACHABLE):
            await configure(regular_user.id, server.id, AuthInput(type="none"))

    assert time.monotonic() - started < 5


@pytest.mark.asyncio
async def test_runs_apply_the_tool_limits_to_the_live_list(
    db_session, regular_user, user_session, fake_mcp, monkeypatch
):
    server = await connect_oauth(db_session, regular_user, fake_mcp)
    monkeypatch.setattr(settings, "MCP_MAX_TOOL_DESCRIPTION_CHARS", 4)

    async with hub(server, user_session) as h:
        [tool] = h.tools()

    assert tool.description == "[fake] Look"
    monkeypatch.setattr(settings, "MCP_MAX_TOOL_SCHEMA_BYTES", 10)
    with pytest.raises(ToolHubError, match="input schema"):
        async with hub(server, user_session):
            pass


@pytest.mark.asyncio
async def test_a_tool_changed_since_the_agent_was_saved_is_noted(
    db_session, regular_user, user_session, fake_mcp
):
    server = await connect_oauth(db_session, regular_user, fake_mcp)
    server.tools = [t | {"hash": "older"} for t in server.tools]
    db_session.commit()
    saved_at = datetime.now(timezone.utc) - timedelta(minutes=5)

    async with hub(server, user_session, saved_at=saved_at) as h:
        notices = h.notices()
    async with hub(server, user_session, saved_at=datetime.now(timezone.utc)) as h:
        later = h.notices()

    assert notices == [
        "tool lookup on MCP server 'fake' changed since this agent was saved"
    ]
    assert later == []


@pytest.mark.asyncio
async def test_a_run_records_changed_tools_as_a_notice(
    db_session, regular_user, fake_providers, fake_mcp
):
    server = await connect_oauth(db_session, regular_user, fake_mcp)
    server.tools = [t | {"hash": "older"} for t in server.tools]
    definition = AgentDefinition(
        user_id=regular_user.id,
        name="digest",
        spec={},
        updated_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db_session.add(definition)
    db_session.commit()
    run_id = make_run(db_session, regular_user, "anthropic/m", **external_spec(server.id))
    run = db_session.get(AgentRun, run_id)
    assert run is not None
    run.definition_id = definition.id
    db_session.commit()
    fake_providers.script = [reply("done")]

    await execute_run(run_id)

    _, steps = load(db_session, run_id)
    assert [s.kind for s in steps][:2] == ["user", "notice"]
    assert "tool lookup on MCP server 'fake' changed" in steps[1].content[0]["text"]
