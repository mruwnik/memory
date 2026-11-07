"""Queue handlers and claim-time rendering of worker runs.

A subscription whose handler is {"queue": name} leaves its deliveries pending
until one of its owner's workers claims them by that name. A claim turns them into
a run on workers, rendered from the items as they are now; the worker reports back
on that run. Runs an ingest trigger placed on workers are re-rendered the same way.

An item the subscription can no longer read has its delivery skipped. One only the
claiming credential can't read is left for another worker: a narrow key never ends
what a wider one could deliver.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Collection, cast

from pydantic import ValidationError
from sqlalchemy import distinct, exists, func, or_, select, tuple_
from sqlalchemy.orm import aliased, undefer

from memory.agents.caps import CapExceeded, check_trigger_rate
from memory.agents.fire import count_failure, hold_back, trip
from memory.agents.principal import PrincipalRevoked
from memory.agents.raw_events import ACCOUNTS, ROUTABLE, account_owner, container_of, reads_container
from memory.agents.spec import MAX_TEXT_CHARS
from memory.agents.trigger_input import bare, render_input, untrusted
from memory.agents.triggers import (
    DELIVERY_MAX_AGE,
    DONE,
    INGEST,
    LIVE_INGEST,
    PENDING,
    REMOVED_VERSION,
    SKIPPED_INACCESSIBLE,
    SKIPPED_POSTED,
    THROTTLED,
    Batch,
    IngestConfig,
    accessible_ids,
    can_read,
    changes_by_item,
    coalesced_ref,
    dedupe_key,
    deliverable_ids,
    delivery_ref,
    due_batches,
    episode_over,
    lock_trigger,
    RAW_WAITING,
    removals,
    subscription_key,
    posted_among,
)
from memory.common import settings
from memory.common.db.connection import DBSession
from memory.common.db.models import (
    AgentRun,
    AgentTrigger,
    AgentTriggerMatch,
    APIKey,
    Event,
    SourceItem,
    User,
)
from memory.common.db.models.agents import EXECUTOR_WORKERS, TERMINAL_STATUSES, RunStatus
from memory.common.db.models.events import ITEM, RAW

logger = logging.getLogger(__name__)

CLAIM_SCAN = 200
# Workers poll about every second, so a claim that fails waits this long before
# its next attempt, or a passing error would use up every attempt at once.
CLAIM_RETRY = timedelta(minutes=1)
# Runs one claim walks past for a worker that can't render them, so a key narrower
# than the queue's runs can't scan it end to end on every poll.
MAX_PASSED_RAW = 20
RAW_CLIPPED = "\n[payload truncated]</untrusted_item>"


def is_delivery(run: AgentRun) -> bool:
    """Whether the run carries items an ingest trigger delivered."""
    return run.trigger_kind == INGEST and "item_ids" in (run.trigger_ref or {})


def unposted(db: DBSession, trigger: AgentTrigger, item_ids: Collection[int]) -> set[int]:
    """The `item_ids` none of the trigger's runs reported posting: a worker's reply can
    be stored and routed before its report says it posted it."""
    items = db.query(SourceItem).filter(SourceItem.id.in_(item_ids)).all()
    return set(item_ids) - posted_among(db, trigger.id, items)


def rendered(
    db: DBSession, template: str, item_ids: list[int], changes: dict[str, list[str]], removed: set[int]
) -> str:
    items = db.query(SourceItem).filter(SourceItem.id.in_(item_ids)).order_by(SourceItem.id).all()
    return render_input(template, items, MAX_TEXT_CHARS, changes, removed & set(item_ids))


def raw_input(event: Event, item_ids: list[int], show_payload: bool) -> str:
    """A raw delivery's input: what arrived, the items it stored, and its payload,
    untrusted and clipped to fit MAX_TEXT_CHARS, unless it is withheld."""
    where = f", container {bare(event.container)}" if event.container else ""
    lines = [
        f"{event.source} {bare(event.type or '')} delivery (raw event {event.id}, "
        f"account {event.account_id}{where}, {event.outcome})"
    ]
    if item_ids:
        lines.append("Stored as items: " + ", ".join(str(i) for i in item_ids))
    if event.payload is not None and not show_payload:
        lines.append("payload withheld")
    elif event.payload is not None:
        text = json.dumps(event.payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
        lines.append(untrusted(f"raw:{event.id}", "payload", text))
    rendered = "\n".join(lines)
    if len(rendered) <= MAX_TEXT_CHARS:
        return rendered
    return rendered[: MAX_TEXT_CHARS - len(RAW_CLIPPED)] + RAW_CLIPPED


def render_raw(db: DBSession, run: AgentRun, owner: User, claimer: APIKey | None) -> bool | None:
    """Render, uncommitted, a raw delivery run about to be claimed, from its event as
    kept now. The run fails (False) once its subscription is gone, its credential
    can't read, the account the event came in on is no longer the owner's, or the
    channel or repo it's about is no longer collected. The payload is shown only
    if both the subscription's key and the claimer could read items filed there
    and every item it stored; one only the claimer can't is left, unchanged, for
    another worker (None), as filter deliveries are."""
    trigger = db.get(AgentTrigger, run.trigger_id) if run.trigger_id is not None else None
    if trigger is None:
        fail(run, "its subscription was deleted")
        return False
    try:
        key = subscription_key(db, trigger, owner)
    except PrincipalRevoked as e:
        fail(run, str(e))
        return False
    event = db.get(Event, run.trigger_ref.get("event_id"), options=[undefer(Event.payload)])
    if not can_read(owner, key):
        fail(run, "its subscription's credential can no longer read")
        return False
    if (
        event is None
        or event.outcome not in ROUTABLE
        or event.source not in ACCOUNTS
        or account_owner(db, event.source, cast(int, event.account_id)) != owner.id
    ):
        fail(run, "its delivery is gone, or came in on an account no longer yours")
        return False
    container = container_of(db, event)
    if container is not None and not container.collected:
        fail(run, "its channel or repo is no longer collected")
        return False
    caused = db.scalars(
        select(Event.item_id).where(Event.caused_by == event.id, Event.kind == ITEM).distinct()
    )
    stored = [i for i in caused if i is not None]
    present = set(db.scalars(select(SourceItem.id).where(SourceItem.id.in_(stored))))
    subscription_reads = set(accessible_ids(db, owner, key, stored))
    claimer_reads = set(accessible_ids(db, owner, claimer, stored))

    def sees(k: APIKey | None, reads: set[int]) -> bool:
        return container is not None and present <= reads and reads_container(db, owner, k, container)

    show_payload = event.payload is not None and sees(key, subscription_reads)
    if show_payload and not sees(claimer, claimer_reads):
        return None
    item_ids = sorted(subscription_reads & claimer_reads)
    run.input = raw_input(event, item_ids, show_payload)
    run.trigger_ref = {
        "trigger_id": trigger.id,
        "event_id": event.id,
        "source": event.source,
        "type": event.type,
        "outcome": event.outcome,
        "account_id": event.account_id,
        "container": event.container,
        "item_ids": item_ids,
    }
    return True


def fail(run: AgentRun, error: str) -> None:
    run.status, run.finished_at, run.error = RunStatus.FAILED.value, datetime.now(timezone.utc), error
    # What it would have shown is gone with the access that let it; a raw delivery
    # keeps its event, which the webhook log lists it under.
    ref = run.trigger_ref or {}
    run.input, run.trigger_ref = "", {"event_id": ref["event_id"]} if "event_id" in ref else {}


def rerender(db: DBSession, run: AgentRun, owner: User, claimer: APIKey | None) -> bool | None:
    """Render, uncommitted, a delivery run (is_delivery) about to be claimed, from the
    items as they are now. Deliveries of items its trigger can no longer read, or
    reported posting, are skipped; with none left, or the trigger gone, broken or
    revoked, the run fails (False). None, changing nothing, if `claimer` can't read
    one of the rest."""
    trigger = db.get(AgentTrigger, run.trigger_id) if run.trigger_id is not None else None
    if trigger is None:
        fail(run, "its trigger was deleted")
        return False
    try:
        template = IngestConfig.model_validate(trigger.config).template
    except ValidationError:
        fail(run, "its trigger's config is no longer valid")
        return False
    removal_ids = db.scalars(
        select(AgentTriggerMatch.item_id).where(
            AgentTriggerMatch.run_id == run.id, AgentTriggerMatch.content_hash == REMOVED_VERSION
        )
    )
    removed = removals(db, trigger.id, set(removal_ids))
    try:
        key = subscription_key(db, trigger, owner)
        trigger_can = deliverable_ids(db, owner, key, run.trigger_ref["item_ids"], removed)
    except PrincipalRevoked:
        trigger_can = set()
    fresh = unposted(db, trigger, trigger_can)
    kept = [i for i in run.trigger_ref["item_ids"] if i in fresh]
    if kept and deliverable_ids(db, owner, claimer, kept, removed) != set(kept):
        return None
    gone = db.scalars(
        select(AgentTriggerMatch).where(
            AgentTriggerMatch.run_id == run.id, AgentTriggerMatch.item_id.not_in(kept)
        )
    )
    for d in gone:
        d.status = SKIPPED_POSTED if d.item_id in trigger_can else SKIPPED_INACCESSIBLE
    if not kept:
        fail(run, "its trigger can't read anything it was started for now")
        return False
    changes = {k: v for k, v in run.trigger_ref.get("changes", {}).items() if int(k) in trigger_can}
    run.input = rendered(db, template, kept, changes, removed)
    run.trigger_ref = run.trigger_ref | {"item_ids": kept, "changes": changes}
    return True


def ready_to_claim(db: DBSession, run: AgentRun, owner: User, claimer: APIKey | None) -> bool | None:
    """rerender, failing the run if rendering it breaks, so a run that can't be
    rendered can't hold up every claim behind it."""
    run_id = run.id
    try:
        if run.trigger_kind == RAW:
            return render_raw(db, run, owner, claimer)
        return rerender(db, run, owner, claimer)
    except Exception:
        logger.exception("could not render run %s for a claim", run_id)
        db.rollback()
    broken = db.get(AgentRun, run_id, with_for_update=True, populate_existing=True)
    if broken is None or broken.status != RunStatus.PENDING.value:
        return None
    fail(broken, "it could not be rendered")
    return False


