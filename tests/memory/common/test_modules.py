import asyncio
import json
import os
import subprocess
import sys
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from memory.api import app as app_module
from memory.api.MCP.base import mcp
from memory.api.MCP.servers import tool_server
from memory.common.modules import (
    MODULES,
    module_config_errors,
    resolve_modules,
    scopes_hidden,
    server_module,
    servers_disabled,
)

ALL = frozenset(MODULES)
DEFAULTS = {"on_by_default": True, "off_by_default": False}


@pytest.mark.parametrize(
    "enable, disable, expected",
    [
        (set(), set(), {"on_by_default"}),
        ({"off_by_default"}, set(), {"on_by_default", "off_by_default"}),
        (set(), {"on_by_default"}, set()),
        ({"off_by_default"}, {"on_by_default"}, {"off_by_default"}),
    ],
)
def test_env_applies_on_top_of_the_defaults(enable, disable, expected):
    assert resolve_modules(frozenset(enable), frozenset(disable), DEFAULTS) == expected


def test_every_module_is_off_by_default():
    assert resolve_modules(frozenset(), frozenset()) == frozenset()


@pytest.mark.parametrize(
    "enable, disable, refused",
    [
        ({"teams", "tems"}, set(), "tems"),
        (set(), {"multiuser"}, "multiuser"),
        ({"teams"}, {"teams"}, "teams"),
    ],
)
def test_bad_module_config_is_refused(enable, disable, refused):
    errors = module_config_errors(frozenset(enable), frozenset(disable))

    assert len(errors) == 1
    assert refused in errors[0]


def test_valid_module_config_has_no_errors():
    assert module_config_errors(ALL, frozenset()) == []


@pytest.mark.parametrize(
    "enabled, servers, scopes",
    [
        (
            frozenset(),
            {"teams", "projects", "containers"},
            {"teams", "teams:write", "projects", "projects:write", "claude_sessions"},
        ),
        (frozenset({"teams"}), {"containers"}, {"claude_sessions"}),
        (ALL, set(), set()),
    ],
)
def test_disabled_modules_drop_their_servers_and_scopes(enabled, servers, scopes):
    assert servers_disabled(enabled) == servers
    assert scopes_hidden(enabled) == scopes


def test_default_install_has_no_module_tools_scopes_routes_or_loops():
    code = (
        "import asyncio, json\n"
        "from memory import claude_containers\n"
        "def refuse():\n"
        "    raise AssertionError('container maintenance started')\n"
        "claude_containers.maintenance_running = refuse\n"
        "from memory.api import users\n"
        "users.check_single_user = lambda db, enabled: None\n"
        "from memory.api.app import app, lifespan\n"
        "from memory.api.MCP.base import mcp\n"
        "from memory.api.users import list_available_scopes\n"
        "async def boot():\n"
        "    async with lifespan(app):\n"
        "        pass\n"
        "asyncio.run(boot())\n"
        "print(json.dumps({\n"
        "    'tools': sorted(asyncio.run(mcp.get_tools())),\n"
        "    'scopes': [s.value for s in list_available_scopes(user=None)],\n"
        "    'routes': [getattr(r, 'path', '') for r in app.routes],\n"
        "}))\n"
    )
    # Empty rather than unset, so load_dotenv can't fill them from a local .env.
    env = {
        **os.environ,
        "ENABLE_MODULES": "",
        "DISABLE_MODULES": "",
        "PYTHONPATH": os.pathsep.join(sys.path),
    }
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    found = json.loads(out.stdout.strip().splitlines()[-1])

    assert {"core_search", "claude_session_search", "claude_session_fetch"} <= set(found["tools"])
    assert not [
        t for t in found["tools"] if t.startswith(("teams_", "projects_", "containers_"))
    ]
    assert "notes" in found["scopes"]
    assert not {
        "teams", "teams:write", "projects", "projects:write", "claude_sessions"
    } & set(found["scopes"])
    assert "/auth/me" in found["routes"]
    assert not [r for r in found["routes"] if r.startswith("/claude")]


@pytest.mark.asyncio
async def test_api_boot_refuses_when_single_user_check_fails(monkeypatch):
    def refuse(db, enabled):
        raise SystemExit("too many humans")

    monkeypatch.setattr(app_module, "check_single_user", refuse)

    with pytest.raises(SystemExit):
        async with app_module.lifespan(app_module.app):
            pass


@pytest.mark.asyncio
async def test_api_boots_when_the_database_is_not_ready(monkeypatch, caplog):
    def unreachable(db, enabled):
        raise OperationalError("SELECT", {}, Exception("connection refused"))

    monkeypatch.setattr(app_module, "check_single_user", unreachable)
    monkeypatch.setattr(app_module, "announce_setup_token", lambda: None)
    monkeypatch.setattr(app_module.settings, "ENABLED_MODULES", frozenset())
    # The MCP session manager runs once per process, and other tests start it.
    monkeypatch.setattr(app_module, "mcp_http_app", SimpleNamespace(lifespan=lambda app: nullcontext()))

    async with app_module.lifespan(app_module.app):
        pass
    assert "Could not check human users at startup" in caplog.text


@pytest.mark.parametrize(
    "tool, server",
    [
        ("teams_upsert", "teams"),
        ("containers_list_sessions", "containers"),
        ("claude_session_search", "claude"),
        ("notes_upsert", "notes"),
        ("web_search", None),
        ("mcp_3_teams_x", None),
    ],
)
def test_tool_server_is_the_longest_server_prefix(tool, server):
    assert tool_server(tool) == server


@pytest.mark.parametrize(
    "server, module",
    [
        ("teams", "teams"),
        ("projects", "teams"),
        ("containers", "claude_containers"),
        ("claude", None),
        ("notes", None),
    ],
)
def test_server_module(server, module):
    assert server_module(server) == module


def test_every_mounted_tool_resolves_to_a_server():
    tools = asyncio.run(mcp.get_tools())

    owners = {name: tool_server(name) for name in tools}
    assert None not in owners.values()
    assert {"claude", "containers", "teams", "projects"} <= set(owners.values())
    modules = {name: server_module(owner or "") for name, owner in owners.items()}
    assert {m for n, m in modules.items() if n.startswith("claude_session_")} == {None}
    assert {m for n, m in modules.items() if n.startswith("containers_")} == {
        "claude_containers"
    }
