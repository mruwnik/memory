"""MCP subserver for subscriptions: a source (items that match a filter, a cron
expression, or a one-off time) handed to a handler (an agent, a queue your workers
drain, or a message)."""

import re
from datetime import timezone
from typing import Any, Literal

from fastmcp import FastMCP
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

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
from memory.agents.prebuilt import code_agent
from memory.agents.spec import MAX_NAME_CHARS, MAX_TEXT_CHARS, AgentSpec
from memory.agents.trigger_input import check_template
from memory.agents.triggers import INGEST, RESUME, THROTTLED, IngestConfig, PerItem, resume_from_trip
from memory.api.auth import token_api_key
from memory.api.MCP.access import get_mcp_current_user
from memory.api.MCP.servers.agent import (
    caller,
    caller_token,
    check_spec,
    exposure_warnings,
    owned_definition,
    parse_spec,
)
from memory.api.MCP.servers.notification_targets import resolve_and_validate_target
from memory.api.MCP.visibility import READ_ONLY, require_scopes, visible_when
from memory.common import settings
from memory.common.dates import parse_wall_clock_utc
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import AgentDefinition, AgentRun, AgentTrigger, AgentTriggerMatch, APIKey, Event, User
from memory.common.db.models.agents import iso
from memory.common.db.models.events import TICK, TIMER
from memory.common.scopes import (
    SCOPE_ADMIN,
    SCOPE_AGENTS,
    SCOPE_AGENTS_WORK,
    SCOPE_READ,
    SCOPE_SCHEDULE,
    SCOPE_SCHEDULE_WRITE,
)

subscription_mcp = FastMCP("memory-subscription")

WRITERS = (SCOPE_AGENTS, SCOPE_AGENTS_WORK, SCOPE_SCHEDULE_WRITE)
READERS = (*WRITERS, SCOPE_SCHEDULE)
# Default names carry the subscription's id, so no other subscription may take one.
DEFAULT_NAME = re.compile(r"(subscription|trigger|schedule)-\d+")
SOURCE_KINDS = {"filter": INGEST, "cron": CRON, "at": TIMER}
NOTIFY_FIELDS = ("channel", "target", "message", "subject")
FIRED_TIMERS_LISTED = 50
MAX_HISTORY = 100


def need(scopes: set[str], what: str, *allowed: str) -> None:
    if not {SCOPE_ADMIN, *allowed} & scopes:
        raise ValueError(f"{what} needs the '{allowed[0]}' scope")


def reads_filters(scopes: set[str]) -> bool:
    """Filter subscriptions say what is read and who works on it: schedule keys see
    only cron subscriptions."""
    return bool({SCOPE_ADMIN, SCOPE_AGENTS} & scopes) or {SCOPE_AGENTS_WORK, SCOPE_READ} <= scopes


def check_binding(bound_key_id: int | None, key: APIKey | None) -> None:
    """An API key may change only what it set up."""
    if key is not None and bound_key_id != key.id:
        raise ValueError("this subscription was set up through another credential")


def check_pause_binding(db: DBSession, trigger: AgentTrigger, key: APIKey | None) -> None:
    """Pausing needs the binding too, unless the key it is bound to no longer
    works: then anyone who owns it may pause it."""
    bound = db.get(APIKey, trigger.api_key_id) if trigger.api_key_id is not None else None
    if trigger.via_api_key and (bound is None or not bound.is_valid()):
        return
    check_binding(trigger.api_key_id, key)


def owned(db: DBSession, subscription_id: int, user_id: int) -> AgentTrigger:
    """One of the caller's subscriptions that their credential may see."""
    trigger = db.get(AgentTrigger, subscription_id)
    if (
        trigger is None
        or trigger.user_id != user_id
        or trigger.kind != CRON and not reads_filters(set(caller_token()[1]))
    ):
        raise ValueError(f"unknown subscription {subscription_id}")
    return trigger


