from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, undefer

from memory.agents.triggers import accessible_ids
from memory.api.auth import request_api_key, require_rest_access
from memory.common.access_control import has_admin_scope
from memory.common.db.connection import get_session
from memory.common.db.models import AgentTrigger, AgentTriggerMatch, Event, GithubAccount, TranscriptAccount, User
from memory.common.db.models.discord import DiscordBot
from memory.common.db.models.events import NOT_COLLECTED, RAW
from memory.common.db.models.slack import SlackApp

EVENT_ARRIVAL_RESOLUTION = timedelta(minutes=1)


def record_event_arrival(db: Session, row: SlackApp | DiscordBot) -> None:
    """Stamp last_event_at, at most once a minute so busy integrations don't write per event."""
    now = datetime.now(timezone.utc)
    if row.last_event_at and now - row.last_event_at < EVENT_ARRIVAL_RESOLUTION:
        return
    row.last_event_at = now
    db.commit()


router = APIRouter(prefix="/webhook-events", tags=["sources"])

Source = Literal["github", "slack", "discord", "transcripts"]
# Each source's account table and the owner its raw events are recorded for.
ACCOUNTS: dict[str, tuple[Any, Any]] = {
    "github": (GithubAccount, GithubAccount.user_id),
    "slack": (SlackApp, SlackApp.created_by_user_id),
    "discord": (DiscordBot, DiscordBot.created_by_user_id),
    "transcripts": (TranscriptAccount, TranscriptAccount.user_id),
}


class WebhookAccount(BaseModel):
    source: str
    account_id: int
    name: str
    last_event_id: int | None
    last_event_at: datetime | None


class Triggered(BaseModel):
    subscription_id: int
    subscription: str
    item_id: int
    status: str
    run_id: str | None


class RawEventView(BaseModel):
    id: int
    created_at: datetime
    outcome: str
    type: str | None
    container: str | None
    item_ids: list[int]
    triggered: list[Triggered]
    payload: Any = None


@router.get("")
def webhook_accounts(
    user: User = Depends(require_rest_access), db: Session = Depends(get_session)
) -> list[WebhookAccount]:
    """The source accounts whose webhook logs the caller may read (an admin: all),
    each with its latest delivery."""
    accounts: list[tuple[str, int, str, int | None]] = []
    for source, (model, owner) in ACCOUNTS.items():
        latest = (
            select(func.max(Event.id))
            .where(Event.kind == RAW, Event.source == source, Event.account_id == model.id)
            .scalar_subquery()
        )
        query = select(model.id, model.name, latest).order_by(model.id)
        if not has_admin_scope(user):
            query = query.where(owner == user.id)
        accounts += [(source, id, name, last) for id, name, last in db.execute(query)]
    latest_ids = [last for *_, last in accounts if last is not None]
    times = dict(db.execute(select(Event.id, Event.created_at).where(Event.id.in_(latest_ids))).tuples().all())
    return [
        WebhookAccount(
            source=source, account_id=id, name=name, last_event_id=last, last_event_at=times.get(last) if last else None
        )
        for source, id, name, last in accounts
    ]


def triggered_by(db: Session, user: User, raw_ids: list[int]) -> dict[int, list[Triggered]]:
    """For each raw event, the deliveries its items were routed into, of the
    caller's subscriptions (an admin: everyone's)."""
    query = (
        select(Event.caused_by, AgentTriggerMatch, AgentTrigger.name)
        .join(AgentTriggerMatch, AgentTriggerMatch.event_id == Event.id)
        .join(AgentTrigger, AgentTrigger.id == AgentTriggerMatch.trigger_id)
        .where(Event.caused_by.in_(raw_ids))
        .order_by(Event.id, AgentTrigger.id)
    )
    if not has_admin_scope(user):
        query = query.where(AgentTrigger.user_id == user.id)
    found: dict[int, list[Triggered]] = {}
    for raw_id, match, name in db.execute(query):
        found.setdefault(raw_id, []).append(
            Triggered(
                subscription_id=match.trigger_id,
                subscription=name,
                item_id=match.item_id,
                status=match.status,
                run_id=match.run_id,
            )
        )
    return found


def event_view(e: Event, items: list[int], triggered: list[Triggered], payload: bool) -> RawEventView:
    # An untracked channel is untracked: its stub says only that something arrived.
    if e.outcome == NOT_COLLECTED:
        return RawEventView(
            id=e.id, created_at=e.created_at, outcome=NOT_COLLECTED, type=None, container=None, item_ids=[], triggered=[]
        )
    return RawEventView(
        id=e.id,
        created_at=e.created_at,
        outcome=e.outcome or "",
        type=e.type,
        container=e.container,
        item_ids=items,
        triggered=triggered,
        payload=e.payload if payload else None,
    )


@router.get("/{source}/{account_id}")
def webhook_log(
    request: Request,
    source: Source,
    account_id: int,
    before: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    payload: bool = False,
    user: User = Depends(require_rest_access),
    db: Session = Depends(get_session),
) -> list[RawEventView]:
    """The newest webhook deliveries to one of the caller's source accounts (an
    admin: any account's), before event id `before`, with the items each stored
    and the deliveries those were routed into. Payloads are untrusted and only
    included on request, until they expire."""
    query = (
        select(Event)
        .where(Event.kind == RAW, Event.source == source, Event.account_id == account_id)
        .order_by(Event.id.desc())
        .limit(limit)
    )
    if not has_admin_scope(user):
        query = query.where(Event.owner_id == user.id)
    if before is not None:
        query = query.where(Event.id < before)
    if payload:
        query = query.options(undefer(Event.payload))
    events = db.scalars(query).all()
    raw_ids = [e.id for e in events]
    caused = db.execute(
        select(Event.caused_by, Event.item_id).where(Event.caused_by.in_(raw_ids)).order_by(Event.id)
    ).all()
    # The account's owner may since have lost access to an item it stored.
    readable = set(accessible_ids(db, user, request_api_key(request, db), [i for _, i in caused if i is not None]))
    items: dict[int, list[int]] = {}
    for raw_id, item_id in caused:
        if item_id in readable:
            items.setdefault(raw_id, []).append(item_id)
    triggered = {
        raw_id: [t for t in found if t.item_id in readable] for raw_id, found in triggered_by(db, user, raw_ids).items()
    }
    return [event_view(e, items.get(e.id, []), triggered.get(e.id, []), payload) for e in events]
