"""MCP subserver for agent definitions and runs.

Every tool acts as the caller. A run always executes as the definition's owner
(only owners may run a definition), with the caller's API-key scopes when one
was used.
"""

import asyncio
import logging
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from memory.agents.caps import CapExceeded
from memory.agents.enqueue import SpecInvalid, TriggerInfo, enqueue_run
from memory.agents.mcp_client import memory_tools
from memory.agents.triggers import (
    INGEST,
    Batch,
    IngestConfig,
    buffer_client,
    buffer_key,
    source_types,
)
from memory.agents.spec import (
    DEFAULT_AGENT_MODEL,
    MAX_NAME_CHARS,
    MAX_TEXT_CHARS,
    AgentSpec,
    is_denied,
    spec_errors,
)
from memory.api.auth import lookup_api_key
from memory.api.MCP.access import get_mcp_current_user
from memory.api.MCP.servers.scheduler import (
    build_scheduled_task,
    check_active_task_cap,
    validate_cron_interval,
)
from memory.api.MCP.visibility import require_scopes, visible_when
from memory.common import settings
from memory.common.access_control import has_admin_scope
from memory.common.celery_app import app as celery_app
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import (
    AgentDefinition,
    AgentRun,
    AgentRunStep,
    AgentTrigger,
    ScheduledTask,
)
from memory.common.db.models.agents import TERMINAL_STATUSES, RunStatus
from memory.common.db.models.scheduled_tasks import TaskType, compute_next_cron
from memory.common.llms.neutral import BUILTIN_TOOLS, Capabilities
from memory.common.llms.registry import PROVIDERS, all_models, split_model
from memory.common.scopes import SCOPE_AGENTS

logger = logging.getLogger(__name__)

agent_mcp = FastMCP("memory-agent")


def caller_token() -> tuple[str, list[str]]:
    """The caller's token and scopes, for resolving tools as the caller."""
    token = get_access_token()
    if token is None:
        raise ValueError("Not authenticated")
    with make_session() as db:
        key = lookup_api_key(token.token, db)
        if key is not None and key.is_one_time:
            # The key was spent authenticating this call, so it can't be resolved again.
            raise ValueError(
                "one-time API keys can't manage agents; use a session or a regular API key"
            )
    return token.token, list(token.scopes or [])


def caller() -> tuple[int, bool]:
    caller_token()
    user = get_mcp_current_user()
    if user is None or user.id is None:
        raise ValueError("Not authenticated")
    return user.id, has_admin_scope(user)


def owned_definition(
    db: DBSession, definition_id: int, user_id: int
) -> AgentDefinition:
    definition = db.get(AgentDefinition, definition_id)
    if definition is None or definition.user_id != user_id:
        raise ValueError(f"unknown agent definition {definition_id}")
    return definition


def trigger_summaries(db: DBSession, definition_ids: list[int]) -> dict[int, dict]:
    rows = (
        db.query(
            AgentTrigger.definition_id,
            func.count(),
            func.count().filter(AgentTrigger.enabled),
            func.count().filter(AgentTrigger.state.has_key("tripped")),
        )
        .filter(AgentTrigger.definition_id.in_(definition_ids))
        .group_by(AgentTrigger.definition_id)
        .all()
    )
    found = {d: {"total": t, "enabled": e, "tripped": x} for d, t, e, x in rows}
    empty = {"total": 0, "enabled": 0, "tripped": 0}
    return {d: found.get(d, empty) for d in definition_ids}


def readable_run(
    db: DBSession, run_id: str, user_id: int, is_admin: bool, lock: bool = False
) -> AgentRun:
    run = db.get(AgentRun, run_id, with_for_update=lock)
    if run is None or (run.user_id != user_id and not is_admin):
        raise ValueError(f"unknown run {run_id}")
    return run


def parse_spec(spec: dict[str, Any]) -> AgentSpec:
    try:
        return AgentSpec.model_validate(spec)
    except ValidationError as e:
        raise ValueError(f"invalid spec: {e}") from e