def queue_runs(owner: User, queue: str, raw: bool) -> tuple[Any, ...]:
    """Runs of the owner's live subscriptions still on `queue`: with `raw`, raw
    deliveries no worker claimed yet, else runs whose lease lapsed."""
    hold = timedelta(seconds=settings.AGENT_WORK_HOLD_MAX_SEC)
    which = RAW_WAITING if raw else (AgentRun.started_at > datetime.now(timezone.utc) - hold,)
    return (
        AgentTrigger.id == AgentRun.trigger_id,
        AgentRun.user_id == owner.id,
        AgentRun.queue == queue,
        AgentRun.status == RunStatus.PENDING.value,
        AgentRun.executor == EXECUTOR_WORKERS,
        *which,
        AgentTrigger.kind.in_((INGEST, RAW)),
        AgentTrigger.enabled,
        AgentTrigger.handler["queue"].astext == queue,
    )


def queued_run(
    db: DBSession, owner: User, queue: str, claimer: APIKey | None, raw: bool = False
) -> AgentRun | None:
    """The oldest of queue_runs, locked and rendered afresh."""
    passed: list[str] = []
    while True:
        run = db.scalars(
            select(AgentRun)
            .where(*queue_runs(owner, queue, raw), AgentRun.id.not_in(passed))
            .order_by(AgentRun.created_at, AgentRun.id)
            .limit(1)
            .with_for_update(skip_locked=True, of=AgentRun)
        ).first()
        if run is None:
            return None
        run_id = run.id
        ready = ready_to_claim(db, run, owner, claimer)
        if ready:
            return run
        if ready is None:
            passed.append(run_id)
            db.rollback()
            if len(passed) >= MAX_PASSED_RAW:
                return None
        else:
            db.commit()


