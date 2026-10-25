"""Instance settings an admin can change in the UI.

Each registered key resolves as the env if the operator set it, else the admin's DB
value, else the default. Restart-bound keys are read once per process (`booted`);
live ones through `value`, which caches the DB values for CACHE_TTL_SECONDS.
"""

import logging
import threading
import time
from dataclasses import dataclass
from collections.abc import Callable, Iterable
from typing import Any, Literal

from cryptography.fernet import InvalidToken
from sqlalchemy import func, null
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from memory.common import settings
from memory.common.db.connection import make_session
from memory.common.db.models import InstanceSetting
from memory.common.db.models.secrets import decrypt_value, encrypt_value

logger = logging.getLogger(__name__)

Kind = Literal["bool", "secret", "list"]
Applies = Literal["live", "restart"]
Source = Literal["env", "ui", "default"]

CACHE_TTL_SECONDS = 30


def accept(_value: Any) -> None:
    return None


@dataclass(frozen=True)
class Setting:
    key: str
    kind: Kind
    applies: Applies
    default: Any
    description: str
    # Rejects a value the process can't run with; run on save and on every read.
    validate: Callable[[Any], None] = accept
    choices: frozenset[str] = frozenset()

    @property
    def env_var(self) -> str:
        return self.key.upper()

    def env_value(self) -> Any:
        """The operator's value, or None when the env leaves it unset."""
        value = getattr(settings, self.env_var)
        if self.kind == "bool":
            return value
        # An empty secret file or list counts as unset.
        return value or None

    def parse(self, raw: Any) -> Any:
        """A submitted value as this setting's type; ValueError when it isn't one."""
        if self.kind == "bool":
            if not isinstance(raw, bool):
                raise ValueError("must be true or false")
            return raw
        if self.kind == "secret":
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError("must be a non-empty string")
            return raw.strip()
        if not isinstance(raw, list) or not all(isinstance(v, str) for v in raw):
            raise ValueError("must be a list of names")
        value = frozenset(v.strip().lower() for v in raw)
        if unknown := value - self.choices:
            raise ValueError(f"unknown names: {', '.join(sorted(unknown))}")
        return value


REGISTRY: dict[str, Setting] = {}


def register(setting: Setting) -> Setting:
    REGISTRY[setting.key] = setting
    return setting


VOYAGE_API_KEY = register(
    Setting(
        key="voyage_api_key",
        kind="secret",
        applies="live",
        default="",
        description="Voyage AI key for embeddings and reranking",
    )
)


def search_ceiling(key: str, default: bool, description: str) -> Setting:
    return register(Setting(key=key, kind="bool", applies="live", default=default, description=description))


ENABLE_BM25_SEARCH = search_ceiling(
    "enable_bm25_search", True, "Keyword (BM25) search alongside vector search"
)
ENABLE_SEARCH_SCORING = search_ceiling(
    "enable_search_scoring", True, "Recency, popularity and title-match score boosts"
)
ENABLE_HYDE_EXPANSION = search_ceiling(
    "enable_hyde_expansion", False, "HyDE query expansion (an LLM call per search)"
)
ENABLE_QUERY_ANALYSIS = search_ceiling(
    "enable_query_analysis", True, "LLM query analysis (an LLM call per search)"
)
ENABLE_RERANKING = search_ceiling(
    "enable_reranking", True, "Voyage reranking of the fused results"
)

UNREADABLE = "can't be read; set it again"


def decode(setting: Setting, row: InstanceSetting) -> Any:
    """A row's value; ValueError (or InvalidToken) when it doesn't decrypt or no longer validates."""
    if setting.kind == "secret":
        return decrypt_value(row.value_encrypted) if row.value_encrypted else ""
    if setting.kind == "list":
        if not isinstance(row.value, list):
            raise ValueError("not a list")
        value = frozenset(row.value)
    elif not isinstance(row.value, bool):
        raise ValueError("not a bool")
    else:
        value = row.value
    setting.validate(value)
    return value