async def invisible_tools(spec: AgentSpec) -> list[str]:
    names = [n for n in spec.tools if n not in BUILTIN_TOOLS]
    if not names:
        return []
    token, scopes = caller_token()
    async with memory_tools(token, scopes) as tools:
        visible = {t.name for t in await tools.list()}
    return [n for n in names if n not in visible]


async def check_spec(spec: AgentSpec) -> None:
    errors = spec_errors(spec) + [
        f"tool '{n}' is not visible to you (missing scope?)"
        for n in await invisible_tools(spec)
    ]
    if errors:
        raise ValueError("; ".join(errors))


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_AGENTS))
async def upsert(
    name: str, spec: dict, description: str = "", definition_id: int | None = None
) -> dict:
    """Create or update an agent definition.

    `spec` fields: system_prompt, model ("anthropic/claude-sonnet-5-5"), max_output_tokens
    (omit for the most each run can afford, up to 16000), reasoning (off|low|medium|high),
    temperature, provider_extras, tools (memory tool names and builtins web_search/web_fetch),
    mcp_servers ([{server_id, tools}]), finish ({"mode": "answer"} or
    {"mode": "tool", "tools": [...]}), result_schema, max_turns, max_cost_usd,
    max_cost_usd_per_day, timeout_sec. Errors name every problem at once.
    """
    user_id, _ = caller()
    name = name.strip()
    if not name:
        raise ValueError("name is required")
    if len(name) > MAX_NAME_CHARS:
        raise ValueError(f"name must be at most {MAX_NAME_CHARS} characters")
    parsed = parse_spec(spec)
    await check_spec(parsed)
    with make_session() as db:
        if definition_id is None:
            definition = AgentDefinition(
                user_id=user_id, name=name, description=description, spec={}
            )
            db.add(definition)
        else:
            definition = owned_definition(db, definition_id, user_id)
            definition.name, definition.description = name, description
        definition.spec = parsed.model_dump(mode="json")
        try:
            db.flush()
        except IntegrityError as e:
            raise ValueError(f"you already have an agent named {name!r}") from e
        return definition.serialize()


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_AGENTS))
async def delete(definition_id: int) -> dict:
    """Delete an agent definition and its schedules. Its past runs keep their frozen spec."""
    user_id, _ = caller()
    with make_session() as db:
        definition = owned_definition(db, definition_id, user_id)
        for task in definition_schedules(db, user_id, definition_id):
            db.delete(task)
        # Triggers go first, explicitly: fire_trigger locks a trigger before its
        # definition, and deleting in the same order can't deadlock against it.
        trigger_ids = [
            t.id
            for t in db.query(AgentTrigger)
            .filter(AgentTrigger.definition_id == definition_id)
            .with_for_update()
        ]
        db.query(AgentTrigger).filter(AgentTrigger.id.in_(trigger_ids)).delete()
        db.delete(definition)
    for trigger_id in trigger_ids:
        drop_buffer(trigger_id)
    return {"deleted": definition_id}


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_AGENTS))
async def list_all() -> dict:
    """Your agent definitions, each with its most recent run."""
    user_id, _ = caller()
    with make_session() as db:
        definitions = (
            db.query(AgentDefinition)
            .filter(AgentDefinition.user_id == user_id)
            .order_by(AgentDefinition.name)
            .all()
        )
        summaries = trigger_summaries(db, [d.id for d in definitions])
        result = []
        for d in definitions:
            last = (
                db.query(AgentRun)
                .filter(AgentRun.definition_id == d.id)
                .order_by(AgentRun.created_at.desc())
                .first()
            )
            result.append(
                d.serialize()
                | {
                    "last_run": last.serialize() if last else None,
                    "triggers": summaries[d.id],
                }
            )
    return {"definitions": result}


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_AGENTS))
async def fetch(definition_id: int) -> dict:
    """One of your agent definitions."""
    user_id, _ = caller()
    with make_session() as db:
        return owned_definition(db, definition_id, user_id).serialize()


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_AGENTS))
async def run(
    input: str, definition_id: int | None = None, spec: dict | None = None
) -> dict:
    """Start a run of one of your definitions, or of an ad-hoc spec. Returns at once; use agent_wait."""
    if (definition_id is None) == (spec is None):
        raise ValueError("pass exactly one of definition_id or spec")
    user_id, _ = caller()
    token, _ = caller_token()
    with make_session() as db:
        definition = (
            owned_definition(db, definition_id, user_id)
            if definition_id is not None
            else None
        )
        parsed = parse_spec(definition.spec if definition else spec or {})
        await check_spec(parsed)
        api_key = lookup_api_key(token, db)
        trigger = TriggerInfo(
            kind="mcp" if api_key else "manual",
            ref={"definition_id": definition_id} if definition_id is not None else {},
        )
        try:
            started = enqueue_run(
                db,
                user_id=user_id,
                spec=parsed,
                input=input,
                trigger=trigger,
                definition=definition,
                api_key=api_key,
            )
        except (SpecInvalid, CapExceeded) as e:
            raise ValueError(str(e)) from e
        return {"run_id": started.id, "status": started.status}


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_AGENTS))
async def wait(
    run_id: str, after_seq: int = -1, wait: int = 0, limit: int = 100
) -> dict:
    """A run plus up to `limit` of its transcript steps with seq > after_seq.

    With wait > 0, blocks (up to AGENT_MAX_WAIT_SEC) until new steps appear or the
    run finishes; call again with the last seq you saw to follow a live run.
    """
    user_id, is_admin = caller()
    deadline = time.monotonic() + min(max(wait, 0), settings.AGENT_MAX_WAIT_SEC)
    while True:
        with make_session() as db:
            found = readable_run(db, run_id, user_id, is_admin)
            steps = (
                db.query(AgentRunStep)
                .filter(AgentRunStep.run_id == run_id, AgentRunStep.seq > after_seq)
                .order_by(AgentRunStep.seq)
                .limit(min(max(limit, 1), 500))
                .all()
            )
            payload = {
                "run": found.serialize(include_spec=True),
                "steps": [s.serialize() for s in steps],
            }
            finished = found.status in TERMINAL_STATUSES
        if steps or finished or time.monotonic() >= deadline:
            return payload
        await asyncio.sleep(1)


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_AGENTS))
async def list_runs(
    definition_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
    before: str | None = None,
) -> dict:
    """Your runs, newest first. `before` is a created_at ISO timestamp for paging."""
    user_id, _ = caller()
    with make_session() as db:
        query = db.query(AgentRun).filter(AgentRun.user_id == user_id)
        if definition_id is not None:
            query = query.filter(AgentRun.definition_id == definition_id)
        if status is not None:
            query = query.filter(AgentRun.status == status)
        if before is not None:
            query = query.filter(AgentRun.created_at < datetime.fromisoformat(before))
        runs = (
            query.order_by(AgentRun.created_at.desc())
            .limit(min(max(limit, 1), 200))
            .all()
        )
        return {"runs": [r.serialize() for r in runs]}


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_AGENTS))
async def cancel(run_id: str) -> dict:
    """Cancel a run. Pending runs stop at once; running ones stop before their next model or tool call."""
    user_id, is_admin = caller()
    with make_session() as db:
        found = readable_run(db, run_id, user_id, is_admin, lock=True)
        now = datetime.now(timezone.utc)
        if found.status == RunStatus.PENDING:
            celery_app.control.revoke(run_id)
            # Also flagged, so a worker that already claimed it stops at its first check.
            found.cancel_requested_at = now
            found.status = RunStatus.CANCELLED.value
            found.error = "cancelled by user before it started"
            found.finished_at = now
        elif found.status not in TERMINAL_STATUSES:
            found.cancel_requested_at = now
        db.flush()
        return found.serialize()


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_AGENTS))
async def star(run_id: str, starred: bool) -> dict:
    """Star a run to keep its transcript past the retention window."""
    user_id, is_admin = caller()
    with make_session() as db:
        found = readable_run(db, run_id, user_id, is_admin)
        found.starred = starred
        db.flush()
        return found.serialize()