def oldest_raw(db: DBSession, owner: User, queue: str) -> datetime | None:
    return db.scalar(select(func.min(AgentRun.created_at)).where(*queue_runs(owner, queue, raw=True)))


def claimable(owner: User, queue: str, now: datetime) -> tuple[Any, ...]:
    """Pending, undeferred deliveries of the owner's live subscriptions to `queue` that
    no earlier unfinished delivery with the same order key holds back. Deliveries
    end within DELIVERY_MAX_AGE and their runs within the hold cap, so only those
    recent enough to be unfinished are looked at."""
    earlier = aliased(AgentTriggerMatch)
    unfinished = or_(
        earlier.status == PENDING,
        exists().where(
            AgentRun.id == earlier.run_id, AgentRun.status.not_in(list(TERMINAL_STATUSES))
        ),
    )
    horizon = now - DELIVERY_MAX_AGE - timedelta(seconds=settings.AGENT_WORK_HOLD_MAX_SEC)
    held = exists().where(
        earlier.trigger_id == AgentTriggerMatch.trigger_id,
        earlier.order_key == AgentTriggerMatch.order_key,
        earlier.created_at > horizon,
        earlier.created_at < AgentTriggerMatch.created_at,
        unfinished,
    )
    return (
        AgentTrigger.id == AgentTriggerMatch.trigger_id,
        AgentTrigger.user_id == owner.id,
        *LIVE_INGEST,
        AgentTrigger.handler["queue"].astext == queue,
        AgentTriggerMatch.status == PENDING,
        or_(AgentTriggerMatch.not_before.is_(None), AgentTriggerMatch.not_before <= now),
        ~held,
    )


