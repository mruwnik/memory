"""Tests for MCP server configuration and enabling/disabling."""

import asyncio
import os
import subprocess
import sys
from unittest.mock import patch

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from memory.api.MCP import base
from memory.common import module_settings, settings
from memory.common.modules import MODULES
from memory.api.MCP.servers import (
    MCPServer,
    ALL_MCP_SERVERS,
    check_disabled_servers,
    effective_disabled_servers,
    get_enabled_servers,
    get_server_instance,
    tool_server,
    unmounted_servers,
)
from memory.common.db.models import InstanceSetting
from memory.workers.tasks import agents as agent_tasks

ALL_MODULES = frozenset(MODULES)


# --- MCPServer enum tests ---


def test_all_servers_in_enum():
    """Verify all expected servers are in the enum."""
    expected = {
        "agent",
        "books",
        "claude",
        "containers",
        "core",
        "deadlines",
        "discord",
        "email",
        "forecast",
        "ingest",
        "journal",
        "meta",
        "notes",
        "organizer",
        "people",
        "polling",
        "projects",
        "reports",
        "scheduler",
        "slack",
        "teams",
    }
    actual = {s.value for s in MCPServer}
    assert actual == expected


def test_all_mcp_servers_matches_enum():
    """ALL_MCP_SERVERS should contain all enum values."""
    assert ALL_MCP_SERVERS == frozenset(s.value for s in MCPServer)


def test_enum_values_are_strings():
    """MCPServer enum values should be usable as strings."""
    assert MCPServer.CORE == "core"
    assert MCPServer.DISCORD == "discord"


# --- get_enabled_servers tests ---


@pytest.mark.parametrize(
    "disabled,modules,expected_enabled",
    [
        (frozenset(), ALL_MODULES, ALL_MCP_SERVERS),
        (frozenset({"slack"}), ALL_MODULES, ALL_MCP_SERVERS - {"slack"}),
        (frozenset({"slack", "forecast"}), ALL_MODULES, ALL_MCP_SERVERS - {"slack", "forecast"}),
        (frozenset({"core", "meta", "books"}), ALL_MODULES, ALL_MCP_SERVERS - {"core", "meta", "books"}),
        (
            frozenset({"slack"}),
            frozenset(),
            ALL_MCP_SERVERS - {"slack", "teams", "projects", "containers"},
        ),
        (frozenset(), frozenset({"teams"}), ALL_MCP_SERVERS - {"containers"}),
    ],
)
def test_unmounted_servers(monkeypatch, set_modules, disabled, modules, expected_enabled):
    """DISABLED_MCP_SERVERS and the servers of modules that are off are both unmounted."""
    monkeypatch.setattr(settings, "DISABLED_MCP_SERVERS", disabled)
    set_modules(modules)
    assert get_enabled_servers(unmounted_servers()) == expected_enabled


def test_get_enabled_servers_warns_on_unknown():
    """get_enabled_servers warns about unknown server names."""
    with patch("memory.api.MCP.servers.logger") as mock_logger:
        result = get_enabled_servers(frozenset({"slack", "unknown_server"}))
    mock_logger.warning.assert_called_once()
    assert "unknown_server" in str(mock_logger.warning.call_args)
    assert "slack" not in result


@pytest.mark.parametrize(
    "tool, server",
    [("core_search", "core"), ("people_tidbit_list", "people"), ("mcp_3_fetch", None)],
)
def test_tool_server(tool, server):
    assert tool_server(tool) == server


def test_the_env_disables_servers_over_the_ui_value(db_session, monkeypatch):
    monkeypatch.setattr(settings, "DISABLED_MCP_SERVERS", frozenset({"slack"}))
    db_session.add(InstanceSetting(key="disabled_mcp_servers", value=[]))
    db_session.commit()

    assert effective_disabled_servers() == frozenset({"slack"})


def test_effective_disabled_servers_adds_modules_that_are_off(db_session, set_modules):
    set_modules([])
    db_session.add(InstanceSetting(key="disabled_mcp_servers", value=["slack"]))
    db_session.commit()

    assert effective_disabled_servers() == frozenset({"slack", "teams", "projects", "containers"})


def test_only_the_env_and_modules_unmount_servers():
    code = "import memory.api.MCP.base as b; print(sorted(b.SUBSERVER_PREFIXES))"
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(sys.path),
        "DISABLED_MCP_SERVERS": "slack",
        # Named rather than left to the defaults, so booting reads no stored module flags.
        "ENABLE_MODULES": "",
        "DISABLE_MODULES": ",".join(MODULES),
    }
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr

    assert out.stdout.strip() == str(sorted(ALL_MCP_SERVERS - {"slack", "teams", "projects", "containers"}))