def find(db: DBSession, user_id: int, subscription_id: int | None, name: str | None) -> AgentTrigger | None:
    if subscription_id is not None:
        return owned(db, subscription_id, user_id)
    if name is None:
        return None
    named = select(AgentTrigger.id).where(AgentTrigger.user_id == user_id, AgentTrigger.name == name.strip())
    found = db.scalar(named)
    return None if found is None else owned(db, found, user_id)


def checked_name(db: DBSession, user_id: int, name: str, trigger_id: int) -> str:
    """`name` stripped, refused if empty, another subscription's default or already
    another of the user's subscriptions'."""
    name = name.strip()
    if not name:
        raise ValueError("name must not be empty")
    if len(name) > MAX_NAME_CHARS:
        raise ValueError(f"name is longer than {MAX_NAME_CHARS} characters")
    if DEFAULT_NAME.fullmatch(name) and not name.endswith(f"-{trigger_id}"):
        raise ValueError('names like "subscription-<number>" are reserved for default names')
    taken = select(AgentTrigger.id).where(
        AgentTrigger.user_id == user_id, AgentTrigger.name == name, AgentTrigger.id != trigger_id
    )
    if db.scalar(select(taken.exists())):
        raise ValueError(f"you already have a subscription named '{name}'")
    return name


def check_cap(db: DBSession, user_id: int, exclude_id: int) -> None:
    query = select(func.count()).where(
        AgentTrigger.user_id == user_id, AgentTrigger.enabled, AgentTrigger.id != exclude_id
    )
    if db.execute(query).scalar_one() >= settings.AGENT_MAX_TRIGGERS_PER_USER:
        raise ValueError(
            f"at most {settings.AGENT_MAX_TRIGGERS_PER_USER} of your subscriptions can be "
            "enabled at once (AGENT_MAX_TRIGGERS_PER_USER); pause or delete one first"
        )


def source_kind(source: dict[str, Any]) -> str:
    found = [SOURCE_KINDS[k] for k in SOURCE_KINDS if k in source]
    if len(found) != 1:
        raise ValueError('source takes exactly one of "filter", "cron" or "at"')
    return found[0]


def one_handler(handler: dict[str, Any]) -> tuple[str, Any]:
    if len(handler) != 1 or next(iter(handler)) not in ("agent", "queue", "notify"):
        raise ValueError('handler takes exactly one of "agent", "queue" or "notify"')
    return next(iter(handler.items()))


def notify_handler(db: DBSession, fields: dict[str, Any]) -> dict[str, Any]:
    # Notify's other fields pick the sending bot or address, which no caller may choose.
    if set(fields) - set(NOTIFY_FIELDS):
        raise ValueError("a notify handler takes only channel, target, message and subject")
    channel, target, message = (fields.get(f) for f in ("channel", "target", "message"))
    if not (channel and target and message):
        raise ValueError("a notify handler needs channel, target and message")
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of: {', '.join(CHANNELS)}")
    user = get_mcp_current_user(db)
    if user is None:
        raise ValueError("Not authenticated")
    try:
        notify = Notify.model_validate(fields | {"target": resolve_and_validate_target(db, user, channel, target)})
    except ValidationError as e:
        raise ValueError(f"invalid notify handler: {e}") from e
    return {"notify": notify.model_dump(exclude_none=True)}


async def checked_handler(
    db: DBSession, user_id: int, scopes: set[str], handler: dict[str, Any]
) -> tuple[dict[str, Any], AgentSpec | None, AgentDefinition | None]:
    """The handler as stored, with the spec and definition an agent handler runs;
    refused unless the caller's credential may use it."""
    kind, value = one_handler(handler)
    if kind == "queue":
        need(scopes, "a queue handler", SCOPE_AGENTS_WORK, SCOPE_AGENTS)
        need(scopes, "a queue handler, which hands items over,", SCOPE_READ)
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_NAME_CHARS:
            raise ValueError(f"a queue name is 1 to {MAX_NAME_CHARS} characters")
        return {"queue": value.strip()}, None, None
    if kind == "notify":
        need(scopes, "a notify handler", SCOPE_SCHEDULE_WRITE)
        if not isinstance(value, dict):
            raise ValueError("a notify handler is {channel, target, message, subject}")
        return notify_handler(db, value), None, None
    need(scopes, "an agent handler", SCOPE_AGENTS)
    if isinstance(value, str):
        spec, definition = code_agent(db, user_id, value), None
    elif isinstance(value, int) and not isinstance(value, bool):
        definition = owned_definition(db, value, user_id)
        spec = parse_spec(definition.spec)
    else:
        raise ValueError("an agent handler names a definition id or an agent's slug")
    await check_spec(spec)
    return {"agent": value}, spec, definition


