"""Queue handlers and claim-time rendering of worker runs.

A subscription whose handler is {"queue": name} leaves its deliveries pending
until one of its owner's workers claims them by that name. A claim turns them into
a run on workers, rendered from the items as they are now; the worker reports back
on that run. Runs an ingest trigger placed on workers are re-rendered the same way.

An item the subscription can no longer read has its delivery skipped. One only the
claiming credential can't read is left for another worker: a narrow key never ends
what a wider one could deliver.
"""

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Collection

from pydantic import ValidationError
from sqlalchemy import exists, func, or_, select, tuple_
from sqlalchemy.orm import aliased

from memory.agents.caps import CapExceeded, check_trigger_rate
from memory.agents.fire import count_failure, hold_back, trip
from memory.agents.principal import PrincipalRevoked, standing_key
from memory.agents.spec import MAX_TEXT_CHARS
from memory.agents.trigger_input import render_input
from memory.agents.triggers import (
    DELIVERY_MAX_AGE,
    DONE,
    INGEST,
    LIVE_INGEST,
    PENDING,
    SKIPPED_INACCESSIBLE,
    SKIPPED_POSTED,
    THROTTLED,
    Batch,
    IngestConfig,
    accessible_ids,
    changes_by_item,
    coalesced_ref,
    dedupe_key,
    delivery_ref,
    due_batches,
    episode_over,
    lock_trigger,
    posted_among,
    trigger_key,
)
from memory.common import settings
from memory.common.access_control import credential_scopes
from memory.common.db.connection import DBSession
from memory.common.db.models import (
    AgentRun,
    AgentTrigger,
    AgentTriggerMatch,
    APIKey,
    SourceItem,
    User,
)
from memory.common.db.models.agents import EXECUTOR_WORKERS, TERMINAL_STATUSES, RunStatus
from memory.common.scopes import SCOPE_ADMIN, SCOPE_READ

logger = logging.getLogger(__name__)

CLAIM_SCAN = 200
# Workers poll about every second, so a claim that fails waits this long before
# its next attempt, or a passing error would use up every attempt at once.
CLAIM_RETRY = timedelta(minutes=1)


def can_read(owner: User, key: APIKey | None) -> bool:
    return bool({SCOPE_READ, SCOPE_ADMIN} & set(credential_scopes(owner, key)))


def is_delivery(run: AgentRun) -> bool:
    """Whether the run carries items an ingest trigger delivered."""
    return run.trigger_kind == INGEST and "item_ids" in (run.trigger_ref or {})


def subscription_key(db: DBSession, trigger: AgentTrigger, owner: User) -> APIKey | None:
    """The key a trigger reads through; raises PrincipalRevoked once it can't. A queue
    subscription only hands items over, so reading is all it needs."""
    if "agent" in trigger.handler:
        return trigger_key(db, trigger, owner)
    if trigger.via_api_key and trigger.api_key_id is None:
        raise PrincipalRevoked("the API key that set up this trigger was deleted")
    return standing_key(db, owner, trigger.api_key_id, SCOPE_READ)


def unposted(db: DBSession, trigger: AgentTrigger, item_ids: Collection[int]) -> set[int]:
    """The `item_ids` none of the trigger's runs reported posting: a worker's reply can
    be stored and routed before its report says it posted it."""
    items = db.query(SourceItem).filter(SourceItem.id.in_(item_ids)).all()
    return set(item_ids) - posted_among(db, trigger.id, items)


def rendered(
    db: DBSession, template: str, item_ids: list[int], changes: dict[str, list[str]]
) -> str:
    items = db.query(SourceItem).filter(SourceItem.id.in_(item_ids)).order_by(SourceItem.id).all()
    return render_input(template, items, MAX_TEXT_CHARS, changes)


