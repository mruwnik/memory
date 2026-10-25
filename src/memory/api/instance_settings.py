"""Admin-only server settings: list, set and clear them, check a Voyage key, restart the api."""

import logging
import os
import signal
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from memory.api.auth import require_scope
from memory.api.MCP.servers import HIDDEN_MCP_SERVERS, check_disabled_servers
from memory.common import embedding, settings
from memory.common.celery_app import REINGEST_ALL_EMPTY_SOURCE_ITEMS, app as celery_app
from memory.common.db.connection import get_session
from memory.common.db.models import HumanUser, InstanceSetting, User
from memory.common.instance_settings import (
    CACHE_TTL_SECONDS,
    REGISTRY,
    VOYAGE_API_KEY,
    Applies,
    Kind,
    Setting,
    Source,
    UNREADABLE,
    clear,
    decode_rows,
    invalidate_cache,
    resolve,
    save,
    stored_values_in,
)
from memory.common.module_settings import (
    ModuleSetting,
    checked_modules,
    enabled_modules,
    run_problem,
)
from memory.common.modules import servers_disabled
from memory.common.scopes import SCOPE_ADMIN
from memory.common.telemetry import record_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingView(BaseModel):
    key: str
    kind: Kind
    applies: Applies
    description: str
    env_var: str | None
    source: Source
    # Set by the operator in the env, so read-only here.
    locked: bool
    set: bool
    # Never a secret's value: secrets carry only key_hint.
    value: Any = None
    key_hint: str | None = None
    choices: list[str] = []
    pending_restart: bool
    # Why the stored UI value is being ignored.
    error: str | None = None
    # Module flags only.
    module: str | None = None
    mcp_servers: list[str] = []


class SettingsView(BaseModel):
    settings: list[SettingView]
    restart_pending: bool
    # Whether POST /settings/restart can restart the api, or it must be restarted by hand.
    self_restart: bool


class SettingsPatch(BaseModel):
    # null clears the UI value, back to the default.
    values: dict[str, Any]


class KeyCheck(BaseModel):
    value: str


class KeyCheckResult(BaseModel):
    ok: bool
    voyage_status: int | None = None


def as_json(setting: Setting, value: Any) -> Any:
    return sorted(value) if setting.kind == "list" else value


def secret_hint(row: InstanceSetting | None, resolved: str, source: Source) -> str | None:
    if source == "env":
        return resolved[-4:]
    return row.key_hint if row is not None and source == "ui" else None


def setting_view(
    setting: Setting,
    row: InstanceSetting | None,
    stored: dict[str, Any],
    pending: bool,
    error: str | None,
) -> SettingView:
    resolved, source = resolve(setting, lambda: stored)
    secret = setting.kind == "secret"
    module = setting if isinstance(setting, ModuleSetting) else None
    return SettingView(
        key=setting.key,
        kind=setting.kind,
        applies=setting.applies,
        description=setting.description,
        env_var=setting.env_var,
        source=source,
        locked=source == "env",
        set=source != "default",
        value=None if secret else as_json(setting, resolved),
        key_hint=secret_hint(row, resolved, source) if secret else None,
        choices=sorted(setting.choices),
        pending_restart=pending,
        error=error,
        module=module.module if module else None,
        mcp_servers=sorted(module.mcp_servers) if module else [],
    )


def hidden_servers_problem(stored: dict[str, Any], modules: frozenset[str]) -> dict[str, str]:
    """Saving refuses this, but turning a module off from the env can still get there."""
    hidden = resolve(HIDDEN_MCP_SERVERS, lambda: stored)[0] | servers_disabled(modules)
    try:
        check_disabled_servers(hidden)
    except ValueError as e:
        return {HIDDEN_MCP_SERVERS.key: f"{e}: no MCP tools are visible"}
    return {}


def human_count_in(db: Session) -> Callable[[], int]:
    return lambda: db.query(HumanUser).count()


def settings_view(db: Session) -> SettingsView:
    rows = {row.key: row for row in db.query(InstanceSetting)}
    stored, unreadable = decode_rows(rows.values())
    # What a restart would boot with, against what this api runs.
    next_modules, problems = checked_modules(lambda: stored, human_count_in(db))
    running = enabled_modules()

    problems |= hidden_servers_problem(stored, running)

    def pending(setting: Setting) -> bool:
        return isinstance(setting, ModuleSetting) and (
            (setting.module in next_modules) != (setting.module in running)
        )

    def error(setting: Setting) -> str | None:
        return problems.get(setting.key) or (UNREADABLE if setting.key in unreadable else None)

    views = [
        setting_view(s, rows.get(s.key), stored, pending(s), error(s)) for s in REGISTRY.values()
    ]
    return SettingsView(
        settings=views,
        restart_pending=any(v.pending_restart for v in views),
        self_restart=settings.API_SELF_RESTART,
    )


