"""Module flags as server settings.

A module named in ENABLE_MODULES or DISABLE_MODULES is the operator's; any other is the
admin's to switch on the settings page, else its default. The api fixes its modules when it
mounts the MCP servers (`boot_modules`), so a change there applies on restart, and records
them (`record_running`). Every other process follows that record, so workers never act on
a module the api doesn't run.
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from memory.common import settings
from memory.common.db.connection import make_session
from memory.common.db.models import HumanUser, InstanceSetting
from memory.common.instance_settings import (
    CACHE_TTL_SECONDS,
    Setting,
    boot_values,
    register,
    resolve,
)
from memory.common.modules import (
    CLAUDE_CONTAINERS,
    MODULE_DESCRIPTIONS,
    MODULE_MCP_SERVERS,
    MODULES,
    MULTI_USER,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModuleSetting(Setting):
    module: str = ""

    @property
    def env_var(self) -> str:
        return "DISABLE_MODULES" if self.module in settings.DISABLE_MODULES else "ENABLE_MODULES"

    @property
    def mcp_servers(self) -> frozenset[str]:
        return MODULE_MCP_SERVERS.get(self.module, frozenset())

    def env_value(self) -> bool | None:
        if self.module in settings.ENABLE_MODULES:
            return True
        if self.module in settings.DISABLE_MODULES:
            return False
        return None


MODULE_SETTINGS: dict[str, ModuleSetting] = {
    name: register(
        ModuleSetting(
            key=f"module_{name}",
            kind="bool",
            applies="restart",
            default=default,
            description=MODULE_DESCRIPTIONS[name],
            module=name,
        )
    )
    for name, default in MODULES.items()
}


def human_count() -> int:
    with make_session() as db:
        return db.query(HumanUser).count()


def run_problem(module: str, on: bool, humans: Callable[[], int]) -> str | None:
    """Why the api can't run with this module on (or off)."""
    if module == CLAUDE_CONTAINERS and on and not settings.ORCHESTRATOR_SOCKET:
        return "needs ORCHESTRATOR_SOCKET: add docker-compose.claude.yaml to COMPOSE_FILE"
    if module == MULTI_USER and not on and humans() > 1:
        return "can't be off while more than one human user exists"
    return None


def checked_modules(
    stored: Callable[[], dict[str, Any]], humans: Callable[[], int]
) -> tuple[frozenset[str], dict[str, str]]:
    """The modules the api boots with, and why each stored value it can't run with is ignored.

    Such a value falls back to the opposite, which boots. The env's values are left to
    require_settings and check_single_user, which refuse to boot.
    """
    on, problems = set(), {}
    for name, setting in MODULE_SETTINGS.items():
        enabled, source = resolve(setting, stored)
        if source == "ui" and (problem := run_problem(name, enabled, humans)):
            problems[setting.key] = problem
            enabled = not enabled
        if enabled:
            on.add(name)
    return frozenset(on), problems


_boot_modules: frozenset[str] | None = None


def boot_modules() -> frozenset[str]:
    """Fix this process's modules for its lifetime."""
    global _boot_modules
    if _boot_modules is None:
        modules, problems = checked_modules(boot_values, human_count)
        for key in problems:
            logger.error("Ignoring the stored value of %s: the api can't boot with it", key)
        _boot_modules = modules
    return _boot_modules


# Not a registered setting: the api's record of what it runs, which no one edits.
RUNNING_MODULES_KEY = "running_modules"


def record_running(db: Session, modules: frozenset[str]) -> None:
    db.execute(
        insert(InstanceSetting)
        .values(key=RUNNING_MODULES_KEY, value=sorted(modules))
        .on_conflict_do_update(
            index_elements=[InstanceSetting.key],
            set_={"value": sorted(modules), "updated_at": func.now()},
        )
    )
    db.commit()


def read_running() -> frozenset[str] | None:
    with make_session() as db:
        row = db.get(InstanceSetting, RUNNING_MODULES_KEY)
        recorded = row.value if row is not None else None
    return frozenset(recorded) if isinstance(recorded, list) else None


_running_lock = threading.Lock()
_running_cache: tuple[float, frozenset[str] | None] | None = None


def invalidate_running_cache() -> None:
    global _running_cache
    with _running_lock:
        _running_cache = None


def current_modules() -> frozenset[str]:
    """What the api runs, as it last recorded at startup (up to CACHE_TTL_SECONDS behind).

    Before any api has recorded it, the env and defaults only: a UI value may be one the api
    can't run with, so it isn't trusted until an api has booted with it.
    """
    global _running_cache
    with _running_lock:
        cached = _running_cache
    if cached is None or time.monotonic() - cached[0] >= CACHE_TTL_SECONDS:
        cached = (time.monotonic(), read_running())
        with _running_lock:
            _running_cache = cached
    running = cached[1]
    if running is not None:
        return running
    return frozenset(name for name, setting in MODULE_SETTINGS.items() if resolve(setting, dict)[0])


def enabled_modules() -> frozenset[str]:
    """The modules the api booted with; in a process that never booted them, the api's current ones."""
    return _boot_modules if _boot_modules is not None else current_modules()


def follow_modules() -> frozenset[str]:
    """Move this process to the api's current modules, for one that follows them without a restart.

    Called before memory.api.MCP.base is imported, its boot_modules() is then a no-op: its
    checks are the api's (a worker has no ORCHESTRATOR_SOCKET).
    """
    global _boot_modules
    _boot_modules = current_modules()
    return _boot_modules