def capabilities_json(caps: Capabilities) -> dict:
    return asdict(caps) | {"reasoning_levels": sorted(caps.reasoning_levels)}


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_AGENTS))
async def catalog() -> dict:
    """What a definition can use: your visible tools, builtins, models and cost ceilings."""
    caller()
    token, scopes = caller_token()
    async with memory_tools(token, scopes) as tools:
        listed = await tools.list()
    return {
        "builtins": sorted(BUILTIN_TOOLS),
        "tools": [
            {"name": t.name, "description": t.description, "read_only": t.read_only}
            for t in listed
            if not is_denied(t.name)
        ],
        "models": [
            {
                "model": model,
                "capabilities": capabilities_json(info.capabilities),
                "extras_schema": PROVIDERS[
                    split_model(model)[0]
                ].Extras.model_json_schema(),
            }
            for model, info in sorted(all_models().items())
        ],
        "ceilings": {
            "max_run_cost_usd": str(settings.AGENT_MAX_RUN_COST_USD),
            "max_daily_cost_usd": str(settings.AGENT_MAX_DAILY_COST_USD),
            "max_timeout_sec": settings.AGENT_MAX_TIMEOUT_SEC,
            "max_runs_per_hour": settings.AGENT_MAX_RUNS_PER_HOUR,
        },
        "default_model": DEFAULT_AGENT_MODEL,
        "trigger_source_types": source_types(),
    }


