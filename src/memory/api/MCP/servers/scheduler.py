"""MCP subserver for schedules: cron subscriptions, which tick on a cron expression,
and timers, which fire once. Either sends a message or runs an agent definition."""

import logging
from datetime import timezone
from typing import Any

from fastmcp import FastMCP
from pydantic import ValidationError

from memory.agents.clock import (
    CHANNELS,
    CRON,
    SKIPPED_CANCELLED,
    CronConfig,
    Notify,
    add_timer,
    check_cron,
    finish,
    pending_timers,
    schedule_next,
)
from memory.agents.spec import MAX_TEXT_CHARS
from memory.agents.triggers import RESUME, THROTTLED
from memory.api.auth import token_api_key
from memory.api.MCP.access import get_mcp_current_user
from memory.api.MCP.servers.agent import (
    caller,
    caller_token,
    check_spec,
    check_trigger_cap,
    next_trigger_id,
    owned_definition,
    parse_spec,
    trigger_name,
)
from memory.api.MCP.servers.notification_targets import resolve_and_validate_target
from memory.api.MCP.visibility import READ_ONLY, require_scopes, visible_when
from memory.common.access_control import UserProxy
from memory.common.dates import parse_wall_clock_utc
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import AgentDefinition, AgentTrigger, APIKey, Event, User
from memory.common.db.models.events import TICK, TIMER
from memory.common.scopes import SCOPE_ADMIN, SCOPE_AGENTS, SCOPE_SCHEDULE, SCOPE_SCHEDULE_WRITE

logger = logging.getLogger(__name__)

scheduler_mcp = FastMCP("memory-scheduler")

NOTIFY_FIELDS = ("notification_channel", "notification_target", "message", "topic")


def current_caller(db: DBSession) -> UserProxy:
    """The caller, with the scopes of the credential they called with."""
    user = get_mcp_current_user(db)
    if user is None:
        raise ValueError("Not authenticated")
    return user


def owned_schedule(db: DBSession, schedule_id: int, user_id: int) -> AgentTrigger:
    trigger = db.get(AgentTrigger, schedule_id)
    if trigger is None or trigger.user_id != user_id or trigger.kind != CRON:
        raise ValueError(f"unknown schedule {schedule_id}")
    return trigger


def check_binding(bound_key_id: int | None, key: APIKey | None) -> None:
    """An API key may change only what it set up."""
    if key is not None and bound_key_id != key.id:
        raise ValueError("this schedule was set up through another credential")


def check_pause_binding(db: DBSession, trigger: AgentTrigger, key: APIKey | None) -> None:
    """Pausing needs the binding too, unless the key it is bound to no longer
    works: then anyone who owns it may pause it."""
    bound = db.get(APIKey, trigger.api_key_id) if trigger.api_key_id is not None else None
    if trigger.via_api_key and (bound is None or not bound.is_valid()):
        return
    check_binding(trigger.api_key_id, key)


def checked_input(input: str | None) -> str:
    if input is None or not input.strip():
        raise ValueError("input is required to run an agent")
    if len(input) > MAX_TEXT_CHARS:
        raise ValueError(f"input is longer than {MAX_TEXT_CHARS} characters")
    return input


async def runnable_definition(db: DBSession, user_id: int, definition_id: int) -> AgentDefinition:
    """One of the caller's definitions, whose tools they may still use."""
    _, scopes = caller_token()
    if not {SCOPE_AGENTS, SCOPE_ADMIN} & set(scopes):
        raise ValueError("running an agent on a schedule needs the 'agents' scope")
    definition = owned_definition(db, definition_id, user_id)
    await check_spec(parse_spec(definition.spec))
    return definition


