"""Time as events. A cron subscription (an AgentTrigger of kind "cron") writes a
tick event each time it is due, and a timer is an event with a fire time and its
own handler; run_clock hands both, once due, to their handler's task: a notify
handler's message is sent by send_time_notify (scheduler queue), an agent
handler's run is started by memory.agents.cron (agents queue). Both are resent
until the event ends, so a lost message delays it but can't lose it.

Runs on the scheduler queue, so this module must stay importable by the
standard worker image: nothing from memory.agents.spec or memory.agents.enqueue.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from celery.exceptions import SoftTimeLimitExceeded
from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import Integer, cast, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from memory.agents.principal import PrincipalRevoked
from memory.agents.triggers import (
    FAILED,
    MAX_TEMPLATE_CHARS,
    SKIPPED_DISABLED,
    lock_trigger,
    tell_owner,
)
from memory.common import settings
from memory.common.access_control import UserProxy, credential_scopes
from memory.common.celery_app import SEND_TIME_NOTIFY, START_TIME_RUN, app
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import AgentTrigger, APIKey, Event, User
from memory.common.db.models.events import TICK, TIME_KINDS, TIMER
from memory.common.discord_data import (
    caller_can_see_discord_channel,
    caller_can_see_discord_user,
    discord_channel_for_target,
)
from memory.common.notify_delivery import NotificationParams, deliver_notification

logger = logging.getLogger(__name__)

CRON = "cron"
SENT = "sent"
SKIPPED_LATE = "skipped:late"
SKIPPED_COALESCED = "skipped:coalesced"
SKIPPED_CANCELLED = "skipped:cancelled"
# A notify being sent: set and committed before the send, so a retry after a
# crash mid-send can't send it twice.
SENDING = "sending"
# A retry finding a send started longer ago than this takes it for interrupted.
SEND_TIMEOUT = timedelta(minutes=5)
# A recurring message that fails this many times in a row trips its subscription.
FAILURES_TO_TRIP = 3
# A reminder delivered later than this says when it was due.
LATE_AFTER = timedelta(minutes=5)
# After an outage, a tick or agent timer this late is dropped rather than run long
# after the moment it was meant for; the next tick runs as normal. A late
# reminder is still sent.
MAX_LATENESS = timedelta(hours=6)
# A handoff still undelivered this long after it was sent is sent again, each
# resend waiting twice as long, so a backlogged queue doesn't fill with copies.
DISPATCH_GRACE_MINUTES = 2
MAX_DISPATCHES = 8
CLOCK_BATCH = 50
CLOCK_LOCK_KEY = 0x636C6F636B  # "clock"
# Occurrences counted while catching up on one cron subscription, so a minutely
# expression after a long outage can't spin; also bounds check_cron's scan.
MAX_MISSED = 10_000
# check_cron's scan: a week holds every gap of a 5-field cron's weekly pattern.
CRON_SCAN = timedelta(days=7)
MAX_MESSAGE_CHARS = 20_000
CHANNELS = ("discord", "slack", "email")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def next_cron(expression: str, tz: str, after: datetime) -> datetime:
    """When `expression`, read on the wall clock of `tz`, next fires after `after`
    (aware), in UTC.

    A local time skipped by a DST change fires at the same wall-clock time on the
    other side of the gap (02:30 becomes 03:30); a local time that happens twice
    fires on its first occurrence only.
    """
    # croniter's own tz handling misplaces runs across DST changes, so it walks
    # naive wall-clock times and zoneinfo maps each one to UTC.
    zone = ZoneInfo(tz)
    cron = croniter(expression, after.astimezone(zone).replace(tzinfo=None))
    while True:
        candidate = cron.get_next(datetime).replace(tzinfo=zone).astimezone(timezone.utc)
        if candidate > after:
            return candidate


def check_cron_syntax(expression: str) -> str:
    if not croniter.is_valid(expression):
        raise ValueError("invalid cron expression")
    fields = len(expression.split())
    if fields != 5:
        raise ValueError(f"only 5-field cron expressions are supported, got {fields}")
    return expression


def check_cron(expression: str) -> str:
    """`expression`, if it is a 5-field cron whose occurrences are never closer
    than MIN_CRON_INTERVAL_MINUTES, over a full week of them."""
    check_cron_syntax(expression)
    minimum = timedelta(minutes=settings.MIN_CRON_INTERVAL_MINUTES)
    cron = croniter(expression, utcnow().replace(tzinfo=None))
    previous = cron.get_next(datetime)
    end = previous + CRON_SCAN
    for _ in range(MAX_MISSED):
        current = cron.get_next(datetime)
        if current - previous < minimum:
            raise ValueError(
                f"cron interval too short ({(current - previous).total_seconds() / 60:.0f}m); "
                f"the minimum is {settings.MIN_CRON_INTERVAL_MINUTES} minutes"
            )
        if current > end:
            break
        previous = current
    return expression


class CronConfig(BaseModel):
    """A cron subscription's source."""

    model_config = ConfigDict(extra="forbid")

    cron: str
    # An agent handler's run input.
    input: str = Field(default="", max_length=MAX_TEMPLATE_CHARS)

    # The interval is checked when a schedule is set (check_cron), so a later
    # change to MIN_CRON_INTERVAL_MINUTES doesn't stop schedules already set.
    @field_validator("cron")
    @classmethod
    def valid_cron(cls, value: str) -> str:
        return check_cron_syntax(value)