MATCH_KEY = tuple_(
    AgentTriggerMatch.trigger_id, AgentTriggerMatch.item_id, AgentTriggerMatch.content_hash
)


def locked_group(
    db: DBSession,
    trigger: AgentTrigger,
    batch: Batch | None,
    key: tuple[int, int, bytes],
    conditions: tuple[Any, ...],
    now: datetime,
) -> list[AgentTriggerMatch]:
    """The deliveries a claim of the one at `key` takes, locked: it alone, or its
    trigger's first due batch. Empty if another worker has them or no batch is due."""
    query = (
        select(AgentTriggerMatch)
        .where(*conditions)
        .with_for_update(skip_locked=True, of=AgentTriggerMatch)
    )
    if batch is None:
        return list(db.scalars(query.where(MATCH_KEY == key)))
    pending = db.scalars(
        query.where(AgentTriggerMatch.trigger_id == trigger.id).order_by(
            AgentTriggerMatch.created_at, AgentTriggerMatch.item_id
        )
    ).all()
    return next(iter(due_batches(list(pending), batch, now)), [])


def delivery_run(
    db: DBSession,
    trigger: AgentTrigger,
    template: str,
    owner: User,
    claimer: APIKey | None,
    queue: str,
    group: list[AgentTriggerMatch],
) -> AgentRun | None:
    """The run, uncommitted, for the deliveries in `group` that the trigger and the
    claimer can read, which are marked done. Those the trigger can't read, or
    reported posting, are skipped; those only the claimer can't stay pending. None
    if no run is left."""
    item_ids = [d.item_id for d in group]
    removed = removals(db, trigger.id, {d.item_id for d in group if bytes(d.content_hash) == REMOVED_VERSION})
    trigger_can = deliverable_ids(db, owner, subscription_key(db, trigger, owner), item_ids, removed)
    fresh = unposted(db, trigger, trigger_can)
    claimer_can = deliverable_ids(db, owner, claimer, item_ids, removed)
    for d in group:
        if d.item_id not in trigger_can:
            d.status = SKIPPED_INACCESSIBLE
        elif d.item_id not in fresh:
            d.status = SKIPPED_POSTED
    taken = [d for d in group if d.item_id in fresh & claimer_can]
    if not taken:
        return None
    taken_ids = sorted(d.item_id for d in taken)
    changes = changes_by_item(taken)
    run = AgentRun(
        user_id=owner.id,
        queue=queue,
        spec={},
        input=rendered(db, template, taken_ids, changes, removed),
        max_cost_usd=Decimal(0),
        trigger_kind=INGEST,
        trigger_ref={"trigger_id": trigger.id, "item_ids": taken_ids, "changes": changes}
        | coalesced_ref(taken),
        dedupe_key=dedupe_key(trigger.id, taken),
        trigger_id=trigger.id,
        api_key_id=trigger.api_key_id,
        executor=EXECUTOR_WORKERS,
    )
    db.add(run)
    for d in taken:
        d.status, d.run = DONE, run
    if THROTTLED in trigger.state and episode_over(trigger.state, datetime.now(timezone.utc)):
        trigger.state = {k: v for k, v in trigger.state.items() if k != THROTTLED}
    db.flush()
    return run


