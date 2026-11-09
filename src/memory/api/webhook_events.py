from datetime import datetime, timedelta, timezone
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import BigInteger, cast as sql_cast, func, select
from sqlalchemy.orm import Session, undefer

from memory.agents.activity import run_outcome
from memory.agents.raw_events import container_of, container_reader
from memory.agents.subscriptions import accessible_ids
from memory.api.auth import request_api_key, require_rest_access
from memory.common.access_control import UserProxy, credential_scopes, has_admin_scope
from memory.common.db.connection import get_session
from memory.common.db.models import (
    AgentRun,
    APIKey,
    Delivery,
    DiscordChannel,
    Event,
    GithubAccount,
    GithubRepo,
    Subscription,
    TranscriptAccount,
    User,
)
from memory.common.db.models.discord import DiscordBot
from memory.common.db.models.events import LINKS_ITEMS, NOT_COLLECTED, RAW
from memory.common.discord_data import visible_channels
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
Outcome = Literal["stored", "not_collected", "no_item_type", "rejected"]
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
    # The caller's own: only these can carry the caller's raw subscriptions.
    own: bool


class Triggered(BaseModel):
    subscription_id: int
    subscription: str
    # None for a raw subscription's delivery of the event itself.
    item_id: int | None
    status: str
    run_id: str | None


class RawEventView(BaseModel):
    id: int
    created_at: datetime
    outcome: str
    type: str | None
    container: str | None
    # The repo's full name or the channel's name, where the viewer may see it.
    container_name: str | None = None
    # Why syncing the repo fails: events arriving don't mean its items are stored.
    container_error: str | None = None
    # Whether its ingest links the items it writes; others (GitHub refetches,
    # Discord reactions) change items without linking them here.
    links_items: bool = False
    item_ids: list[int]
    triggered: list[Triggered]
    payload: Any = None
    # The viewer couldn't read items filed where it came from, so its payload is left out.
    payload_withheld: bool = False


def credential_viewer(user: User, key: APIKey | None) -> UserProxy:
    """The caller as its credential sees: an admin's read-only key is no admin."""
    return UserProxy.from_user(user, scopes=credential_scopes(user, key))


@router.get("")
def webhook_accounts(
    request: Request, user: User = Depends(require_rest_access), db: Session = Depends(get_session)
) -> list[WebhookAccount]:
    """The source accounts whose webhook logs the caller may read (an admin: all),
    each with its latest delivery."""
    viewer = credential_viewer(user, request_api_key(request, db))
    accounts: list[tuple[str, int, str, int | None, bool]] = []
    for source, (model, owner) in ACCOUNTS.items():
        latest = (
            select(func.max(Event.id))
            .where(Event.kind == RAW, Event.source == source, Event.account_id == model.id)
            .scalar_subquery()
        )
        query = select(model.id, model.name, latest, owner == user.id).order_by(model.id)
        if not has_admin_scope(viewer):
            query = query.where(owner == user.id)
        accounts += [(source, id, name, last, bool(own)) for id, name, last, own in db.execute(query)]
    latest_ids = [last for *_, last, _ in accounts if last is not None]
    times = dict(db.execute(select(Event.id, Event.created_at).where(Event.id.in_(latest_ids))).tuples().all())
    return [
        WebhookAccount(
            source=source,
            account_id=id,
            name=name,
            last_event_id=last,
            last_event_at=times.get(last) if last else None,
            own=own,
        )
        for source, id, name, last, own in accounts
    ]


# As agent_runs_raw_event_idx indexes it.
RAW_RUN_EVENT = sql_cast(AgentRun.trigger_ref["event_id"].astext, BigInteger)


def raw_runs_of(raw_ids: list[int]):
    """Raw subscriptions' runs of these raw events, with their subscription's name."""
    return (
        select(AgentRun, Subscription.name)
        .join(Subscription, Subscription.id == AgentRun.subscription_id)
        .where(AgentRun.trigger_kind == RAW, RAW_RUN_EVENT.in_(raw_ids))
        .order_by(AgentRun.created_at, Subscription.id)
    )


def started_run(run_id: str | None, started: bool) -> str | None:
    """A delivery links its run only once the run started: until then there's nothing to see."""
    return run_id if started else None


def triggered_by(db: Session, user: UserProxy, raw_ids: list[int]) -> dict[int, list[Triggered]]:
    """For each raw event, the deliveries its items were routed into, of the
    caller's subscriptions (an admin: everyone's)."""
    query = (
        select(Event.caused_by, Delivery, Subscription.name, AgentRun.started_at.is_not(None))
        .join(Delivery, Delivery.event_id == Event.id)
        .join(Subscription, Subscription.id == Delivery.subscription_id)
        .outerjoin(AgentRun, AgentRun.id == Delivery.run_id)
        .where(Event.caused_by.in_(raw_ids))
        .order_by(Event.id, Subscription.id)
    )
    raw_runs = raw_runs_of(raw_ids)
    if not has_admin_scope(user):
        query = query.where(Subscription.user_id == user.id)
        raw_runs = raw_runs.where(Subscription.user_id == user.id)
    found: dict[int, list[Triggered]] = {}
    for raw_id, match, name, started in db.execute(query):
        found.setdefault(raw_id, []).append(
            Triggered(
                subscription_id=match.subscription_id,
                subscription=name,
                item_id=match.item_id,
                status=match.status,
                run_id=started_run(match.run_id, bool(started)),
            )
        )
    for run, name in db.execute(raw_runs):
        found.setdefault(run.trigger_ref["event_id"], []).append(
            Triggered(
                subscription_id=cast(int, run.subscription_id),
                subscription=name,
                item_id=None,
                status=run_outcome(run),
                run_id=started_run(run.id, run.started_at is not None),
            )
        )
    return found