def notify_handler(db: DBSession, fields: dict[str, str | None]) -> dict[str, Any]:
    channel, target, message = (fields.get(f) for f in NOTIFY_FIELDS[:3])
    if not (channel and target and message):
        raise ValueError("a message needs notification_channel, notification_target and message")
    if channel not in CHANNELS:
        raise ValueError(f"notification_channel must be one of: {', '.join(CHANNELS)}")
    resolved = resolve_and_validate_target(db, current_caller(db), channel, target)
    try:
        notify = Notify.model_validate(
            {"channel": channel, "target": resolved, "message": message, "subject": fields.get("topic")}
        )
    except ValidationError as e:
        raise ValueError(f"invalid message: {e}") from e
    return {"notify": notify.model_dump(exclude_none=True)}


def stored_notify_fields(handler: dict[str, Any]) -> dict[str, str | None]:
    notify = handler.get("notify") or {}
    return {
        "notification_channel": notify.get("channel"),
        "notification_target": notify.get("target"),
        "message": notify.get("message"),
        "topic": notify.get("subject"),
    }


FIRED_TIMERS_LISTED = 50


def fired_timers(db: DBSession, user_id: int) -> list[Event]:
    return (
        db.query(Event)
        .filter(Event.kind == TIMER, Event.owner_id == user_id, Event.routed_at.is_not(None))
        .order_by(Event.routed_at.desc(), Event.id.desc())
        .limit(FIRED_TIMERS_LISTED)
        .all()
    )


def timer_payload(timer: Event) -> dict[str, Any]:
    return timer.serialize_timer() | {"kind": TIMER}


@scheduler_mcp.tool()
@visible_when(require_scopes(SCOPE_SCHEDULE_WRITE))
async def upsert(
    cron_expression: str | None = None,
    scheduled_time: str | None = None,
    definition_id: int | None = None,
    input: str | None = None,
    notification_channel: str | None = None,
    notification_target: str | None = None,
    message: str | None = None,
    topic: str | None = None,
    name: str | None = None,
    enabled: bool | None = None,
    schedule_id: int | None = None,
) -> dict[str, Any]:
    """Create or update a schedule, which sends a message or runs one of your agent definitions.

    Creating takes exactly one of cron_expression (a recurring schedule; 5 fields,
    at most every MIN_CRON_INTERVAL_MINUTES) or scheduled_time (an ISO datetime:
    a timer that fires once), both read in your timezone (see meta_get_user)
    unless the time carries an offset. And either:
    - notification_channel (discord, slack, email), notification_target and
      message (topic is the subject), to send a message. The target may be a
      person's name, identifier or email (resolved to their Discord/Slack id or
      address), a Discord/Slack channel or user id, or an email address; DM or
      channel post follows from the id.
    - definition_id and input, to run that definition with this input (needs the
      'agents' scope; its tools are checked against your access on every change).
      A recurring run's trigger_ref has its `time`, `since` (the last tick that was
      delivered) and `missed` (ticks it stands for that were skipped, e.g. while the
      server was down; only the latest of those runs, and one more than 6 hours late
      doesn't run at all).

    With schedule_id, updates that recurring schedule; omitted fields keep their
    values, and enabled=false alone always pauses it. Schedules act with the
    credentials that created them (an API key's scopes, if one was used); an API
    key can only change what it created. A schedule whose credentials stop working
    pauses itself and notifies you; enabling it again schedules its next tick from
    now. Recurring schedules count toward AGENT_MAX_TRIGGERS_PER_USER, one-offs
    toward MAX_TIMERS_PER_USER. Returns the schedule (kind "cron") or timer.
    """
    user_id, _ = caller()
    token, _ = caller_token()
    notify_fields: dict[str, str | None] = dict(zip(NOTIFY_FIELDS, (notification_channel, notification_target, message, topic)))
    with make_session() as db:
        key = token_api_key(token, db)
        if schedule_id is not None:
            return await update(
                db, user_id, key, schedule_id, cron_expression, scheduled_time,
                definition_id, input, notify_fields, name, enabled,
            )
        if bool(cron_expression) == bool(scheduled_time):
            raise ValueError("give exactly one of cron_expression (recurring) or scheduled_time (once)")
        definition = None
        if definition_id is not None:
            if any(notify_fields.values()):
                raise ValueError("a schedule either runs an agent or sends a message, not both")
            definition = await runnable_definition(db, user_id, definition_id)
            handler = {"agent": definition.id}
            input = checked_input(input)
        else:
            handler = notify_handler(db, notify_fields)
            input = None
        owner = db.get_one(User, user_id)
        if scheduled_time:
            if enabled is False:
                raise ValueError("a one-off can't be created paused")
            at = parse_wall_clock_utc(scheduled_time, owner.timezone)
            if at is None:
                raise ValueError("invalid datetime format for scheduled_time")
            timer = add_timer(
                db, user_id, at.replace(tzinfo=timezone.utc), handler,
                key.id if key else None, input=input,
            )
            return timer_payload(timer)
        config = CronConfig(cron=check_cron(cron_expression or ""), input=input or "")
        if enabled is not False:
            check_trigger_cap(db, user_id, exclude_id=None)
        trigger = AgentTrigger(
            id=next_trigger_id(db),
            user_id=user_id,
            kind=CRON,
            state={},
            handler=handler,
            definition_id=definition.id if definition else None,
            config=config.model_dump(),
            enabled=enabled is not False,
            api_key_id=key.id if key else None,
            via_api_key=key is not None,
        )
        trigger.name = trigger_name(db, user_id, name or f"schedule-{trigger.id}", trigger.id)
        if trigger.enabled:
            schedule_next(trigger, owner.timezone)
        db.add(trigger)
        db.flush()
        return trigger.serialize()