def owned_trigger(db: DBSession, trigger_id: int, user_id: int) -> AgentTrigger:
    trigger = db.get(AgentTrigger, trigger_id)
    if trigger is None or trigger.definition.user_id != user_id:
        raise ValueError(f"unknown trigger {trigger_id}")
    return trigger


def check_trigger_cap(db: DBSession, user_id: int, exclude_id: int | None) -> None:
    query = (
        db.query(AgentTrigger)
        .join(AgentDefinition, AgentDefinition.id == AgentTrigger.definition_id)
        .filter(AgentDefinition.user_id == user_id, AgentTrigger.enabled)
    )
    if exclude_id is not None:
        query = query.filter(AgentTrigger.id != exclude_id)
    if query.count() >= settings.AGENT_MAX_TRIGGERS_PER_USER:
        raise ValueError(
            f"at most {settings.AGENT_MAX_TRIGGERS_PER_USER} of your triggers can be "
            "enabled at once (AGENT_MAX_TRIGGERS_PER_USER); pause or delete one first"
        )


async def exposure_warnings(spec: AgentSpec) -> list[str]:
    """Tools a trigger would hand to content nobody vetted: writes and egress."""
    egress = sorted(set(spec.tools) & BUILTIN_TOOLS)
    names = [n for n in spec.tools if n not in BUILTIN_TOOLS]
    writes = []
    if names:
        token, scopes = caller_token()
        async with memory_tools(token, scopes) as tools:
            read_only = {t.name for t in await tools.list() if t.read_only}
        writes = [n for n in names if n not in read_only]
    if not (egress or writes):
        return []
    return [
        "this trigger feeds external content to an agent that can "
        f"write or reach outside memory ({', '.join(writes + egress)}); "
        "content could steer those tools, so keep the caps tight"
    ]