def take_from_queue(
    db: DBSession, owner: User, queue: str, claimer: APIKey | None
) -> AgentRun | None:
    """The next run of the owner's `queue` for `claimer` to lease, uncommitted: a lapsed
    one, else the oldest of the raw deliveries and those made from the oldest claimable
    deliveries. Scans in CLAIM_SCAN batches
    by (created_at, item_id) cursor without locks, then locks the trigger and the
    deliveries it picks, passing over any another worker took since.

    A trigger whose deliveries can't be claimed now (deferred by a cap, tripped, or
    failing) is passed over for the rest of the claim. A failure counts against its
    deliveries, which wait CLAIM_RETRY and fail after EVENT_MAX_ATTEMPTS, so none can
    block the queue.
    """
    if (run := queued_run(db, owner, queue, claimer)) is not None:
        return run
    raw_at = oldest_raw(db, owner, queue)
    cursor, passed = None, set()
    while True:
        now = datetime.now(timezone.utc)
        conditions = claimable(owner, queue, now)
        query = select(
            AgentTriggerMatch.created_at,
            AgentTriggerMatch.trigger_id,
            AgentTriggerMatch.item_id,
            AgentTriggerMatch.content_hash,
        ).where(*conditions)
        if passed:
            query = query.where(AgentTriggerMatch.trigger_id.not_in(passed))
        if cursor is not None:
            query = query.where(
                tuple_(AgentTriggerMatch.created_at, AgentTriggerMatch.item_id) > cursor
            )
        scan = db.execute(
            query.order_by(AgentTriggerMatch.created_at, AgentTriggerMatch.item_id).limit(CLAIM_SCAN)
        ).all()
        if not scan:
            return None if raw_at is None else queued_run(db, owner, queue, claimer, raw=True)
        for created_at, trigger_id, item_id, version in scan:
            if raw_at is not None and raw_at <= created_at:
                if (run := queued_run(db, owner, queue, claimer, raw=True)) is not None:
                    return run
                raw_at = None
            cursor = (created_at, item_id)
            if trigger_id in passed:
                continue
            trigger = lock_trigger(db, trigger_id)
            if trigger is None:
                continue
            group: list[AgentTriggerMatch] = []
            try:
                config = IngestConfig.model_validate(trigger.config)
                batch = config.delivery if isinstance(config.delivery, Batch) else None
                key = (trigger_id, item_id, version)
                group = locked_group(db, trigger, batch, key, conditions, now)
                if group:
                    check_trigger_rate(db, trigger)
                    run = delivery_run(db, trigger, config.template, owner, claimer, queue, group)
                    if run is not None:
                        return run
                # A batch is claimed from its oldest member, so the rest can't go alone.
                elif batch is not None:
                    passed.add(trigger_id)
                db.commit()
                continue
            except CapExceeded as e:
                refs = [delivery_ref(d.item_id, d.content_hash) for d in group]
                db.rollback()
                hold_back(db, trigger_id, refs, e)
            # Revoked credentials and a config that no longer validates trip the
            # subscription, as they do an agent trigger.
            except (PrincipalRevoked, ValidationError) as e:
                db.rollback()
                trip(db, trigger_id, owner, str(e))
            except Exception as e:
                logger.exception("could not claim deliveries of trigger %s", trigger_id)
                refs = [delivery_ref(d.item_id, d.content_hash) for d in group]
                db.rollback()
                count_failure(db, trigger_id, refs, e, retry_after=CLAIM_RETRY)
            passed.add(trigger_id)