def parse_change(key: str, raw: Any) -> tuple[Setting, Any]:
    setting = REGISTRY.get(key)
    if setting is None:
        raise HTTPException(status_code=400, detail=f"unknown setting {key}")
    if raw is None:
        # Allowed while the env sets the key, so a stale UI value can't come back once it's unset.
        return setting, None
    if setting.env_value() is not None:
        raise HTTPException(
            status_code=409,
            detail=f"{key} is set by the operator in the environment ({setting.env_var})",
        )
    if setting.kind == "secret" and not settings.SECRETS_ENCRYPTION_KEY:
        raise HTTPException(
            status_code=409, detail="SECRETS_ENCRYPTION_KEY must be set to store a secret"
        )
    try:
        new = setting.parse(raw)
        setting.validate(new)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"{key}: {e}")
    return setting, new


def check_boots_with(
    changes: list[tuple[Setting, Any]], stored: dict[str, Any], humans: Callable[[], int]
) -> None:
    """Refuse changes the api couldn't run with, so the boot fallbacks are only for env changes."""
    changed = {s.key: new for s, new in changes}
    after = {key: v for key, v in (stored | changed).items() if v is not None}
    for setting, _ in changes:
        if not isinstance(setting, ModuleSetting) or setting.env_value() is not None:
            continue
        on = resolve(setting, lambda: after)[0]
        if problem := run_problem(setting.module, on, humans):
            raise HTTPException(status_code=400, detail=f"{setting.key}: {problem}")
    if any(s is HIDDEN_MCP_SERVERS or isinstance(s, ModuleSetting) for s, _ in changes):
        modules = checked_modules(lambda: after, humans)[0]
        hidden = resolve(HIDDEN_MCP_SERVERS, lambda: after)[0] | servers_disabled(modules)
        try:
            check_disabled_servers(hidden)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"{HIDDEN_MCP_SERVERS.key}: {e}")


def rejected_voyage_key(key: str) -> int | None:
    status = embedding.check_key(key)
    if status is not None:
        logger.info("Voyage rejected a key: HTTP %s", status)
    return status


def audit_record(setting: Setting, old: Any, new: Any) -> dict:
    source = "ui" if old is not None else "default"
    if setting.kind == "secret":
        return {"source": source, "changed": True, "key_hint": new[-4:] if new else None}
    return {
        "source": source,
        "old": as_json(setting, old if old is not None else setting.default),
        "new": as_json(setting, new if new is not None else setting.default),
    }


@router.get("")
def get_settings(
    _user: User = require_scope(SCOPE_ADMIN), db: Session = Depends(get_session)
) -> SettingsView:
    return settings_view(db)


@router.patch("")
def update_settings(
    body: SettingsPatch,
    user: User = require_scope(SCOPE_ADMIN),
    db: Session = Depends(get_session),
) -> SettingsView:
    """Set (or with null, clear) UI values. Nothing is saved unless every change is valid."""
    if not body.values:
        raise HTTPException(status_code=400, detail="no settings to change")
    changes = [parse_change(key, raw) for key, raw in body.values.items()]
    stored = stored_values_in(db)
    check_boots_with(changes, stored, human_count_in(db))
    voyage_key = next((new for s, new in changes if s is VOYAGE_API_KEY and new), None)
    if voyage_key is not None and (status := rejected_voyage_key(voyage_key)) is not None:
        raise HTTPException(status_code=400, detail=f"Voyage rejected the key (HTTP {status})")

    records = {}
    for setting, new in changes:
        if new is None:
            clear(db, setting)
        else:
            save(db, setting, new, user.id)
        records[setting.key] = audit_record(setting, stored.get(setting.key), new)
    db.commit()
    invalidate_cache()

    logger.info("User %s changed settings: %s", user.id, ", ".join(records))
    record_event(
        "settings.update", user_id=user.id, source="settings", attributes={"changes": records}
    )
    if voyage_key is not None:
        # After the other processes' caches expire, so the workers embed with the new key.
        celery_app.send_task(REINGEST_ALL_EMPTY_SOURCE_ITEMS, countdown=CACHE_TTL_SECONDS + 5)
    return settings_view(db)


@router.post("/voyage_api_key/check")
def check_voyage_key(body: KeyCheck, _user: User = require_scope(SCOPE_ADMIN)) -> KeyCheckResult:
    """Try a key with one tiny embed, without saving it."""
    if not body.value.strip():
        raise HTTPException(status_code=400, detail="the key is empty")
    status = rejected_voyage_key(body.value.strip())
    return KeyCheckResult(ok=status is None, voyage_status=status)


def exit_api() -> None:
    """uvicorn shuts down cleanly on SIGTERM, and the container's restart policy starts it again."""
    os.kill(os.getpid(), signal.SIGTERM)


@router.post("/restart", status_code=202)
def restart_api(
    background: BackgroundTasks, user: User = require_scope(SCOPE_ADMIN)
) -> dict[str, str]:
    if not settings.API_SELF_RESTART:
        raise HTTPException(
            status_code=409,
            detail="this api can't restart itself: restart it by hand, or with tools/ops/restart.sh api",
        )
    logger.info("User %s restarted the api", user.id)
    record_event("settings.restart", user_id=user.id, source="settings")
    # A background task runs once the response has gone out.
    background.add_task(exit_api)
    return {"status": "restarting"}