_logged_unreadable: set[str] = set()


def decode_rows(rows: Iterable[InstanceSetting]) -> tuple[dict[str, Any], set[str]]:
    """The registered rows' values, and the keys whose rows can't be read, which count as unset.

    A bad row (a restore under another SECRETS_ENCRYPTION_KEY, a value an upgrade no longer
    accepts) must not stop a boot or a read, because the UI is the only way to replace it.
    """
    values, unreadable = {}, set()
    for row in rows:
        if (setting := REGISTRY.get(row.key)) is None:
            continue
        try:
            values[row.key] = decode(setting, row)
        except (InvalidToken, ValueError) as e:
            unreadable.add(row.key)
            if row.key not in _logged_unreadable:
                _logged_unreadable.add(row.key)
                logger.error("Ignoring the stored value of %s: %s", row.key, type(e).__name__)
    return values, unreadable


def stored_values_in(db: Session) -> dict[str, Any]:
    """Every registered key's readable DB value, decrypted."""
    return decode_rows(db.query(InstanceSetting))[0]


def read_stored_values() -> dict[str, Any]:
    with make_session() as db:
        return stored_values_in(db)


_cache_lock = threading.Lock()
_cache: tuple[float, dict[str, Any]] | None = None


def cached_stored_values() -> dict[str, Any]:
    global _cache
    with _cache_lock:
        if _cache is not None and time.monotonic() - _cache[0] < CACHE_TTL_SECONDS:
            return _cache[1]
    values = read_stored_values()
    with _cache_lock:
        _cache = (time.monotonic(), values)
    return values


def invalidate_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = None


def resolve(setting: Setting, stored: Callable[[], dict[str, Any]]) -> tuple[Any, Source]:
    """The value and where it came from. `stored` is only read when the env is unset."""
    if (env := setting.env_value()) is not None:
        return env, "env"
    values = stored()
    if setting.key in values:
        return values[setting.key], "ui"
    return setting.default, "default"


def value(setting: Setting) -> Any:
    """A live setting's current value (up to CACHE_TTL_SECONDS behind another process's save)."""
    return resolve(setting, cached_stored_values)[0]


_boot_values: dict[str, Any] | None = None
_booted: dict[str, Any] = {}


def boot(stored: dict[str, Any] | None = None) -> None:
    """Read the DB values this process boots with, once. A failed query fails the boot.

    Tests pass `stored` to boot without a DB.
    """
    global _boot_values
    if _boot_values is None:
        _boot_values = read_stored_values() if stored is None else stored


def booted(setting: Setting) -> Any:
    """A restart-bound setting's value for the life of this process."""
    if setting.key not in _booted:
        boot()
        assert _boot_values is not None
        _booted[setting.key] = resolve(setting, lambda: _boot_values or {})[0]
    return _booted[setting.key]


def pending_restart(setting: Setting, stored: dict[str, Any]) -> bool:
    """Whether a restart would change the value this process runs with."""
    return setting.key in _booted and resolve(setting, lambda: stored)[0] != _booted[setting.key]


def save(db: Session, setting: Setting, new: Any, user_id: int) -> None:
    secret = setting.kind == "secret"
    fields = {
        "value": null() if secret else sorted(new) if setting.kind == "list" else new,
        "value_encrypted": encrypt_value(new) if secret else None,
        "key_hint": new[-4:] if secret else None,
        "updated_by": user_id,
        "updated_at": func.now(),
    }
    # An upsert, so two admins saving a new key at once don't collide on the primary key.
    db.execute(
        insert(InstanceSetting)
        .values(key=setting.key, **fields)
        .on_conflict_do_update(index_elements=[InstanceSetting.key], set_=fields)
    )


def clear(db: Session, setting: Setting) -> None:
    if row := db.get(InstanceSetting, setting.key):
        db.delete(row)
