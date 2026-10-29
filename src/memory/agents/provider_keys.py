"""Which provider key pays for a run, and checking a key with its provider.

Key values never reach a log line or an error message.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from memory.common import settings
from memory.common.db.connection import DBSession, make_session
from memory.agents.system import SystemAgent
from memory.common.db.models import (
    KeyStatus,
    LLMKeyChoice,
    LLMProviderKey,
    SourceItem,
    User,
)
from memory.common.llms.registry import split_model
from memory.common.notify import notify_user_now

logger = logging.getLogger(__name__)

CHECK_TIMEOUT_SEC = 15
CHECKS: dict[str, tuple[str, Callable[[str], dict[str, str]]]] = {
    "anthropic": (
        "https://api.anthropic.com/v1/models",
        lambda key: {"x-api-key": key, "anthropic-version": "2023-06-01"},
    ),
    "openai": (
        "https://api.openai.com/v1/models",
        lambda key: {"Authorization": f"Bearer {key}"},
    ),
}


@dataclass(frozen=True)
class Credential:
    """A key read once inside a DB session, safe to carry across threads. `spent_for`
    is who its calls are made for, recorded with their spend: on a shared system key
    that is not the key's owner."""

    id: int
    user_id: int
    provider: str
    label: str
    key: str = field(repr=False)
    spent_for: int | None


def credential(key: LLMProviderKey, spent_for: int | None) -> Credential:
    return Credential(key.id, key.user_id, key.provider, key.label, key.key, spent_for)


class KeyUnavailable(ValueError):
    """No key can pay for the run; the message says what to fix."""


def keys_page() -> str:
    return f"{settings.SERVER_URL.rstrip('/')}/ui/keys"


def resolve_key_ref(db: DBSession, user_id: int, ref: int | str) -> LLMProviderKey:
    """One of the user's keys, by id or by name."""
    query = db.query(LLMProviderKey).filter(LLMProviderKey.user_id == user_id)
    if isinstance(ref, int):
        query = query.filter(LLMProviderKey.id == ref)
    else:
        query = query.filter(LLMProviderKey.label == ref)
    key = query.one_or_none()
    if key is None:
        raise ValueError(f"unknown API key {ref!r}")
    return key


def rejected_message(key: LLMProviderKey | Credential) -> str:
    return (
        f"your {key.provider} API key '{key.label}' was rejected by {key.provider}; "
        f"replace it at {keys_page()}"
    )


def pinned_key(db: DBSession, user_id: int, key_id: int, model: str) -> LLMProviderKey:
    key = db.get(LLMProviderKey, key_id)
    if key is None or key.user_id != user_id:
        raise KeyUnavailable(
            f"API key {key_id} is not yours or was deleted; pick another key for this agent"
        )
    provider = split_model(model)[0]
    if key.provider != provider:
        raise KeyUnavailable(
            f"API key '{key.label}' is an {key.provider} key, but {model} needs an "
            f"{provider} key"
        )
    return key


def default_key(db: DBSession, user_id: int, provider: str) -> LLMProviderKey | None:
    return (
        db.query(LLMProviderKey)
        .filter(
            LLMProviderKey.user_id == user_id,
            LLMProviderKey.provider == provider,
            LLMProviderKey.is_default,
        )
        .one_or_none()
    )


def usable(key: LLMProviderKey | None) -> LLMProviderKey | None:
    return key if key is not None and key.status == KeyStatus.OK else None


def personal_key(db: DBSession, user_id: int, agent: SystemAgent) -> LLMProviderKey | None:
    """The user's first usable default key among `agent`'s providers: what their
    searches and their own items use."""
    defaults = (usable(default_key(db, user_id, p)) for p in agent.providers)
    return next((k for k in defaults if k is not None), None)


def system_key(
    db: DBSession, user_id: int | None, agent: SystemAgent
) -> LLMProviderKey | None:
    """Personal defaults first, then the shared defaults for system agents only."""
    personal = personal_key(db, user_id, agent) if user_id is not None else None
    if personal is not None:
        return personal
    for provider in agent.providers:
        key = usable(
            db.query(LLMProviderKey)
            .filter(
                LLMProviderKey.provider == provider,
                LLMProviderKey.is_system_default,
            )
            .one_or_none()
        )
        if key is not None:
            return key
    return None


def payer_id(holder: LLMKeyChoice) -> int | None:
    """Whose default key pays for a source left on default: its owner, else whoever
    registered its app or bot."""
    return holder.content_owner_id or holder.llm_registrant_id