async def update(
    db: DBSession,
    user_id: int,
    key: APIKey | None,
    schedule_id: int,
    cron_expression: str | None,
    scheduled_time: str | None,
    definition_id: int | None,
    input: str | None,
    notify_fields: dict[str, str | None],
    name: str | None,
    enabled: bool | None,
) -> dict[str, Any]:
    trigger = owned_schedule(db, schedule_id, user_id)
    changes = (cron_expression, scheduled_time, definition_id, input, name, *notify_fields.values())
    # Pausing never re-checks the definition or access: it must work after they're lost.
    if enabled is False and not any(c is not None for c in changes):
        check_pause_binding(db, trigger, key)
        trigger.enabled, trigger.next_at = False, None
        db.flush()
        return trigger.serialize()
    check_binding(trigger.api_key_id, key)
    if scheduled_time is not None:
        raise ValueError("scheduled_time only creates a one-off; change a schedule's cron_expression")
    config = dict(trigger.config)
    if cron_expression is not None:
        config["cron"] = check_cron(cron_expression)
    if any(v is not None for v in notify_fields.values()):
        if definition_id is not None:
            raise ValueError("a schedule either runs an agent or sends a message, not both")
        merged = stored_notify_fields(trigger.handler) | {
            k: v for k, v in notify_fields.items() if v is not None
        }
        trigger.handler, trigger.definition_id = notify_handler(db, merged), None
        config.pop("input", None)
    elif definition_id is not None or "agent" in trigger.handler:
        definition = await runnable_definition(
            db, user_id, definition_id if definition_id is not None else trigger.handler["agent"]
        )
        trigger.handler, trigger.definition_id = {"agent": definition.id}, definition.id
        config["input"] = checked_input(input if input is not None else config.get("input"))
    elif input is not None:
        raise ValueError("input only applies to schedules that run an agent")
    trigger.config = CronConfig.model_validate(config).model_dump()
    if name is not None:
        trigger.name = trigger_name(db, user_id, name, trigger.id)
    was_enabled = trigger.enabled
    trigger.enabled = was_enabled if enabled is None else enabled
    if trigger.enabled and not was_enabled:
        check_trigger_cap(db, user_id, exclude_id=trigger.id)
        stale = {"tripped", "failures", THROTTLED, RESUME}
        trigger.state = {k: v for k, v in (trigger.state or {}).items() if k not in stale}
    if not trigger.enabled:
        trigger.next_at = None
    elif not was_enabled or cron_expression is not None:
        schedule_next(trigger, db.get_one(User, user_id).timezone)
    db.flush()
    return trigger.serialize()