def drop_buffer(trigger_id: int) -> None:
    """Forget batched matches, so a re-enabled trigger doesn't fire stale ones."""
    try:
        buffer_client().delete(buffer_key(trigger_id))
    except Exception:
        # The buffer expires on its own; this only speeds that up.
        logger.warning("could not clear the buffer of trigger %s", trigger_id)


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_AGENTS))
async def trigger_upsert(
    definition_id: int,
    config: dict | None = None,
    enabled: bool | None = None,
    trigger_id: int | None = None,
) -> dict:
    """Create or update an ingest trigger: run one of your definitions when newly stored items match.

    config: {"filter": {source_types, tags_any, channel_ids, text_contains, regex,
    exclude_authors}, "delivery": {"mode": "per_item"} or {"mode": "batch",
    "window_sec", "max_items"}, "template": the run's input, where "{items}" becomes
    one line per item with its id}. Set filter values must all hold; within a list,
    any value matches. Only items stored since it was last enabled match, each
    at most once, and only if you can read them.

    Loop protection: posts by our own Discord bots and by your Slack user never
    match. Anything else an agent writes that gets ingested again (email, GitHub,
    Slack posted with a bot token) is only stopped by the caps: a trigger that hits
    one (the definition's max_runs_per_hour or a daily cost pool) disables itself
    and notifies you; enabling it again clears that. The response's `warnings`
    lists the definition's tools that write or reach outside memory, which a
    trigger exposes to whatever content arrives.

    Runs act with the credentials used here (an API key's scopes, if you used one).
    At most AGENT_MAX_TRIGGERS_PER_USER of your triggers can be enabled at once.
    To pause, pass only trigger_id and enabled=false; that always works. Any other
    change (config is required to create; omitted on update, it's kept) re-checks
    the definition's tools against your access.
    """
    user_id, _ = caller()
    if trigger_id is not None and enabled is False and config is None:
        return pause_trigger(trigger_id, user_id)
    token, _ = caller_token()
    with make_session() as db:
        definition = owned_definition(db, definition_id, user_id)
        existing = owned_trigger(db, trigger_id, user_id) if trigger_id is not None else None
        if config is None and existing is None:
            raise ValueError("config is required to create a trigger")
        try:
            parsed = IngestConfig.model_validate(existing.config if config is None else config)
        except ValidationError as e:
            raise ValueError(f"invalid trigger config: {e}") from e
        spec = parse_spec(definition.spec)
        await check_spec(spec)
        key = lookup_api_key(token, db)
        was_enabled = existing is not None and existing.enabled
        will_be_enabled = was_enabled if enabled is None and existing else enabled is not False
        if will_be_enabled and not was_enabled:
            check_trigger_cap(db, user_id, exclude_id=trigger_id)
        was_batched = existing is not None and isinstance(
            IngestConfig.model_validate(existing.config).delivery, Batch
        )
        trigger = existing or AgentTrigger(kind=INGEST, state={})
        db.add(trigger)
        trigger.definition_id = definition.id
        trigger.config = parsed.model_dump(mode="json")
        trigger.api_key_id, trigger.via_api_key = (key.id, True) if key else (None, False)
        trigger.enabled = will_be_enabled
        if will_be_enabled and not was_enabled:
            # The database clock, which also stamps items' inserted_at.
            trigger.active_since = func.now()
            trigger.state = {k: v for k, v in (trigger.state or {}).items() if k != "tripped"}
        db.flush()
        stale_buffer = was_batched and (not will_be_enabled or parsed.delivery.mode != "batch")
        result = trigger.serialize() | {"warnings": await exposure_warnings(spec)}
    if stale_buffer:
        drop_buffer(result["id"])
    return result


def pause_trigger(trigger_id: int, user_id: int) -> dict:
    """Disabling never re-checks access: it must work even after access is lost."""
    with make_session() as db:
        trigger = owned_trigger(db, trigger_id, user_id)
        trigger.enabled = False
        db.flush()
        result = trigger.serialize()
    drop_buffer(trigger_id)
    return result


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_AGENTS))
async def triggers(definition_id: int) -> dict:
    """The triggers of one of your definitions, including any tripped breaker's reason."""
    user_id, _ = caller()
    with make_session() as db:
        owned_definition(db, definition_id, user_id)
        found = (
            db.query(AgentTrigger)
            .filter(AgentTrigger.definition_id == definition_id)
            .order_by(AgentTrigger.id)
            .all()
        )
        return {"triggers": [t.serialize() for t in found]}


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_AGENTS))
async def trigger_delete(trigger_id: int) -> dict:
    """Delete one of your triggers."""
    user_id, _ = caller()
    with make_session() as db:
        db.delete(owned_trigger(db, trigger_id, user_id))
    drop_buffer(trigger_id)
    return {"deleted": trigger_id}


