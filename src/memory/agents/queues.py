"""Queue handlers and claim-time rendering of worker runs.

A subscription whose handler is {"queue": name} leaves its deliveries pending
until one of its owner's workers claims them by that name. A claim turns them into
a run on workers, rendered from the items as they are now; the worker reports back
on that run. Runs an ingest subscription placed on workers are re-rendered the same way.

An item the subscription can no longer read has its delivery skipped. One only the
claiming credential can't read is left for another worker: a narrow key never ends
what a wider one could deliver.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fnmatch import fnmatchcase
from typing import Any, Collection, cast

from pydantic import ValidationError
from sqlalchemy import distinct, exists, func, or_, select, tuple_
from sqlalchemy.orm import aliased, undefer

from memory.agents.caps import CapExceeded, check_subscription_rate
from memory.agents.fire import count_failure, hold_back, trip
from memory.agents.principal import PrincipalRevoked
from memory.agents.raw_events import ACCOUNTS, ROUTABLE, account_owner, container_of, reads_container
from memory.agents.spec import MAX_TEXT_CHARS
from memory.agents.subscription_input import CLIPPED, bare, new_tag, preamble, render_input, untrusted
from memory.agents.subscriptions import (
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
    lock_subscription,
    RAW_WAITING,
    removals,
    subscription_key,
    posted_among,
)
from memory.common import settings
from memory.common.db.connection import DBSession
from memory.common.db.models import (
    AgentRun,
    Subscription,
    Delivery,
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


def is_delivery(run: AgentRun) -> bool:
    """Whether the run carries items an ingest subscription delivered."""
    return run.trigger_kind == INGEST and "item_ids" in (run.trigger_ref or {})


def unposted(db: DBSession, subscription: Subscription, item_ids: Collection[int]) -> set[int]:
    """The `item_ids` none of the subscription's runs reported posting: a worker's reply can
    be stored and routed before its report says it posted it."""
    items = db.query(SourceItem).filter(SourceItem.id.in_(item_ids)).all()
    return set(item_ids) - posted_among(db, subscription.id, items)


def rendered(
    db: DBSession, template: str, item_ids: list[int], changes: dict[str, list[str]], removed: set[int]
) -> str:
    items = db.query(SourceItem).filter(SourceItem.id.in_(item_ids)).order_by(SourceItem.id).all()
    return render_input(template, items, MAX_TEXT_CHARS, changes, removed & set(item_ids))


def raw_input(event: Event, item_ids: list[int], show_payload: bool) -> str:
    """A raw delivery's input: what arrived, the items it stored, and its payload,
    untrusted and clipped to fit MAX_TEXT_CHARS, unless it is withheld."""
    where = f", container {bare(event.container)}" if event.container else ""
    tag = new_tag()
    lines = [
        preamble(tag),
        f"{event.source} {bare(event.type or '')} delivery (raw event {event.id}, "
        f"account {event.account_id}{where}, {event.outcome})"
    ]
    if item_ids:
        lines.append("Stored as items: " + ", ".join(str(i) for i in item_ids))
    if event.payload is not None and not show_payload:
        lines.append("payload withheld")
    elif event.payload is not None:
        text = json.dumps(event.payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
        empty = untrusted(tag, f"raw:{event.id}", "payload", "")
        room = MAX_TEXT_CHARS - len("\n".join([*lines, empty])) - len(CLIPPED)
        lines.append(untrusted(tag, f"raw:{event.id}", "payload", text, max(room, 0)))
    return "\n".join(lines)


def render_raw(db: DBSession, run: AgentRun, owner: User, claimer: APIKey | None) -> bool | None:
    """Render, uncommitted, a raw delivery run about to be claimed, from its event as
    kept now. The run fails (False) once its subscription is gone, its credential
    can't read, the account the event came in on is no longer the owner's, or the
    channel or repo it's about is no longer collected. The payload is shown only
    if both the subscription's key and the claimer could read items filed there
    and every item it stored; one only the claimer can't is left, unchanged, for
    another worker (None), as filter deliveries are."""
    subscription = db.get(Subscription, run.subscription_id) if run.subscription_id is not None else None
    if subscription is None:
        fail(run, "its subscription was deleted")
        return False
    try:
        key = subscription_key(db, subscription, owner)
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
        "subscription_id": subscription.id,
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
    items as they are now. Deliveries of items its subscription can no longer read, or
    reported posting, are skipped; with none left, or the subscription gone, broken or
    revoked, the run fails (False). None, changing nothing, if `claimer` can't read
    one of the rest."""
    subscription = db.get(Subscription, run.subscription_id) if run.subscription_id is not None else None
    if subscription is None:
        fail(run, "its subscription was deleted")
        return False
    try:
        template = IngestConfig.model_validate(subscription.config).template
    except ValidationError:
        fail(run, "its subscription's config is no longer valid")
        return False
    removal_ids = db.scalars(
        select(Delivery.item_id).where(
            Delivery.run_id == run.id, Delivery.content_hash == REMOVED_VERSION
        )
    )
    removed = removals(db, subscription.id, set(removal_ids))
    try:
        key = subscription_key(db, subscription, owner)
        subscription_can = deliverable_ids(db, owner, key, run.trigger_ref["item_ids"], removed)
    except PrincipalRevoked:
        subscription_can = set()
    fresh = unposted(db, subscription, subscription_can)
    kept = [i for i in run.trigger_ref["item_ids"] if i in fresh]
    if kept and deliverable_ids(db, owner, claimer, kept, removed) != set(kept):
        return None
    gone = db.scalars(
        select(Delivery).where(
            Delivery.run_id == run.id, Delivery.item_id.not_in(kept)
        )
    )
    for d in gone:
        d.status = SKIPPED_POSTED if d.item_id in subscription_can else SKIPPED_INACCESSIBLE
    if not kept and subscription_can:
        fail(run, "its subscription's runs already posted everything it can read of it")
        return False
    if not kept:
        fail(run, "its subscription can't read anything it was started for now")
        return False
    changes = {k: v for k, v in run.trigger_ref.get("changes", {}).items() if int(k) in subscription_can}
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
        Subscription.id == AgentRun.subscription_id,
        AgentRun.user_id == owner.id,
        AgentRun.queue == queue,
        AgentRun.status == RunStatus.PENDING.value,
        AgentRun.executor == EXECUTOR_WORKERS,
        *which,
        Subscription.kind.in_((INGEST, RAW)),
        Subscription.enabled,
        Subscription.handler["queue"].astext == queue,
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
    earlier = aliased(Delivery)
    unfinished = or_(
        earlier.status == PENDING,
        exists().where(
            AgentRun.id == earlier.run_id, AgentRun.status.not_in(list(TERMINAL_STATUSES))
        ),
    )
    horizon = now - DELIVERY_MAX_AGE - timedelta(seconds=settings.AGENT_WORK_HOLD_MAX_SEC)
    held = exists().where(
        earlier.subscription_id == Delivery.subscription_id,
        earlier.order_key == Delivery.order_key,
        earlier.created_at > horizon,
        earlier.created_at < Delivery.created_at,
        unfinished,
    )
    return (
        Subscription.id == Delivery.subscription_id,
        Subscription.user_id == owner.id,
        *LIVE_INGEST,
        Subscription.handler["queue"].astext == queue,
        Delivery.status == PENDING,
        or_(Delivery.not_before.is_(None), Delivery.not_before <= now),
        ~held,
    )