def fail(run: AgentRun, error: str) -> None:
    run.status, run.finished_at, run.error = RunStatus.FAILED.value, datetime.now(timezone.utc), error
    # What it would have shown is gone with the access that let it.
    run.input, run.trigger_ref = "", {}


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
    try:
        key = subscription_key(db, trigger, owner)
        trigger_can = set(accessible_ids(db, owner, key, run.trigger_ref["item_ids"]))
    except PrincipalRevoked:
        trigger_can = set()
    fresh = unposted(db, trigger, trigger_can)
    kept = [i for i in run.trigger_ref["item_ids"] if i in fresh]
    if kept and set(accessible_ids(db, owner, claimer, kept)) != set(kept):
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
    run.input = rendered(db, template, kept, changes)
    run.trigger_ref = run.trigger_ref | {"item_ids": kept, "changes": changes}
    return True


def ready_to_claim(db: DBSession, run: AgentRun, owner: User, claimer: APIKey | None) -> bool | None:
    """rerender, failing the run if rendering it breaks, so a run that can't be
    rendered can't hold up every claim behind it."""
    run_id = run.id
    try:
        return rerender(db, run, owner, claimer)
    except Exception:
        logger.exception("could not render run %s for a claim", run_id)
        db.rollback()
    broken = db.get(AgentRun, run_id, with_for_update=True, populate_existing=True)
    if broken is None or broken.status != RunStatus.PENDING.value:
        return None
    fail(broken, "it could not be rendered")
    return False


def lapsed_run(db: DBSession, owner: User, queue: str, claimer: APIKey | None) -> AgentRun | None:
    """A run of the queue whose lease lapsed, locked and rendered afresh, while its
    trigger is live and still on that queue."""
    hold = timedelta(seconds=settings.AGENT_WORK_HOLD_MAX_SEC)
    passed: list[str] = []
    while True:
        run = db.scalars(
            select(AgentRun)
            .join(AgentTrigger, AgentTrigger.id == AgentRun.trigger_id)
            .where(
                AgentRun.user_id == owner.id,
                AgentRun.queue == queue,
                AgentRun.status == RunStatus.PENDING.value,
                AgentRun.executor == EXECUTOR_WORKERS,
                AgentRun.started_at > datetime.now(timezone.utc) - hold,
                AgentRun.id.not_in(passed),
                *LIVE_INGEST,
                AgentTrigger.handler["queue"].astext == queue,
            )
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
        else:
            db.commit()


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
    trigger_can = set(accessible_ids(db, owner, subscription_key(db, trigger, owner), item_ids))
    fresh = unposted(db, trigger, trigger_can)
    claimer_can = set(accessible_ids(db, owner, claimer, item_ids))
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
        input=rendered(db, template, taken_ids, changes),
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
    one, else one made from the oldest claimable deliveries. Scans in CLAIM_SCAN batches
    by (created_at, item_id) cursor without locks, then locks the trigger and the
    deliveries it picks, passing over any another worker took since.

    A trigger whose deliveries can't be claimed now (deferred by a cap, tripped, or
    failing) is passed over for the rest of the claim. A failure counts against its
    deliveries, which wait CLAIM_RETRY and fail after EVENT_MAX_ATTEMPTS, so none can
    block the queue.
    """
    if (run := lapsed_run(db, owner, queue, claimer)) is not None:
        return run
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
            return None
        for created_at, trigger_id, item_id, version in scan:
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


def backlog(db: DBSession, owner_id: int) -> list[dict[str, Any]]:
    """Each of the owner's queues with its pending deliveries' count and oldest."""
    queue = AgentTrigger.handler["queue"].astext
    rows = db.execute(
        select(queue, func.count(AgentTriggerMatch.item_id), func.min(AgentTriggerMatch.created_at))
        .select_from(AgentTrigger)
        .outerjoin(
            AgentTriggerMatch,
            (AgentTriggerMatch.trigger_id == AgentTrigger.id) & (AgentTriggerMatch.status == PENDING),
        )
        .where(AgentTrigger.user_id == owner_id, AgentTrigger.handler.has_key("queue"))
        .group_by(queue)
        .order_by(queue)
    ).all()
    now = datetime.now(timezone.utc)
    return [
        {
            "queue": name,
            "pending": count,
            "oldest_age_sec": None if oldest is None else int((now - oldest).total_seconds()),
        }
        for name, count, oldest in rows
    ]