@scheduler_mcp.tool(annotations=READ_ONLY)
@visible_when(require_scopes(SCOPE_SCHEDULE))
async def list_all(definition_id: int | None = None, include_fired: bool = False) -> dict[str, Any]:
    """Your recurring schedules and the timers still waiting to fire; with
    definition_id, only those that run that agent definition. include_fired adds
    `fired`: the latest FIRED_TIMERS_LISTED timers that ended, newest first, each
    with its `result` (sent, done with the run_id, failed with the error, or
    skipped:<reason>; `late_sec` when it went out late)."""
    user_id, _ = caller()
    with make_session() as db:
        query = db.query(AgentTrigger).filter(
            AgentTrigger.user_id == user_id, AgentTrigger.kind == CRON
        )
        timers = pending_timers(db, user_id)
        fired = fired_timers(db, user_id) if include_fired else []
        if definition_id is not None:
            query = query.filter(AgentTrigger.definition_id == definition_id)
            timers = [t for t in timers if (t.handler or {}).get("agent") == definition_id]
            fired = [t for t in fired if (t.handler or {}).get("agent") == definition_id]
        listed = {
            "schedules": [t.serialize() for t in query.order_by(AgentTrigger.id)],
            "timers": [timer_payload(t) for t in timers],
        }
        return listed | ({"fired": [timer_payload(t) for t in fired]} if include_fired else {})


@scheduler_mcp.tool()
@visible_when(require_scopes(SCOPE_SCHEDULE_WRITE))
async def delete(schedule_id: int | None = None, timer_id: int | None = None) -> dict[str, Any]:
    """Delete one of your recurring schedules (and its tick history), or cancel a timer."""
    if (schedule_id is None) == (timer_id is None):
        raise ValueError("give exactly one of schedule_id or timer_id")
    user_id, _ = caller()
    token, _ = caller_token()
    with make_session() as db:
        key = token_api_key(token, db)
        if schedule_id is not None:
            trigger = owned_schedule(db, schedule_id, user_id)
            check_binding(trigger.api_key_id, key)
            db.delete(trigger)
            return {"deleted": schedule_id}
        timer = db.get(Event, timer_id, with_for_update=True)
        if timer is None or timer.kind != TIMER or timer.owner_id != user_id:
            raise ValueError(f"unknown timer {timer_id}")
        if timer.routed_at is not None:
            raise ValueError(f"timer {timer_id} already fired")
        check_binding((timer.data or {}).get("api_key_id"), key)
        finish(timer, SKIPPED_CANCELLED)
        return {"cancelled": timer_id}


@scheduler_mcp.tool(annotations=READ_ONLY)
@visible_when(require_scopes(SCOPE_SCHEDULE))
async def history(schedule_id: int, limit: int = 10) -> list[dict[str, Any]]:
    """A recurring schedule's latest ticks: when each was due, how many it stood for
    that were skipped (`missed`), and its `result`: sent, done (with the run_id),
    failed (with the error) or skipped:<reason> (late, disabled, coalesced into a
    later tick, or a cap). A tick without a result hasn't been delivered yet."""
    user_id, _ = caller()
    with make_session() as db:
        owned_schedule(db, schedule_id, user_id)
        ticks = (
            db.query(Event)
            .filter(Event.kind == TICK, Event.trigger_id == schedule_id)
            .order_by(Event.fire_at.desc())
            .limit(max(1, min(limit, 100)))
            .all()
        )
        return [
            {
                "id": t.id,
                "time": t.fire_at.isoformat() if t.fire_at else None,
                "missed": (t.data or {}).get("missed", 0),
                "result": t.result,
            }
            for t in ticks
        ]