MATCH_KEY = tuple_(
    Delivery.subscription_id, Delivery.item_id, Delivery.content_hash
)


def locked_group(
    db: DBSession,
    subscription: Subscription,
    batch: Batch | None,
    key: tuple[int, int, bytes],
    conditions: tuple[Any, ...],
    now: datetime,
) -> list[Delivery]:
    """The deliveries a claim of the one at `key` takes, locked: it alone, or its
    subscription's first due batch. Empty if another worker has them or no batch is due."""
    query = (
        select(Delivery)
        .where(*conditions)
        .with_for_update(skip_locked=True, of=Delivery)
    )
    if batch is None:
        return list(db.scalars(query.where(MATCH_KEY == key)))
    pending = db.scalars(
        query.where(Delivery.subscription_id == subscription.id).order_by(
            Delivery.created_at, Delivery.item_id
        )
    ).all()
    return next(iter(due_batches(list(pending), batch, now)), [])


def delivery_run(
    db: DBSession,
    subscription: Subscription,
    template: str,
    owner: User,
    claimer: APIKey | None,
    queue: str,
    group: list[Delivery],
) -> AgentRun | None:
    """The run, uncommitted, for the deliveries in `group` that the subscription and the
    claimer can read, which are marked done. Those the subscription can't read, or
    reported posting, are skipped; those only the claimer can't stay pending. None
    if no run is left."""
    item_ids = [d.item_id for d in group]
    removed = removals(db, subscription.id, {d.item_id for d in group if bytes(d.content_hash) == REMOVED_VERSION})
    subscription_can = deliverable_ids(db, owner, subscription_key(db, subscription, owner), item_ids, removed)
    fresh = unposted(db, subscription, subscription_can)
    claimer_can = deliverable_ids(db, owner, claimer, item_ids, removed)
    for d in group:
        if d.item_id not in subscription_can:
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
        trigger_ref={"subscription_id": subscription.id, "item_ids": taken_ids, "changes": changes}
        | coalesced_ref(taken),
        dedupe_key=dedupe_key(subscription.id, taken),
        subscription_id=subscription.id,
        api_key_id=subscription.api_key_id,
        executor=EXECUTOR_WORKERS,
    )
    db.add(run)
    for d in taken:
        d.status, d.run = DONE, run
    if THROTTLED in subscription.state and episode_over(subscription.state, datetime.now(timezone.utc)):
        subscription.state = {k: v for k, v in subscription.state.items() if k != THROTTLED}
    db.flush()
    return run