def test_check_disabled_servers_keeps_one_server():
    check_disabled_servers(ALL_MCP_SERVERS - {"core"})
    with pytest.raises(ValueError):
        check_disabled_servers(ALL_MCP_SERVERS)


# --- get_server_instance tests ---


@pytest.mark.parametrize("server", list(MCPServer))
def test_get_server_instance_returns_fastmcp(server):
    """get_server_instance returns a FastMCP instance for each server."""
    instance = get_server_instance(server)
    # All servers should have a name attribute (FastMCP instances do)
    assert hasattr(instance, "name")


def test_get_server_instance_raises_on_invalid():
    """get_server_instance raises ValueError for unknown servers."""
    with pytest.raises(ValueError, match="Unknown server"):
        get_server_instance("not_a_server")  # type: ignore


# --- parse_csv_set tests ---


@pytest.mark.parametrize(
    "env_value,default,expected",
    [
        (None, frozenset(), frozenset()),  # missing key returns default
        (None, frozenset({"x"}), frozenset({"x"})),  # missing key returns custom default
        ("a,b,c", frozenset(), frozenset({"a", "b", "c"})),  # comma-separated
        (" a , b , c ", frozenset(), frozenset({"a", "b", "c"})),  # strips whitespace
        ("SLACK,Forecast,CORE", frozenset(), frozenset({"slack", "forecast", "core"})),  # lowercases
        ("a,,b,,,c", frozenset(), frozenset({"a", "b", "c"})),  # ignores empty entries
        ("   ", frozenset(), frozenset()),  # whitespace-only returns default
        ("", frozenset(), frozenset()),  # empty string returns default
    ],
)
def test_parse_csv_set(env_value, default, expected):
    from memory.common.settings import parse_csv_set

    env = {"TEST_KEY": env_value} if env_value is not None else {}
    with patch.dict("os.environ", env, clear=True):
        result = parse_csv_set("TEST_KEY", default)
        assert result == expected


def run_follows(db_session, running):
    """The api records what it runs; the agents worker then follows it."""
    module_settings.record_running(db_session, frozenset(running))
    module_settings.invalidate_running_cache()
    agent_tasks.follow_module_flags()


def test_a_module_the_api_switched_on_is_mounted_for_the_next_task(db_session, monkeypatch, set_modules):
    # A worker that mounted while teams was off.
    set_modules([])
    fresh = FastMCP("agents-worker")
    monkeypatch.setattr(base, "mcp", fresh)
    monkeypatch.setattr(base, "SUBSERVER_PREFIXES", [])

    run_follows(db_session, ["teams"])

    tools = asyncio.run(fresh.get_tools())
    assert {"teams_upsert", "projects_fetch", "core_search"} <= set(tools)
    assert not [t for t in tools if t.startswith("containers_")]


def test_a_module_the_api_switched_off_is_refused_for_the_next_task(db_session, set_modules):
    # A worker that mounted while teams was on.
    set_modules(["teams"])

    run_follows(db_session, [])

    async def call():
        async with Client(base.mcp) as client:
            await client.call_tool("teams_fetch", {})

    with pytest.raises(ToolError, match="Access denied: teams_fetch"):
        asyncio.run(call())
    assert {"teams", "projects", "containers"} <= effective_disabled_servers()


# The api fell back to claude_containers off (no ORCHESTRATOR_SOCKET), though it is on in the settings.
def test_the_agents_worker_follows_what_the_api_runs_not_the_saved_value(db_session, monkeypatch, set_modules):
    monkeypatch.setattr(settings, "ENABLE_MODULES", frozenset())
    db_session.add(InstanceSetting(key="module_claude_containers", value=True))
    set_modules([])
    mounts = []
    monkeypatch.setattr(base.mcp, "mount", lambda server, prefix: mounts.append(prefix))
    monkeypatch.setattr(base, "SUBSERVER_PREFIXES", [s for s in base.SUBSERVER_PREFIXES if s != "containers"])

    run_follows(db_session, [])

    assert mounts == []
    assert "containers" in effective_disabled_servers()


async def nothing(*args):
    return None


def none(*args):
    return None


@pytest.mark.parametrize(
    "task, implementation, stub, args",
    [
        ("run_agent", "memory.agents.runner.execute_run", nothing, ["run-1"]),
        ("fire_trigger", "memory.agents.fire.fire_trigger", none, [1, []]),
        ("start_cron_run", "memory.agents.cron.start_cron_run", none, ["exec-1"]),
    ],
)
def test_every_agents_task_follows_the_api_first(
    db_session, monkeypatch, set_modules, task, implementation, stub, args
):
    monkeypatch.setattr(implementation, stub)
    set_modules(["teams"])
    module_settings.record_running(db_session, frozenset())
    module_settings.invalidate_running_cache()

    getattr(agent_tasks, task)(*args)

    assert module_settings.enabled_modules() == frozenset()