def checked_input(handler: dict[str, Any], input: str | None) -> str:
    if "agent" not in handler:
        if input:
            raise ValueError("input only applies to agent handlers")
        return ""
    if input is None or not input.strip():
        raise ValueError("input is required to run an agent")
    if len(input) > MAX_TEXT_CHARS:
        raise ValueError(f"input is longer than {MAX_TEXT_CHARS} characters")
    return input


def check_combination(kind: str, handler: dict[str, Any], shaping: dict[str, Any]) -> None:
    if "notify" in handler and kind == INGEST:
        raise ValueError("notify handlers take cron and timer sources")
    if "queue" in handler and kind != INGEST:
        raise ValueError("queue handlers take filter sources")
    given = sorted(k for k, v in shaping.items() if v is not None)
    if kind != INGEST and given:
        raise ValueError(f"{', '.join(given)} apply to filter sources")
    if "agent" in handler and shaping["order_by"] not in (None, "none"):
        raise ValueError("order_by applies to queue handlers: agent deliveries aren't ordered")
    for field in ("max_per_hour", "backlog_max"):
        if shaping[field] is not None and shaping[field] < 1:
            raise ValueError(f"{field} must be at least 1")


def checked_config(kind: str, config: dict[str, Any], handler: dict[str, Any], cron_changed: bool) -> dict[str, Any]:
    if kind == CRON:
        cron = check_cron(config["cron"]) if cron_changed else config["cron"]
        try:
            parsed = CronConfig(cron=cron, input=checked_input(handler, config.get("input")))
        except ValidationError as e:
            raise ValueError(f"invalid cron source: {e}") from e
        return parsed.model_dump()
    try:
        ingest = IngestConfig.model_validate(config)
    except ValidationError as e:
        raise ValueError(f"invalid filter source: {e}") from e
    check_template(ingest.template, ingest.filter.source_types, isinstance(ingest.delivery, PerItem))
    return ingest.model_dump(mode="json")


def merged_source(stored: dict[str, Any], given: dict[str, Any], handler: dict[str, Any]) -> dict[str, Any]:
    """`given` over the stored source, a filter key by key, since a dropped key would
    widen what matches. Only an agent runs with an input."""
    merged = stored | given
    if isinstance(stored.get("filter"), dict) and isinstance(given.get("filter"), dict):
        merged["filter"] = stored["filter"] | given["filter"]
    if "agent" not in handler and "input" not in given:
        merged.pop("input", None)
    return merged


def timer_payload(timer: Event) -> dict[str, Any]:
    return timer.serialize_timer() | {"kind": TIMER}


def add_one_off(
    db: DBSession, user_id: int, key: APIKey | None, handler: dict[str, Any], source: dict[str, Any]
) -> dict[str, Any]:
    if set(source) - {"at", "input"}:
        raise ValueError('a timer\'s source is {"at", "input"}')
    at = parse_wall_clock_utc(source["at"], db.get_one(User, user_id).timezone)
    if at is None:
        raise ValueError("invalid datetime format for at")
    input = checked_input(handler, source.get("input")) or None
    timer = add_timer(db, user_id, at.replace(tzinfo=timezone.utc), handler, key.id if key else None, input=input)
    return timer_payload(timer)


