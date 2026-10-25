"""Admin-only instance settings: list, set and clear them, check a Voyage key, restart the api."""

import logging
import os
import signal
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from memory.api.auth import require_scope
from memory.common import embedding, settings
from memory.common.celery_app import REINGEST_ALL_EMPTY_SOURCE_ITEMS, app as celery_app
from memory.common.db.connection import get_session
from memory.common.db.models import InstanceSetting, User
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
    pending_restart,
    resolve,
    save,
    stored_values_in,
)
from memory.common.scopes import SCOPE_ADMIN
from memory.common.telemetry import record_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingView(BaseModel):
    key: str
    kind: Kind
    applies: Applies
    description: str
    env_var: str
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
    rows: dict[str, InstanceSetting],
    stored: dict[str, Any],
    unreadable: set[str],
) -> SettingView:
    resolved, source = resolve(setting, lambda: stored)
    secret = setting.kind == "secret"
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
        key_hint=secret_hint(rows.get(setting.key), resolved, source) if secret else None,
        choices=sorted(setting.choices),
        pending_restart=pending_restart(setting, stored),
        error=UNREADABLE if setting.key in unreadable else None,
    )


def settings_view(db: Session) -> SettingsView:
    rows = {row.key: row for row in db.query(InstanceSetting)}
    stored, unreadable = decode_rows(rows.values())
    views = [setting_view(s, rows, stored, unreadable) for s in REGISTRY.values()]
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
    voyage_key = next((new for s, new in changes if s is VOYAGE_API_KEY and new), None)
    if voyage_key is not None and (status := rejected_voyage_key(voyage_key)) is not None:
        raise HTTPException(status_code=400, detail=f"Voyage rejected the key (HTTP {status})")

    stored = stored_values_in(db)
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