class Notify(BaseModel):
    """A notify handler: the message it sends and where to."""

    model_config = ConfigDict(extra="forbid")

    channel: Literal["discord", "slack", "email"]
    # A resolved Discord/Slack id or email address; DM or channel is read off it.
    target: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    subject: str | None = None
    discord_bot_id: int | None = None
    from_address: str | None = None

    def params(self, user_id: int) -> NotificationParams:
        extra = {
            "subject": self.subject,
            "discord_bot_id": self.discord_bot_id,
            "from_address": self.from_address,
        }
        return NotificationParams(
            notification_channel=self.channel,
            notification_target=self.target,
            message=self.message,
            user_id=user_id,
            topic=self.subject,
            data={k: v for k, v in extra.items() if v is not None},
        )


@dataclass(frozen=True)
class Target:
    """Who a tick or timer acts for, and how."""

    owner: User
    handler: dict[str, Any]
    input: str
    api_key_id: int | None
    via_api_key: bool
    # A tick's subscription.
    trigger: AgentTrigger | None


def target_of(db: DBSession, event: Event) -> Target | None:
    """None when a tick's subscription was disabled meanwhile."""
    owner = db.get(User, event.owner_id)
    if owner is None:
        return None
    if event.kind == TIMER:
        data = event.data or {}
        return Target(
            owner,
            event.handler or {},
            data.get("input") or "",
            data.get("api_key_id"),
            bool(data.get("via_api_key")),
            None,
        )
    trigger = db.get(AgentTrigger, event.trigger_id)
    if trigger is None or not trigger.enabled:
        return None
    return Target(
        owner,
        trigger.handler,
        (trigger.config or {}).get("input") or "",
        trigger.api_key_id,
        trigger.via_api_key,
        trigger,
    )


def bound_key(db: DBSession, target: Target) -> APIKey | None:
    """The API key the work was set up through, while it is still valid."""
    if target.via_api_key and target.api_key_id is None:
        raise PrincipalRevoked("the API key that set this up was deleted")
    if target.api_key_id is None:
        return None
    key = db.get(APIKey, target.api_key_id)
    if key is None or not key.is_valid():
        raise PrincipalRevoked(
            f"the API key that set this up ({target.api_key_id}) is no longer valid"
        )
    return key


def check_notify_target(db: DBSession, owner: User, key: APIKey | None, notify: Notify) -> None:
    """Only Discord targets depend on a key's scopes: admins may DM anyone, others
    whom their bots have seen."""
    if key is None or notify.channel != "discord":
        return
    caller = UserProxy.from_user(owner, scopes=credential_scopes(owner, key))
    channel = discord_channel_for_target(db, notify.target)
    if channel is None and not notify.target.isdigit():
        raise ValueError("its Discord target is not a channel or user id")
    visible = (
        caller_can_see_discord_channel(db, caller, channel)
        if channel is not None
        else caller_can_see_discord_user(db, caller, int(notify.target))
    )
    if not visible:
        raise PrincipalRevoked("the API key that set this up may not message its target")


def finish(event: Event, status: str, **details: Any) -> None:
    event.result = {"status": status, **details}
    event.routed_at = utcnow()


def lateness(event: Event, now: datetime) -> timedelta:
    return now - event.fire_at if event.fire_at else timedelta(0)


