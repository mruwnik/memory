"""The logged-in user's own identity: their Person, accounts and suggested Persons."""

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from memory.api.auth import get_interactive_user
from memory.common import discord_link
from memory.common.db.connection import get_session
from memory.common.db.models import Person, User
from memory.common.identity import (
    CredentialAccount,
    accounts,
    claim_person,
    suggestions,
    unlink_account,
)
from memory.common.notify import (
    CHANNEL_PRIORITY,
    channel_problems,
    get_notification_channel,
    user_info_for,
)

router = APIRouter(prefix="/auth/me/identity", tags=["identity"])


def person_summary(person: Person) -> dict:
    return {"id": person.id, "identifier": person.identifier, "display_name": person.display_name}


def identity_body(db: Session, user: User) -> dict:
    info = {"user": user_info_for(db, user)}
    chosen = get_notification_channel(info, None)
    return {
        "person": user.person and person_summary(user.person),
        "accounts": [asdict(a) for a in accounts(db, user)],
        "suggestions": [
            {
                "person": person_summary(s.person),
                "reasons": [{"kind": kind, "value": value} for kind, value in s.reasons],
            }
            for s in suggestions(db, user)
        ],
        "notify": {
            "order": list(CHANNEL_PRIORITY),
            "chosen": chosen and chosen[0],
            "problems": channel_problems(info),
        },
    }


@router.get("")
def get_identity(user: User = Depends(get_interactive_user), db: Session = Depends(get_session)) -> dict:
    return identity_body(db, user)


class Claim(BaseModel):
    person_id: int


@router.post("/claim")
def claim(
    data: Claim, user: User = Depends(get_interactive_user), db: Session = Depends(get_session)
) -> dict:
    """Make a Person one of your verified accounts belongs to yours, merging it
    into your Person if you have one."""
    try:
        claim_person(db, user, data.person_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    db.commit()
    return identity_body(db, user)


@router.delete("/accounts/{kind}/{account_id:path}")
def unlink(
    kind: Literal["discord", "slack", "github", "email"],
    account_id: str,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> dict:
    """Remove an account from your Person. Accounts a credential proves stay
    until the credential is removed."""
    try:
        unlink_account(db, user, kind, account_id)
    except CredentialAccount:
        raise HTTPException(
            status_code=400,
            detail="This account is proven by a connected credential; disconnect that instead",
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Your person has no such account")
    db.commit()
    return identity_body(db, user)


@router.post("/discord-code")
def discord_code(user: User = Depends(get_interactive_user)) -> dict:
    """A one-time code that proves the Discord account DMing ``link <code>`` to
    one of ``bots`` (your own running ones) is yours."""
    code, expires_at = discord_link.issue_code(user.id)
    bots = [bot.name for bot in user.discord_bots if bot.is_active and bot.token_encrypted is not None]
    return {"code": code, "expires_at": expires_at.isoformat(), "bots": bots}