def new_subscription(db: DBSession, user_id: int, kind: str, key: APIKey | None) -> AgentTrigger:
    # The id is drawn before the insert, so the default name can carry it.
    sequence = func.pg_get_serial_sequence("agent_triggers", "id")
    return AgentTrigger(
        id=db.execute(select(func.nextval(sequence))).scalar_one(),
        user_id=user_id,
        kind=kind,
        state={},
        api_key_id=key.id if key else None,
        via_api_key=key is not None,
    )


def apply_enabled(
    db: DBSession, trigger: AgentTrigger, was_enabled: bool, resume: bool, cron_changed: bool
) -> None:
    """Start a subscription being enabled afresh, or, with `resume`, from when it
    tripped; point an enabled cron at its next tick when it starts or changes."""
    if trigger.enabled and not was_enabled:
        check_cap(db, trigger.user_id, exclude_id=trigger.id)
        if resume:
            resume_from_trip(db, trigger)
        elif trigger.kind == INGEST:
            # The database clock, which also stamps items' inserted_at.
            trigger.active_since = func.now()
        # A replay left from an earlier resume would deliver what "from now" skips.
        stale = {"tripped", "failures", THROTTLED} | (set() if resume else {RESUME})
        trigger.state = {k: v for k, v in (trigger.state or {}).items() if k not in stale}
    if trigger.kind != CRON:
        return
    if not trigger.enabled:
        trigger.next_at = None
    elif not was_enabled or cron_changed:
        schedule_next(trigger, db.get_one(User, trigger.user_id).timezone)