def late_details(event: Event, now: datetime) -> dict[str, int]:
    late = lateness(event, now)
    return {"late_sec": int(late.total_seconds())} if late > LATE_AFTER else {}


def too_late(event: Event, target: Target, now: datetime) -> bool:
    """Ticks and agent timers this late are dropped; reminders still go out."""
    reminder = event.kind == TIMER and "notify" in target.handler
    return not reminder and lateness(event, now) > MAX_LATENESS


def trip(db: DBSession, trigger_id: int, reason: str) -> AgentTrigger | None:
    """Disable, uncommitted, a cron subscription that can't fire any more; its
    owner is told (Notices.tell) once that's committed."""
    trigger = lock_trigger(db, trigger_id)
    if trigger is None or not trigger.enabled:
        return None
    trigger.enabled = False
    trigger.state = (trigger.state or {}) | {"tripped": {"reason": reason, "at": utcnow().isoformat()}}
    logger.warning("cron subscription %s tripped", trigger.id)
    return trigger


@dataclass
class Notices:
    """What to tell owners once the transaction that decided it commits."""

    tripped: list[tuple[AgentTrigger, str]] = field(default_factory=list)
    # (owner id, timer id, reason)
    failed_timers: list[tuple[int, int, str]] = field(default_factory=list)

    def tell(self, db: DBSession) -> None:
        for trigger, reason in self.tripped:
            owner = db.get(User, trigger.user_id)
            if owner is not None:
                tell_owner(
                    db,
                    owner,
                    subject=f"Schedule paused: {trigger.name}",
                    message=f"Schedule {trigger.id} was disabled: {reason}\n"
                    "Enable it again once that's resolved.",
                )
        for owner_id, timer_id, reason in self.failed_timers:
            owner = db.get(User, owner_id)
            if owner is not None:
                tell_owner(db, owner, subject="Timer failed", message=f"Timer {timer_id} failed: {reason}")


def fail(
    db: DBSession, event: Event, target: Target, reason: str, notices: Notices, trip_it: bool = True
) -> None:
    """Fail the event, telling its owner of a failed timer; with `trip_it`, a
    subscription that would fail every tick is tripped."""
    finish(event, FAILED, error=reason)
    if event.kind == TIMER:
        notices.failed_timers.append((target.owner.id, event.id, reason))
    elif trip_it and target.trigger is not None and (trigger := trip(db, target.trigger.id, reason)):
        notices.tripped.append((trigger, reason))


def deliver(db: DBSession, event: Event, now: datetime, notices: Notices) -> str | None:
    """Hand off, uncommitted, a due tick or timer; returns the task to send it to
    (which ends it), or None when it ended here."""
    target = target_of(db, event)
    if target is None:
        finish(event, SKIPPED_DISABLED)
        return None
    if too_late(event, target, now):
        finish(event, SKIPPED_LATE, **late_details(event, now))
        return None
    if "notify" in target.handler:
        task = SEND_TIME_NOTIFY
    elif "agent" in target.handler:
        task = START_TIME_RUN
    else:
        fail(db, event, target, "only notify and agent handlers take ticks and timers", notices)
        return None
    if event.attempts >= MAX_DISPATCHES:
        fail(db, event, target, "it was handed off but never delivered", notices, trip_it=False)
        return None
    event.dispatched_at, event.attempts = now, event.attempts + 1
    return task


def message_for(event: Event, target: Target, notify: Notify, now: datetime) -> NotificationParams:
    params = notify.params(target.owner.id)
    if event.kind == TIMER and event.fire_at and lateness(event, now) > LATE_AFTER:
        due = event.fire_at.astimezone(ZoneInfo(target.owner.timezone))
        params.message = f"(Due {due:%Y-%m-%d %H:%M %Z})\n\n{params.message}"
    return params


def record_send(db: DBSession, event: Event, target: Target, sent: bool, notices: Notices) -> None:
    """End, uncommitted, the event by how its send went; a recurring message
    failing FAILURES_TO_TRIP times in a row trips its subscription."""
    trigger = lock_trigger(db, target.trigger.id) if target.trigger is not None else None
    failures = 0
    if trigger is not None:
        others = {k: v for k, v in (trigger.state or {}).items() if k != "failures"}
        failures = (trigger.state or {}).get("failures", 0) + 1
        trigger.state = others if sent else others | {"failures": failures}
        # Before trip() rereads the trigger.
        db.flush()
    if sent:
        finish(event, SENT, **late_details(event, utcnow()))
        return
    tripping = failures >= FAILURES_TO_TRIP
    reason = "the message could not be sent" + (f" ({failures} times in a row)" if tripping else "")
    fail(db, event, target, reason, notices, trip_it=tripping)