def numeric(containers: set[str]) -> list[int]:
    return [int(c) for c in containers if c.isascii() and c.isdigit()]


def container_errors(db: Session, source: str, account_id: int, containers: set[str]) -> dict[str, str]:
    """The sync errors of this GitHub account's failing repos."""
    if source != "github":
        return {}
    rows = db.execute(
        select(GithubRepo.github_id, GithubRepo.sync_error).where(
            GithubRepo.account_id == account_id,
            GithubRepo.github_id.in_(numeric(containers)),
            GithubRepo.sync_error.is_not(None),
        )
    )
    return {str(id): error for id, error in rows}


def container_names(db: Session, user: UserProxy, source: str, account_id: int, containers: set[str]) -> dict[str, str]:
    """Names for the repos of this GitHub account, or the Discord channels the viewer can see."""
    ids = numeric(containers)
    if source == "github":
        rows = db.execute(
            select(GithubRepo.github_id, GithubRepo.owner + "/" + GithubRepo.name).where(
                GithubRepo.account_id == account_id, GithubRepo.github_id.in_(ids)
            )
        )
    elif source == "discord":
        rows = visible_channels(db, user).filter(DiscordChannel.id.in_(ids)).with_entities(
            DiscordChannel.id, DiscordChannel.name
        )
    else:
        return {}
    return {str(id): name for id, name in rows}


def event_view(
    e: Event,
    items: list[int],
    triggered: list[Triggered],
    payload: bool,
    withheld: bool,
    names: dict[str, str],
    errors: dict[str, str],
) -> RawEventView:
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
        container_name=names.get(e.container) if e.container else None,
        container_error=errors.get(e.container) if e.container else None,
        links_items=bool((e.data or {}).get(LINKS_ITEMS)),
        item_ids=items,
        triggered=triggered,
        payload=e.payload if payload and not withheld else None,
        payload_withheld=withheld,
    )


@router.get("/{source}/{account_id}")
def webhook_log(
    request: Request,
    source: Source,
    account_id: int,
    before: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    payload: bool = False,
    outcome: Outcome | None = None,
    event_type: str | None = Query(None, alias="type"),
    user: User = Depends(require_rest_access),
    db: Session = Depends(get_session),
) -> list[RawEventView]:
    """The newest webhook deliveries to one of the caller's source accounts (an
    admin: any account's), before event id `before`, with `outcome` and a type
    starting with `type`, with the items each stored and the deliveries those
    were routed into. Payloads are untrusted and only included on request,
    until they expire."""
    query = (
        select(Event)
        .where(Event.kind == RAW, Event.source == source, Event.account_id == account_id)
        .order_by(Event.id.desc())
        .limit(limit)
    )
    key = request_api_key(request, db)
    viewer = credential_viewer(user, key)
    if not has_admin_scope(viewer):
        query = query.where(Event.owner_id == user.id)
    if before is not None:
        query = query.where(Event.id < before)
    if outcome is not None:
        query = query.where(Event.outcome == outcome)
    # A delivery not collected shows no type, so a type never finds one.
    if event_type:
        query = query.where(Event.type.startswith(event_type, autoescape=True), Event.outcome != NOT_COLLECTED)
    if payload:
        query = query.options(undefer(Event.payload))
    events = db.scalars(query).all()
    raw_ids = [e.id for e in events]
    caused = db.execute(
        select(Event.caused_by, Event.item_id).where(Event.caused_by.in_(raw_ids)).order_by(Event.id)
    ).all()
    # The account's owner may since have lost access to an item it stored.
    readable = set(accessible_ids(db, user, key, [i for _, i in caused if i is not None]))
    items: dict[int, list[int]] = {}
    for raw_id, item_id in caused:
        if item_id in readable:
            items.setdefault(raw_id, []).append(item_id)
    triggered = {
        raw_id: [t for t in found if t.item_id is None or t.item_id in readable]
        for raw_id, found in triggered_by(db, viewer, raw_ids).items()
    }
    # The raw subscriptions' rule. Only deliveries that store nothing keep a
    # payload, so there are no stored items to check as well.
    withheld: set[int] = set()
    if payload:
        reads = container_reader(db, user, key)
        withheld = {
            e.id
            for e in events
            if e.payload is not None and not ((c := container_of(db, e)) is not None and reads(c))
        }
    containers = {e.container for e in events if e.container}
    names = container_names(db, viewer, source, account_id, containers)
    errors = container_errors(db, source, account_id, containers)
    return [
        event_view(e, items.get(e.id, []), triggered.get(e.id, []), payload, e.id in withheld, names, errors)
        for e in events
    ]