@subscription_mcp.tool()
@visible_when(require_scopes(*WRITERS))
async def upsert(
    name: str | None = None,
    handler: dict | None = None,
    source: dict | None = None,
    subscription_id: int | None = None,
    enabled: bool | None = None,
    resume: bool = False,
    max_per_hour: int | None = None,
    backlog_max: int | None = None,
    coalesce: Literal["none", "latest"] | None = None,
    order_by: Literal["item", "thread", "none"] | None = None,
) -> dict:
    """Create or update a subscription: hand what its source produces to its handler.

    Subscribing again with the same name updates that subscription in place (it
    never re-enables one that tripped, nor moves when it started matching), so a
    worker can subscribe itself on every start. subscription_id names one to update
    or rename. On update, omitted arguments keep their values, a `source` given in
    part is merged into the stored one (a filter field by field: give a field its
    empty value, [], false or null, to clear it), and so is a notify handler's. A filter subscription's
    handler stays an agent or a queue; a cron's may move between agent and notify.

    handler, exactly one of:
    - {"agent": <definition id>} or {"agent": "<slug>"} (a prebuilt agent, see
      agent_catalog, or a system agent, "system:..."): starts a run per delivery,
      paid with your key. Needs the 'agents' scope, and on a cron or timer also
      'schedule:write'; the agent's tools are checked against your access on every
      change.
    - {"queue": "<name>"}: deliveries wait until one of your workers claims them
      with agent_work_claim(queue=...). Needs 'agents:work' (or 'agents') and 'read'.
    - {"notify": {"channel": discord|slack|email, "target", "message", "subject"}}:
      sends that message. The target may be a person's name, identifier or email,
      a Discord/Slack channel or user id, or an email address. Needs 'schedule:write'.

    source, exactly one of:
    - {"filter": {...}, "delivery", "template", "on_edit", "on_changes"}: items
      stored from now on that match (agent and queue handlers). filter fields:
      source_types, tags_any, channel_ids, exclude_channel_ids, text_contains, regex,
      authors_any, exclude_authors, known_people_only, changed_by_any,
      exclude_changed_by, exclude_own, repos, mentions_me, threads_im_in, dms_to_me;
      set fields must all hold, and within a list any value matches. delivery:
      {"mode": "per_item"} (default) or {"mode": "batch", "window_sec", "max_items"}.
      template: the delivery's text, with {items}, {items_json} and, per item,
      {item.<field>} (an unknown field is refused, listing the valid ones); text people
      wrote arrives inside <untrusted_item> delimiters. on_edit: an item already
      delivered is delivered again when it changes; on_changes limits that to edits
      reporting one of: edited, commits, comments, review, checks, checks_complete,
      state, labels, assignees, draft, title, due, priority. An item stored before
      the subscription matches once its sync reports news about it (commits,
      comments, review, checks_complete or state). Our own bots' posts, your own
      Slack messages (except for queue handlers; set filter.exclude_own) and items
      the subscription's runs reported posting never match.
    - {"cron": "<5 fields>", "input"}: a tick each time it is due, in your
      timezone (meta_get_user), at most every MIN_CRON_INTERVAL_MINUTES (agent and
      notify handlers). An agent's input is required; its run's trigger_ref has the
      tick's `time`, `since` (the last delivered tick) and `missed` (skipped ticks it
      stands for; a tick more than 6 hours late doesn't run).
    - {"at": "<ISO datetime>", "input"}: a timer firing once (agent and notify
      handlers), in your timezone unless it carries an offset. Timers have no name
      and can't be updated; cancel one with subscription_delete. Counts toward
      MAX_TIMERS_PER_USER. Returns the timer (kind "timer").

    Filter sources also take: max_per_hour (deliveries an hour; default
    AGENT_MAX_RUNS_PER_HOUR), backlog_max (waiting deliveries beyond which the
    oldest are shed), coalesce ("latest": a new event for an item, or its order_by
    key, replaces its waiting delivery), and for queue handlers order_by ("item",
    the default, "thread" or "none": a later delivery with the same key waits for
    the earlier one to end). Over a cap, deliveries wait an hour and are then shed;
    you're notified once per episode.

    Subscriptions act with the credential that created them (an API key's scopes,
    if one was used), for good; an API key can change only what it created. One
    whose credentials or handler stop working disables itself and notifies you;
    enable it again with enabled=true, adding resume=true (filter sources) to also
    get what it missed while disabled, as far back as events are kept. To pause,
    pass only the subscription and enabled=false; that always works. At most
    AGENT_MAX_TRIGGERS_PER_USER subscriptions can be enabled at once. The response's
    `warnings` lists an agent's tools that write or reach outside memory, which a
    filter subscription exposes to whatever content arrives.
    """
    user_id, _ = caller()
    token, scopes = caller_token()
    shaping = dict(max_per_hour=max_per_hour, backlog_max=backlog_max, coalesce=coalesce, order_by=order_by)
    with make_session() as db:
        key = token_api_key(token, db)
        existing = find(db, user_id, subscription_id, name)
        renaming = existing is not None and subscription_id is not None and name is not None
        if resume and enabled is not True:
            raise ValueError("resume only applies when enabling a tripped subscription: pass enabled=true")
        changes = (handler, source, *shaping.values())
        # Pausing never re-checks the handler or access: it must work after they're lost.
        if existing is not None and enabled is False and not renaming and all(c is None for c in changes):
            check_pause_binding(db, existing, key)
            existing.enabled, existing.next_at = False, None
            db.flush()
            return existing.serialize()
        if existing is not None:
            check_binding(existing.api_key_id, key)
            kind = existing.kind
            if source is not None and set(source) & set(SOURCE_KINDS) and source_kind(source) != kind:
                raise ValueError("a subscription's source kind can't change: delete it and subscribe again")
            # Older notify handlers may carry fields no caller may set: they're dropped.
            if "notify" in existing.handler:
                kept = {k: v for k, v in existing.handler["notify"].items() if k in NOTIFY_FIELDS}
                stored_handler = {"notify": kept}
            else:
                stored_handler = existing.handler
            if handler is None:
                handler = stored_handler
            elif kind == INGEST and set(handler) != set(existing.handler):
                # Its waiting deliveries are shaped for the handler kind they were routed to.
                raise ValueError("a filter subscription's handler kind can't change: delete it and subscribe again")
            elif isinstance(handler.get("notify"), dict) and "notify" in stored_handler:
                handler = {"notify": stored_handler["notify"] | handler["notify"]}
        elif handler is None:
            raise ValueError("handler is required to create a subscription")
        elif source is None:
            raise ValueError("source is required to create a subscription")
        else:
            kind = source_kind(source)
        stored, spec, definition = await checked_handler(db, user_id, set(scopes), handler)
        if kind != INGEST and "agent" in stored:
            need(set(scopes), "an agent on a schedule", SCOPE_SCHEDULE_WRITE)
        check_combination(kind, stored, shaping)
        if resume and kind != INGEST:
            raise ValueError("resume applies to filter sources")
        if kind == TIMER:
            if enabled is False:
                raise ValueError("a timer can't be created paused")
            if name is not None:
                raise ValueError("timers have no name")
            return add_one_off(db, user_id, key, stored, source or {})
        stored_config = existing.config if existing else {}
        cron_changed = existing is None or (source or {}).get("cron", stored_config.get("cron")) != stored_config.get("cron")
        config = checked_config(kind, merged_source(stored_config, source or {}, stored), stored, cron_changed)
        was_enabled = existing is not None and existing.enabled
        trigger = existing or new_subscription(db, user_id, kind, key)
        trigger.enabled = was_enabled if enabled is None and existing else enabled is not False
        if existing is None or renaming:
            trigger.name = checked_name(db, user_id, name or f"subscription-{trigger.id}", trigger.id)
        trigger.handler, trigger.config = stored, config
        trigger.definition_id = definition.id if definition else None
        if max_per_hour is not None:
            trigger.max_per_hour = max_per_hour
        if backlog_max is not None:
            trigger.backlog_max = backlog_max
        if coalesce is not None:
            trigger.coalesce_mode = coalesce
        if order_by is not None:
            trigger.order_by = order_by
        elif existing is None and "queue" in stored:
            trigger.order_by = "item"
        db.add(trigger)
        apply_enabled(db, trigger, was_enabled, resume, cron_changed)
        try:
            db.flush()
        except IntegrityError as e:
            if "agent_triggers_user_name_key" not in str(e.orig):
                raise
            raise ValueError(f"you already have a subscription named '{trigger.name}'") from e
        warnings = await exposure_warnings(spec) if spec is not None and kind == INGEST else []
        return trigger.serialize() | {"warnings": warnings}