def owned_schedule(db: DBSession, task_id: str, user_id: int) -> ScheduledTask:
    task = db.get(ScheduledTask, task_id)
    if task is None or task.user_id != user_id or task.task_type != TaskType.AGENT_RUN:
        raise ValueError(f"unknown schedule {task_id}")
    return task


def definition_schedules(
    db: DBSession, user_id: int, definition_id: int
) -> list[ScheduledTask]:
    tasks = (
        db.query(ScheduledTask)
        .filter(
            ScheduledTask.user_id == user_id,
            ScheduledTask.task_type == TaskType.AGENT_RUN.value,
        )
        .order_by(ScheduledTask.created_at)
        .all()
    )
    return [t for t in tasks if (t.data or {}).get("definition_id") == definition_id]


def checked_input(input: str | None) -> str | None:
    if input is None:
        return None
    if not input.strip():
        raise ValueError("input is required")
    if len(input) > MAX_TEXT_CHARS:
        raise ValueError(f"input is longer than {MAX_TEXT_CHARS} characters")
    return input


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_AGENTS))
async def schedule(
    definition_id: int,
    input: str | None = None,
    cron_expression: str | None = None,
    scheduled_time: str | None = None,
    enabled: bool | None = None,
    task_id: str | None = None,
) -> dict:
    """Run one of your definitions on a schedule, with a fixed input.

    Creating needs input and either a 5-field UTC cron_expression (recurring) or
    an ISO scheduled_time (once). With task_id, updates that schedule; omitted
    fields keep their values. Each create or update re-checks the definition's
    tools against your access now, and later runs act with these credentials
    (an API key's scopes, if you used one). Schedules count toward your
    scheduled-task limit.
    """
    user_id, _ = caller()
    token, _ = caller_token()
    input = checked_input(input)
    with make_session() as db:
        definition = owned_definition(db, definition_id, user_id)
        await check_spec(parse_spec(definition.spec))
        key = lookup_api_key(token, db)
        stamp = {"definition_id": definition.id, "api_key_id": key.id if key else None}
        topic = f"agent: {definition.name}"
        if task_id is None:
            if input is None:
                raise ValueError("input is required")
            task = build_scheduled_task(
                db,
                user_id,
                task_id=None,
                task_type=TaskType.AGENT_RUN.value,
                enabled=enabled,
                cron_expression=cron_expression,
                scheduled_time=scheduled_time,
                topic=topic,
                message=None,
                notification_channel=None,
                notification_target=None,
                spawn_config=None,
                agent_run=stamp | {"input": input},
            )
            db.commit()
            return task.serialize()

        task = owned_schedule(db, task_id, user_id)
        if scheduled_time is not None:
            raise ValueError("scheduled_time can only be set when creating a schedule")
        was_active = task.enabled
        task.data = stamp | {"input": input or (task.data or {}).get("input", "")}
        task.topic = topic
        if cron_expression is not None:
            validate_cron_interval(cron_expression)
            task.cron_expression = cron_expression
            task.next_scheduled_time = compute_next_cron(cron_expression)
        if enabled is not None:
            task.enabled = enabled
        if task.enabled and not was_active:
            check_active_task_cap(db, user_id, exclude_id=task.id)
        db.commit()
        return task.serialize()


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_AGENTS))
async def schedules(definition_id: int) -> dict:
    """The schedules that run one of your definitions."""
    user_id, _ = caller()
    with make_session() as db:
        owned_definition(db, definition_id, user_id)
        return {
            "schedules": [
                t.serialize() for t in definition_schedules(db, user_id, definition_id)
            ]
        }


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_AGENTS))
async def unschedule(task_id: str) -> dict:
    """Delete one of your agent schedules and its execution history."""
    user_id, _ = caller()
    with make_session() as db:
        db.delete(owned_schedule(db, task_id, user_id))
    return {"deleted": task_id}
