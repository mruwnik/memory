"""Server settings an admin can change in the UI.

Each registered key resolves as the env if the operator set it (keys with no env var
skip this), else the admin's DB value, else the default. Live keys are read through
`value`, which caches the DB values for CACHE_TTL_SECONDS; the restart-bound module flags
read `boot_values` once per process.
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
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from memory.common import settings
from memory.common.db.connection import make_session
from memory.common.db.models import InstanceSetting
from memory.common.encryption import decrypt_value, encrypt_value

logger = logging.getLogger(__name__)

Kind = Literal["bool", "secret", "list", "int"]
Applies = Literal["live", "restart"]
Source = Literal["env", "ui", "default"]

CACHE_TTL_SECONDS = 30
UNDEFINED_TABLE = "42P01"


def accept(_value: Any) -> None:
    return None


def within(low: int, high: int) -> Callable[[Any], None]:
    def check(value: Any) -> None:
        if not low <= value <= high:
            raise ValueError(f"must be between {low} and {high}")

    return check


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
    from_env: bool = True

    @property
    def env_var(self) -> str | None:
        return self.key.upper() if self.from_env else None

    def env_value(self) -> Any:
        """The operator's value, or None when the env leaves it unset."""
        if self.env_var is None:
            return None
        value = getattr(settings, self.env_var)
        if self.kind in ("bool", "int"):
            return value
        # An empty secret file counts as unset.
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
        if self.kind == "int":
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise ValueError("must be a whole number")
            return raw
        if not isinstance(raw, list) or not all(isinstance(v, str) for v in raw):
            raise ValueError("must be a list of names")
        value = frozenset(v.strip().lower() for v in raw)
        if unknown := value - self.choices:
            raise ValueError(f"unknown names: {', '.join(sorted(unknown))}")
        return value


REGISTRY: dict[str, Setting] = {}


def register[S: Setting](setting: S) -> S:
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

NOTES_GIT_TOKEN = register(
    Setting(
        key="notes_git_token",
        kind="secret",
        applies="live",
        default="",
        description="Access token for the notes vault's https git remote",
    )
)
NOTES_SYNC_INTERVAL = register(
    Setting(
        key="notes_sync_interval",
        kind="int",
        applies="live",
        default=15 * 60,
        description="Seconds between syncs of the notes vault with its git remote",
        validate=within(*settings.NOTES_SYNC_INTERVAL_RANGE),
    )
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
    elif setting.kind == "int":
        if isinstance(row.value, bool) or not isinstance(row.value, int):
            raise ValueError("not an int")
        value = row.value
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


def boot_values() -> dict[str, Any]:
    """The DB values this process boots with, read once. Any failure but a missing table fails the boot."""
    global _boot_values
    if _boot_values is None:
        try:
            _boot_values = read_stored_values()
        except ProgrammingError as e:
            if getattr(e.orig, "pgcode", None) != UNDEFINED_TABLE:
                raise
            # Not migrated yet (a first install), so no admin can have saved a value.
            logger.warning("Could not read server settings at startup: the database isn't migrated")
            _boot_values = {}
    return _boot_values


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