@subscription_mcp.tool(annotations=READ_ONLY)
@visible_when(require_scopes(*READERS))
async def list_all(
    definition_id: int | None = None, queue: str | None = None, include_fired: bool = False
) -> dict:
    """Your subscriptions and the timers still waiting to fire; with definition_id or
    queue, only those handed to that definition or queue. include_fired adds `fired`:
    the latest FIRED_TIMERS_LISTED timers that ended, newest first, each with its
    `result` (sent, done with the run_id, failed with the error, or skipped:<reason>;
    `late_sec` when it went out late). A tripped subscription's state says why.
    Filter subscriptions are listed only for 'agents', or 'agents:work' with 'read'."""
    user_id, _ = caller()
    with make_session() as db:
        query = select(AgentTrigger).where(AgentTrigger.user_id == user_id)
        if not reads_filters(set(caller_token()[1])):
            query = query.where(AgentTrigger.kind == CRON)
        if definition_id is not None:
            query = query.where(AgentTrigger.definition_id == definition_id)
        if queue is not None:
            query = query.where(AgentTrigger.handler["queue"].astext == queue)
        timers = [] if queue is not None else pending_timers(db, user_id)
        fired: list[Event] = []
        if include_fired and queue is None:
            fired = list(
                db.scalars(
                    select(Event)
                    .where(Event.kind == TIMER, Event.owner_id == user_id, Event.routed_at.is_not(None))
                    .order_by(Event.routed_at.desc(), Event.id.desc())
                    .limit(FIRED_TIMERS_LISTED)
                )
            )
        if definition_id is not None:
            timers = [t for t in timers if (t.handler or {}).get("agent") == definition_id]
            fired = [t for t in fired if (t.handler or {}).get("agent") == definition_id]
        listed = {
            "subscriptions": [t.serialize() for t in db.scalars(query.order_by(AgentTrigger.id))],
            "timers": [timer_payload(t) for t in timers],
        }
        return listed | ({"fired": [timer_payload(t) for t in fired]} if include_fired else {})