def queue_backlogs(db: DBSession, owner_id: int | None = None) -> list[dict[str, Any]]:
    """Each queue, the owner's or everyone's, with its subscriptions and its pending
    deliveries' count and oldest age: filter deliveries and unclaimed raw ones."""
    queue = AgentTrigger.handler["queue"].astext
    query = (
        select(
            AgentTrigger.user_id,
            queue,
            func.array_agg(distinct(AgentTrigger.id)),
            func.count(AgentTriggerMatch.item_id),
            func.min(AgentTriggerMatch.created_at),
        )
        .select_from(AgentTrigger)
        .outerjoin(
            AgentTriggerMatch,
            (AgentTriggerMatch.trigger_id == AgentTrigger.id) & (AgentTriggerMatch.status == PENDING),
        )
        .where(AgentTrigger.handler.has_key("queue"))
        .group_by(AgentTrigger.user_id, queue)
        .order_by(AgentTrigger.user_id, queue)
    )
    raw_query = (
        select(AgentRun.user_id, AgentRun.queue, func.count(AgentRun.id), func.min(AgentRun.created_at))
        .where(*RAW_WAITING)
        .group_by(AgentRun.user_id, AgentRun.queue)
    )
    if owner_id is not None:
        query = query.where(AgentTrigger.user_id == owner_id)
        raw_query = raw_query.where(AgentRun.user_id == owner_id)
    raw = {(owner, name): (count, oldest) for owner, name, count, oldest in db.execute(raw_query)}
    now = datetime.now(timezone.utc)

    def counted(owner: int, name: str, ids: list[int], count: int, oldest: datetime | None) -> dict[str, Any]:
        raw_count, raw_oldest = raw.get((owner, name), (0, None))
        first = min((t for t in (oldest, raw_oldest) if t is not None), default=None)
        return {
            "owner_id": owner,
            "queue": name,
            "subscription_ids": sorted(ids),
            "pending": count + raw_count,
            "oldest_age_sec": None if first is None else int((now - first).total_seconds()),
        }

    return [counted(*row) for row in db.execute(query).all()]


def backlog(db: DBSession, owner_id: int) -> list[dict[str, Any]]:
    """Each of the owner's queues with its pending deliveries' count and oldest."""
    return [
        {k: q[k] for k in ("queue", "pending", "oldest_age_sec")} for q in queue_backlogs(db, owner_id)
    ]


def queue_health(db: DBSession) -> list[dict[str, Any]]:
    """Every queue's backlog for the operator, named by ids: queue names are the
    owner's own words."""
    return [{k: v for k, v in q.items() if k != "queue"} for q in queue_backlogs(db)]
