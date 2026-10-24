"""Each source's choice of which provider key pays for LLM work on its items."""

from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel

from memory.agents.provider_keys import chosen_key, default_key, payer_id, usable
from memory.agents.system import SOURCE_PROVIDER
from memory.common.db.connection import DBSession
from memory.common.db.models import LLMKeyChoice, LLMProviderKey, User

LLMKeyInput = int | Literal["default", "none"]


class LLMKeyView(BaseModel):
    choice: Literal["default", "none", "key"]
    key_id: int | None
    label: str | None
    provider: str
    paid_by_you: bool
    # False when no usable key would pay, so no LLM work runs on new items.
    runs: bool


def current_choice(holder: LLMKeyChoice) -> LLMKeyInput:
    if holder.llm_disabled:
        return "none"
    return holder.llm_key_id if holder.llm_key_id is not None else "default"


def apply_llm_key(
    db: DBSession, user: User, holder: LLMKeyChoice, value: LLMKeyInput | None
) -> None:
    """Set `holder`'s choice. Only the source's payer may change it, and an explicit
    key must be theirs and for SOURCE_PROVIDER."""
    if value is None or value == current_choice(holder):
        return
    if user.id != payer_id(holder):
        raise HTTPException(
            status_code=403,
            detail="only the user whose keys pay for this source can change its LLM key",
        )
    if value in ("default", "none"):
        holder.llm_key_id, holder.llm_disabled = None, value == "none"
        return
    key = db.get(LLMProviderKey, value)
    if key is None or key.user_id != user.id:
        raise HTTPException(status_code=400, detail=f"unknown API key {value}")
    if key.provider != SOURCE_PROVIDER:
        raise HTTPException(
            status_code=400,
            detail=f"'{key.label}' is an {key.provider} key; LLM work on synced items "
            f"runs on {SOURCE_PROVIDER}",
        )
    holder.llm_key_id, holder.llm_disabled = key.id, False


def llm_key_view(db: DBSession, viewer: User, holder: LLMKeyChoice) -> LLMKeyView:
    """What pays for `holder`'s items, resolved as ingest_key resolves it; another
    user's key is never named."""
    if holder.llm_disabled:
        return LLMKeyView(
            choice="none",
            key_id=None,
            label=None,
            provider=SOURCE_PROVIDER,
            paid_by_you=False,
            runs=False,
        )
    key = chosen_key(db, holder)
    if key is not None:
        pays = usable(key) if key.provider == SOURCE_PROVIDER else None
    else:
        payer = payer_id(holder)
        pays = usable(default_key(db, payer, SOURCE_PROVIDER)) if payer is not None else None
    mine = pays is not None and pays.user_id == viewer.id
    return LLMKeyView(
        choice="key" if key is not None else "default",
        key_id=key.id if key is not None else None,
        label=pays.label if pays is not None and mine else None,
        provider=SOURCE_PROVIDER,
        paid_by_you=mine,
        runs=pays is not None,
    )