def delivery(match: AgentTriggerMatch) -> dict[str, Any]:
    return {
        "item_id": match.item_id,
        "status": match.status,
        "changes": match.changes,
        "coalesced": match.coalesced,
        "created_at": iso(match.created_at),
        "not_before": iso(match.not_before),
        "error": match.error,
        "run_id": match.run_id,
    }


def tick(event: Event) -> dict[str, Any]:
    return {
        "event_id": event.id,
        "time": iso(event.fire_at),
        "missed": (event.data or {}).get("missed", 0),
        "result": event.result,
    }


def run_summary(run: AgentRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "status": run.status,
        "created_at": iso(run.created_at),
        "finished_at": iso(run.finished_at),
        "error": run.error,
        "trigger_ref": run.trigger_ref,
    }


@subscription_mcp.tool(annotations=READ_ONLY)
@visible_when(require_scopes(*READERS))
async def fetch(subscription_id: int, item_id: int | None = None, limit: int = 20) -> dict:
    """One of your subscriptions with its history, newest first; with item_id, only
    that item's. `deliveries`: a filter source's, each with its status (pending,
    done, failed, expired or skipped:<reason>), changes and run_id (none while
    pending), or a cron source's ticks with `missed` and their `result` (sent, done
    with the run_id, failed with the error, or skipped:<reason>; none until
    delivered). `runs`: the runs it started or its workers claimed, with their status.
    """
    user_id, _ = caller()
    limit = max(1, min(limit, MAX_HISTORY))
    with make_session() as db:
        trigger = owned(db, subscription_id, user_id)
        if trigger.kind == CRON:
            ticks = select(Event).where(Event.kind == TICK, Event.trigger_id == trigger.id)
            deliveries = [tick(e) for e in db.scalars(ticks.order_by(Event.fire_at.desc()).limit(limit))]
        else:
            matches = select(AgentTriggerMatch).where(AgentTriggerMatch.trigger_id == trigger.id)
            if item_id is not None:
                matches = matches.where(AgentTriggerMatch.item_id == item_id)
            ordered = matches.order_by(AgentTriggerMatch.created_at.desc(), AgentTriggerMatch.item_id.desc())
            deliveries = [delivery(m) for m in db.scalars(ordered.limit(limit))]
        runs = select(AgentRun).where(AgentRun.trigger_id == trigger.id, AgentRun.user_id == user_id)
        if item_id is not None:
            runs = runs.where(AgentRun.trigger_ref["item_ids"].contains([item_id]))
        ordered_runs = runs.order_by(AgentRun.created_at.desc(), AgentRun.id.desc()).limit(limit)
        return {
            "subscription": trigger.serialize(),
            "deliveries": deliveries,
            "runs": [run_summary(r) for r in db.scalars(ordered_runs)],
        }


@subscription_mcp.tool()
@visible_when(require_scopes(*WRITERS))
async def delete(subscription_id: int | None = None, timer_id: int | None = None) -> dict:
    """Delete one of your subscriptions (with its waiting deliveries and tick
    history), or cancel a timer that hasn't fired."""
    if (subscription_id is None) == (timer_id is None):
        raise ValueError("give exactly one of subscription_id or timer_id")
    user_id, _ = caller()
    token, _ = caller_token()
    with make_session() as db:
        key = token_api_key(token, db)
        if subscription_id is not None:
            trigger = owned(db, subscription_id, user_id)
            check_binding(trigger.api_key_id, key)
            db.delete(trigger)
            return {"deleted": subscription_id}
        owned_timer = select(Event).where(Event.id == timer_id, Event.kind == TIMER, Event.owner_id == user_id)
        timer = db.scalar(owned_timer.with_for_update())
        if timer is None:
            raise ValueError(f"unknown timer {timer_id}")
        if timer.routed_at is not None:
            raise ValueError(f"timer {timer_id} already fired")
        check_binding((timer.data or {}).get("api_key_id"), key)
        finish(timer, SKIPPED_CANCELLED)
        return {"cancelled": timer_id}