def chosen_key(db: DBSession, holder: LLMKeyChoice) -> LLMProviderKey | None:
    """`holder`'s explicit key. Whoever chose it owns it (apply_llm_key only accepts the
    chooser's own keys), so its owner pays."""
    if holder.llm_key_id is None:
        return None
    return db.get(LLMProviderKey, holder.llm_key_id)


def ingest_key(db: DBSession, item: SourceItem, agent: SystemAgent) -> LLMProviderKey | None:
    """The key that pays for `agent` on `item`, or None when no LLM work may run on it.

    The governing source's "no LLM" choice, or its explicit key, wins. Otherwise the
    default key of whoever brought the item in: its creator, or for Slack and Discord
    items (which have none) whoever registered the app or bot. Shared system defaults
    pay when no usable personal default exists.
    """
    holder = item.llm_key_holder
    if holder is not None and holder.llm_disabled:
        return None
    key = chosen_key(db, holder) if holder is not None else None
    if key is not None:
        return usable(key) if key.provider in agent.providers else None
    return system_key(db, ingest_user(item), agent)


def ingest_user(item: SourceItem) -> int | None:
    """Who brought `item` in: its creator, else whoever registered its app or bot."""
    creator = item.resolve_creator_id()
    if creator is not None:
        return creator
    holder = item.llm_key_holder
    return (holder and holder.llm_registrant_id) or item.llm_registrant_id


def choose_key(
    db: DBSession,
    user_id: int,
    model: str,
    provider_key: int | None,
    require_ok: bool = True,
) -> LLMProviderKey:
    """The user's key that pays for a run on `model`: the pinned one, else their default
    for the model's provider. With `require_ok`, a key its provider rejected is refused."""
    if provider_key is not None:
        key = pinned_key(db, user_id, provider_key, model)
    else:
        key = default_key(db, user_id, split_model(model)[0])
    if key is None:
        raise KeyUnavailable(f"add an API key for {split_model(model)[0]} at {keys_page()}")
    if require_ok and key.status != KeyStatus.OK:
        raise KeyUnavailable(rejected_message(key))
    return key


def notify_key_rejected(key: LLMProviderKey | Credential) -> None:
    """Tell the key's owner, once, that its provider refused it."""
    with make_session() as db:
        user = db.get(User, key.user_id)
        if user is None:
            return
        notify_user_now(
            db,
            user,
            f"Your {key.provider} API key '{key.label}' was rejected",
            f"{key.provider} refused it. Agent runs and other LLM work that use it "
            "stop until you replace it.",
            keys_page(),
        )


def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=CHECK_TIMEOUT_SEC)


async def check_key(provider: str, key: str) -> None:
    """One authenticated request; ValueError unless the provider accepts the key."""
    url, headers = CHECKS[provider]
    try:
        async with http_client() as client:
            response = await client.get(url, headers=headers(key))
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("checking a %s key failed: %s", provider, type(e).__name__)
        raise ValueError(f"couldn't check the key with {provider}; try again") from None
    if response.status_code == 401:
        raise ValueError(f"{provider} rejected this key")
    # A 403 authenticated the key; it just may not list models.
    if not response.is_success and response.status_code != 403:
        logger.warning("checking a %s key: HTTP %s", provider, response.status_code)
        raise ValueError(
            f"couldn't check the key with {provider} (HTTP {response.status_code}); try again"
        )


def key_for_run(key_id: int | None) -> LLMProviderKey:
    """A run's key at start, detached, with last_used_at stamped.

    KeyUnavailable if it was deleted or rejected since the run was enqueued.
    """
    with make_session() as db:
        key = db.get(LLMProviderKey, key_id) if key_id is not None else None
        if key is None:
            raise KeyUnavailable(
                "the API key this run was set to use was deleted; pick another key for the agent"
            )
        if key.status != KeyStatus.OK:
            raise KeyUnavailable(rejected_message(key))
        key.last_used_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(key)
        db.expunge(key)
        return key


def reject_key(key_id: int, used: str) -> bool:
    """Mark the key invalid if it still holds the value `used`.

    True only for the call that moved it from ok to invalid, so its owner is
    notified once; a key replaced since the run started is left alone.
    """
    with make_session() as db:
        key = db.get(LLMProviderKey, key_id, with_for_update=True)
        if key is None or key.status != KeyStatus.OK or key.key != used:
            return False
        key.status = KeyStatus.INVALID.value
        key.status_detail = f"{key.provider} rejected it during an LLM call"
        db.commit()
        return True