def send_time_notify(event_id: int) -> str | None:
    """Send a due tick's or timer's message and end the event; returns its status.

    The event is marked sending, and that committed, before the send, so a retry
    after a crash mid-send ends it failed instead of sending it again.
    """
    notices = Notices()
    with make_session() as db:
        event = db.get(Event, event_id, with_for_update={"skip_locked": True})
        if event is None or event.routed_at is not None:
            return None
        result = event.result or {}
        sending = result.get("status") == SENDING
        if sending and utcnow() - datetime.fromisoformat(result["at"]) < SEND_TIMEOUT:
            return None
        target = target_of(db, event)
        if target is None:
            finish(event, SKIPPED_DISABLED)
        elif sending:
            reason = "interrupted while sending; not sent again in case it went out"
            fail(db, event, target, reason, notices, trip_it=False)
        else:
            try:
                notify = Notify.model_validate(target.handler["notify"])
                check_notify_target(db, target.owner, bound_key(db, target), notify)
            except ValidationError:
                fail(db, event, target, "its notify handler is invalid", notices)
            except ValueError as e:
                fail(db, event, target, str(e), notices)
            else:
                params = message_for(event, target, notify, utcnow())
                event.result = {"status": SENDING, "at": utcnow().isoformat()}
                db.commit()
                sent = deliver_notification(params)
                event = db.get_one(Event, event_id, with_for_update=True, populate_existing=True)
                # A retry took this send for interrupted meanwhile; its outcome stands.
                if event.routed_at is not None:
                    return None
                record_send(db, event, target_of(db, event) or target, sent, notices)
        db.commit()
        notices.tell(db)
        return (event.result or {}).get("status")


def coalesce(db: DBSession, trigger_id: int) -> int:
    """Skip, uncommitted, the subscription's ticks still waiting, which the tick
    about to be written replaces; returns how many ticks they stood for."""
    waiting = db.scalars(
        select(Event)
        .where(
            Event.kind == TICK,
            Event.trigger_id == trigger_id,
            Event.routed_at.is_(None),
            # Handed off already: resends and MAX_LATENESS see to those, so a
            # lagging queue still runs its ticks.
            Event.dispatched_at.is_(None),
        )
        .with_for_update(skip_locked=True)
    ).all()
    for ev in waiting:
        finish(ev, SKIPPED_COALESCED)
    return sum(1 + (ev.data or {}).get("missed", 0) for ev in waiting)


def write_ticks(db: DBSession, now: datetime, notices: "Notices") -> int:
    """Write, uncommitted, a tick for each cron subscription due by `now`: one for
    its latest due time, counting the earlier ones it stands for as missed."""
    due = db.scalars(
        select(AgentTrigger)
        .where(
            AgentTrigger.kind == CRON,
            AgentTrigger.enabled,
            or_(AgentTrigger.next_at.is_(None), AgentTrigger.next_at <= now),
        )
        .order_by(AgentTrigger.id)
        .with_for_update(skip_locked=True, key_share=True)
    ).all()
    written = 0
    for trigger in due:
        owner = db.get(User, trigger.user_id)
        try:
            config = CronConfig.model_validate(trigger.config)
        except ValidationError:
            reason = "its cron expression is no longer valid"
            if tripped_trigger := trip(db, trigger.id, reason):
                notices.tripped.append((tripped_trigger, reason))
            continue
        tz = owner.timezone if owner else "UTC"
        if trigger.next_at is None:
            trigger.next_at = next_cron(config.cron, tz, now)
            continue
        scheduled, missed = trigger.next_at, 0
        while missed < MAX_MISSED and (later := next_cron(config.cron, tz, scheduled)) <= now:
            scheduled, missed = later, missed + 1
        missed += coalesce(db, trigger.id)
        db.execute(
            insert(Event)
            .values(
                kind=TICK,
                trigger_id=trigger.id,
                owner_id=trigger.user_id,
                fire_at=scheduled,
                data={"missed": missed},
            )
            .on_conflict_do_nothing(
                index_elements=[Event.trigger_id, Event.fire_at], index_where=Event.kind == TICK
            )
        )
        trigger.next_at = next_cron(config.cron, tz, now)
        written += 1
    return written


