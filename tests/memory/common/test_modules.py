import json
import os
import subprocess
import sys

import pytest

from memory.api import app as app_module
from memory.common.modules import (
    MODULES,
    module_config_errors,
    resolve_modules,
    scopes_hidden,
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
        (
            frozenset({"teams"}),
            {"projects", "containers"},
            {"projects", "projects:write", "claude_sessions"},
        ),
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
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, env=env
    )
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