def take_from_queue(
    db: DBSession, owner: User, queue: str, claimer: APIKey | None
) -> AgentRun | None:
    """The next run of the owner's `queue` for `claimer` to lease, uncommitted: a lapsed
    one, else the oldest of the raw deliveries and those made from the oldest claimable
    deliveries. Scans in CLAIM_SCAN batches
    by (created_at, item_id) cursor without locks, then locks the subscription and the
    deliveries it picks, passing over any another worker took since.

    A subscription whose deliveries can't be claimed now (deferred by a cap, tripped, or
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
            Delivery.created_at,
            Delivery.subscription_id,
            Delivery.item_id,
            Delivery.content_hash,
        ).where(*conditions)
        if passed:
            query = query.where(Delivery.subscription_id.not_in(passed))
        if cursor is not None:
            query = query.where(
                tuple_(Delivery.created_at, Delivery.item_id) > cursor
            )
        scan = db.execute(
            query.order_by(Delivery.created_at, Delivery.item_id).limit(CLAIM_SCAN)
        ).all()
        if not scan:
            return None if raw_at is None else queued_run(db, owner, queue, claimer, raw=True)
        for created_at, subscription_id, item_id, version in scan:
            if raw_at is not None and raw_at <= created_at:
                if (run := queued_run(db, owner, queue, claimer, raw=True)) is not None:
                    return run
                raw_at = None
            cursor = (created_at, item_id)
            if subscription_id in passed:
                continue
            subscription = lock_subscription(db, subscription_id)
            if subscription is None:
                continue
            group: list[Delivery] = []
            try:
                config = IngestConfig.model_validate(subscription.config)
                batch = config.delivery if isinstance(config.delivery, Batch) else None
                key = (subscription_id, item_id, version)
                group = locked_group(db, subscription, batch, key, conditions, now)
                if group:
                    check_subscription_rate(db, subscription)
                    run = delivery_run(db, subscription, config.template, owner, claimer, queue, group)
                    if run is not None:
                        return run
                # A batch is claimed from its oldest member, so the rest can't go alone.
                elif batch is not None:
                    passed.add(subscription_id)
                db.commit()
                continue
            except CapExceeded as e:
                refs = [delivery_ref(d.item_id, d.content_hash) for d in group]
                db.rollback()
                hold_back(db, subscription_id, refs, e)
            # Revoked credentials and a config that no longer validates trip the
            # subscription, as they do an agent subscription.
            except (PrincipalRevoked, ValidationError) as e:
                db.rollback()
                trip(db, subscription_id, owner, str(e))
            except Exception as e:
                logger.exception("could not claim deliveries of subscription %s", subscription_id)
                refs = [delivery_ref(d.item_id, d.content_hash) for d in group]
                db.rollback()
                count_failure(db, subscription_id, refs, e, retry_after=CLAIM_RETRY)
            passed.add(subscription_id)


def queue_backlogs(db: DBSession, owner_id: int | None = None) -> list[dict[str, Any]]:
    """Each queue, the owner's or everyone's, with its subscriptions and its pending
    deliveries' count and oldest age: filter deliveries and unclaimed raw ones of
    its enabled subscriptions, the ones a worker can claim."""
    queue = Subscription.handler["queue"].astext
    query = (
        select(
            Subscription.user_id,
            queue,
            func.array_agg(distinct(Subscription.id)),
            func.count(Delivery.item_id),
            func.min(Delivery.created_at),
        )
        .select_from(Subscription)
        .outerjoin(
            Delivery,
            (Delivery.subscription_id == Subscription.id)
            & (Delivery.status == PENDING)
            & Subscription.enabled,
        )
        .where(Subscription.handler.has_key("queue"))
        .group_by(Subscription.user_id, queue)
        .order_by(Subscription.user_id, queue)
    )
    raw_query = (
        select(AgentRun.user_id, AgentRun.queue, func.count(AgentRun.id), func.min(AgentRun.created_at))
        .join(Subscription, Subscription.id == AgentRun.subscription_id)
        .where(*RAW_WAITING, Subscription.enabled)
        .group_by(AgentRun.user_id, AgentRun.queue)
    )
    if owner_id is not None:
        query = query.where(Subscription.user_id == owner_id)
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


def matching_queues(db: DBSession, owner_id: int, patterns: list[str]) -> list[str]:
    """The owner's queues whose names match any of the globs, the one with the oldest
    pending delivery first and those with none last."""
    matched = [q for q in backlog(db, owner_id) if any(fnmatchcase(q["queue"], p) for p in patterns)]
    matched.sort(key=lambda q: (q["oldest_age_sec"] is None, -(q["oldest_age_sec"] or 0)))
    return [q["queue"] for q in matched]


def queue_health(db: DBSession) -> list[dict[str, Any]]:
    """Every queue's backlog for the operator, named by ids: queue names are the
    owner's own words."""
    return [{k: v for k, v in q.items() if k != "queue"} for q in queue_backlogs(db)]