def due_events(db: DBSession, now: datetime) -> list[Event]:
    """Ticks and timers due by `now` and not delivered, locked; an agent start
    already sent only once its resend is due."""
    resend_after = func.make_interval(
        0,
        0,
        0,
        0,
        0,
        cast(DISPATCH_GRACE_MINUTES * func.power(2, func.greatest(Event.attempts - 1, 0)), Integer),
    )
    return list(
        db.scalars(
            select(Event)
            .where(
                Event.kind.in_(TIME_KINDS),
                Event.routed_at.is_(None),
                Event.fire_at <= now,
                or_(Event.dispatched_at.is_(None), Event.dispatched_at + resend_after <= now),
            )
            .order_by(Event.fire_at, Event.id)
            .limit(CLOCK_BATCH)
            .with_for_update(skip_locked=True)
        )
    )


def run_clock() -> dict[str, int]:
    """Write the ticks that are due, then hand due ticks and timers to their
    handler's task. Nothing here waits on the network: the tasks send."""
    now = utcnow()
    notices = Notices()
    handoffs: list[tuple[str, int]] = []
    with make_session() as db:
        if not db.scalar(select(func.pg_try_advisory_xact_lock(CLOCK_LOCK_KEY))):
            return {"ticks": 0, "delivered": 0}
        ticks = write_ticks(db, now, notices)
        events = due_events(db, now)
        for ev in events:
            try:
                with db.begin_nested():
                    if task := deliver(db, ev, now, notices):
                        handoffs.append((task, ev.id))
            except SoftTimeLimitExceeded:
                raise
            except Exception as e:
                ev.attempts += 1
                if ev.attempts >= settings.EVENT_MAX_ATTEMPTS:
                    finish(ev, FAILED, error=type(e).__name__)
                logger.exception("could not deliver event %s", ev.id)
        db.commit()
        notices.tell(db)
    for task, event_id in handoffs:
        try:
            app.send_task(task, args=[event_id])
        except Exception:
            logger.exception("could not hand event %s off", event_id)
    return {"ticks": ticks, "delivered": len(events)}


def schedule_next(trigger: AgentTrigger, tz: str) -> None:
    """Point an enabled cron subscription at its next tick from now."""
    config = CronConfig.model_validate(trigger.config)
    trigger.next_at = next_cron(config.cron, tz, utcnow())


def reschedule_user(db: DBSession, user_id: int, tz: str) -> None:
    """Move a user's enabled cron subscriptions onto their new timezone, uncommitted."""
    crons = db.scalars(
        select(AgentTrigger).where(
            AgentTrigger.user_id == user_id, AgentTrigger.kind == CRON, AgentTrigger.enabled
        )
    ).all()
    for trigger in crons:
        try:
            schedule_next(trigger, tz)
        except ValidationError:
            logger.warning("not rescheduling cron subscription %s: its config is invalid", trigger.id)


def pending_timers(db: DBSession, user_id: int) -> list[Event]:
    return list(
        db.scalars(
            select(Event)
            .where(Event.kind == TIMER, Event.owner_id == user_id, Event.routed_at.is_(None))
            .order_by(Event.fire_at, Event.id)
        )
    )


def add_timer(
    db: DBSession,
    user_id: int,
    fire_at: datetime,
    handler: dict[str, Any],
    api_key_id: int | None,
    input: str | None = None,
) -> Event:
    """Add, uncommitted, a timer firing `handler` at `fire_at`, within MAX_TIMERS_PER_USER."""
    if fire_at <= utcnow():
        raise ValueError("the time must be in the future")
    if len(pending_timers(db, user_id)) >= settings.MAX_TIMERS_PER_USER:
        raise ValueError(
            f"at most {settings.MAX_TIMERS_PER_USER} of your timers can wait to fire "
            "(MAX_TIMERS_PER_USER); cancel one first"
        )
    data: dict[str, Any] = {"api_key_id": api_key_id, "via_api_key": api_key_id is not None}
    if input is not None:
        data["input"] = input
    timer = Event(kind=TIMER, owner_id=user_id, fire_at=fire_at, handler=handler, data=data)
    db.add(timer)
    db.flush()
    return timer
