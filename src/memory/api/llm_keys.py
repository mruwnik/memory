"""Each source's choice of which provider key pays for LLM work on its items."""

from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel

from memory.agents.provider_keys import chosen_key, payer_id, system_key, usable
from memory.agents.system import SOURCE_PROVIDERS, SUMMARISE
from memory.common.access_control import has_admin_scope
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
    can_choose_default: bool


def current_choice(holder: LLMKeyChoice) -> LLMKeyInput:
    if holder.llm_disabled:
        return "none"
    return holder.llm_key_id if holder.llm_key_id is not None else "default"


def may_choose_default(user: User, holder: LLMKeyChoice) -> bool:
    """Default spends the payer's keys, so only they or an admin may pick it."""
    return user.id == payer_id(holder) or has_admin_scope(user)


def apply_llm_key(
    db: DBSession, user: User, holder: LLMKeyChoice, value: LLMKeyInput | None
) -> None:
    """Set `holder`'s choice. The caller already passed the holder's visibility check.
    "default" is limited by may_choose_default; an explicit key must be the caller's own,
    and the caller pays."""
    if value is None:
        return
    if value in ("default", "none"):
        if value == current_choice(holder):
            return
        if value == "default" and not may_choose_default(user, holder):
            raise HTTPException(
                status_code=403,
                detail="only the source's owner or an admin can put it back on the "
                "default key; pick one of your own keys or turn LLM work off",
            )
        holder.llm_key_id, holder.llm_disabled = None, value == "none"
        return
    # Checked before the no-op shortcut, so resending another user's hidden key id
    # fails like any unknown id.
    key = db.get(LLMProviderKey, value)
    if key is None or key.user_id != user.id:
        raise HTTPException(status_code=400, detail=f"unknown API key {value}")
    if value == current_choice(holder):
        return
    holder.llm_key_id, holder.llm_disabled = key.id, False


def llm_key_view(db: DBSession, viewer: User, holder: LLMKeyChoice) -> LLMKeyView:
    """What pays for `holder`'s items, resolved as ingest_key resolves it; another
    user's key is never named or identified."""
    if holder.llm_disabled:
        return LLMKeyView(
            choice="none",
            key_id=None,
            label=None,
            provider=SOURCE_PROVIDERS[0],
            paid_by_you=False,
            runs=False,
            can_choose_default=may_choose_default(viewer, holder),
        )
    key = chosen_key(db, holder)
    if key is not None:
        pays = usable(key) if key.provider in SOURCE_PROVIDERS else None
    else:
        payer = payer_id(holder)
        pays = system_key(db, payer, SUMMARISE)
    mine = pays is not None and pays.user_id == viewer.id
    # Which provider another user's key is on is theirs to keep, like its name.
    shown = next((k for k in (pays, key) if k is not None and k.user_id == viewer.id), None)
    return LLMKeyView(
        choice="key" if key is not None else "default",
        key_id=key.id if key is not None and key.user_id == viewer.id else None,
        label=pays.label if pays is not None and mine else None,
        provider=shown.provider if shown is not None else SOURCE_PROVIDERS[0],
        paid_by_you=mine,
        runs=pays is not None,
        can_choose_default=may_choose_default(viewer, holder),
    )
