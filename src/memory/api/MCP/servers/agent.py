"""MCP subserver for agent definitions and runs.

Every tool acts as the caller. A run always executes as the definition's owner
(only owners may run a definition), with the caller's API-key scopes when one
was used. A run that calls agent_run, agent_wait or agent_cancel (opted in
through its spec's tools) acts on its own children only. Runs placed on workers
are claimed and answered (agent_work_*) by anyone with the agents:work scope, as
themselves; the run stays its owner's.
"""

import asyncio
import json
import logging
import secrets
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, ValidationError, WithJsonSchema
from sqlalchemy import and_, func, or_, tuple_
from sqlalchemy.exc import IntegrityError

from memory.agents.cancel import cancel_tree
from memory.agents.clock import SKIPPED_CANCELLED, finish
from memory.agents.caps import CapExceeded
from memory.agents.enqueue import (
    SpecInvalid,
    TriggerInfo,
    enqueue_run,
    stored_spec_problems,
)
from memory.agents.external import (
    STATUS_COLUMNS,
    TOKEN_COLUMNS,
    TOOL_COLUMNS,
    AuthInput,
    authorize,
    configure,
    load_detached,
    refresh_tool_cache,
    save,
)
from memory.agents.mcp_client import calling_run, memory_tools
from memory.agents.prebuilt import PREBUILT_AGENTS, code_agent
from memory.agents.queues import backlog, claimable_queues, is_delivery, ready_to_claim, take_from_queue
from memory.agents.provider_keys import (
    KeyUnavailable,
    check_key,
    choose_key,
    resolve_key_ref,
    system_key,
)
from memory.agents.spec import (
    DEFAULT_AGENT_MODEL,
    MAX_DECLARED,
    MAX_GLOB_CHARS,
    MAX_NAME_CHARS,
    AgentSpec,
    Declared,
    Placement,
    Requirements,
    external_tool_name,
    memory_tool_denied,
    result_errors,
    satisfies,
    spec_errors,
    uses_server,
)
from memory.agents.system import SYSTEM_AGENTS, SystemAgent
from memory.agents.subscriptions import (
    INGEST,
    POSTED,
    can_read,
    source_types,
)
from memory.agents.work import HELD_OUT
from memory.api.auth import token_api_key
from memory.api.MCP.access import get_mcp_current_user
from memory.api.MCP.visibility import READ_ONLY, require_scopes, visible_when
from memory.common import settings
from memory.common.access_control import has_admin_scope
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import (
    AgentDefinition,
    AgentRun,
    AgentRunStep,
    Subscription,
    ExternalMCPServer,
    LLMProviderKey,
    ServerStatus,
    User,
)
from memory.common.db.models.events import TIMER, Event
from memory.common.db.models.agents import (
    EXECUTOR_WORKERS,
    STARTED_BY_CALLER,
    TERMINAL_STATUSES,
    RunStatus,
    iso,
)
from memory.common.db.models.mcp import TRANSPORTS
from memory.common.db.models.provider_keys import KEY_PROVIDERS, KeyStatus
from memory.common.llms.neutral import BUILTIN_TOOLS, Capabilities
from memory.common.llms.registry import PROVIDERS, all_models, split_model
from memory.common.mcp_oauth import MCPServerError, describe, revoke
from memory.common.notify import channel_problems, get_notification_channel, user_info_for
from memory.common.rate_limit import check_rate_limit
from memory.common.scopes import SCOPE_AGENTS, SCOPE_AGENTS_WORK, SCOPE_WRITE

logger = logging.getLogger(__name__)

agent_mcp = FastMCP("memory-agent")


def caller_token() -> tuple[str, list[str]]:
    """The caller's token and scopes, for resolving tools as the caller."""
    token = get_access_token()
    if token is None:
        raise ValueError("Not authenticated")
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


def changed_tools(db: DBSession, definition: AgentDefinition) -> list[str]:
    """External tools whose schema or description changed after the definition
    was last saved; re-saving acknowledges them."""
    changed = []
    for external in definition.spec.get("mcp_servers", []):
        server = db.get(ExternalMCPServer, external["server_id"])
        if server is None or server.user_id != definition.user_id:
            continue
        cached = {t["name"]: t for t in server.tools}
        for name in external["tools"]:
            changed_at = cached.get(name, {}).get("changed_at")
            if changed_at and datetime.fromisoformat(changed_at) > definition.updated_at:
                changed.append(external_tool_name(server.id, name))
    return changed


def external_writers(spec: AgentSpec, user_id: int) -> list[str]:
    """Allowlisted external tools that don't declare themselves read-only."""
    writers = []
    with make_session() as db:
        for external in spec.mcp_servers:
            server = db.get(ExternalMCPServer, external.server_id)
            if server is None or server.user_id != user_id:
                continue
            read_only = {t["name"] for t in server.tools if t["read_only"]}
            writers += [
                external_tool_name(server.id, n) for n in external.tools if n not in read_only
            ]
    return writers


def subscription_summaries(db: DBSession, definition_ids: list[int]) -> dict[int, dict]:
    rows = (
        db.query(
            Subscription.definition_id,
            func.count(),
            func.count().filter(Subscription.enabled),
            func.count().filter(Subscription.state.has_key("tripped")),
        )
        .filter(Subscription.definition_id.in_(definition_ids), Subscription.kind == INGEST)
        .group_by(Subscription.definition_id)
        .all()
    )
    found = {d: {"total": t, "enabled": e, "tripped": x} for d, t, e, x in rows}
    empty = {"total": 0, "enabled": 0, "tripped": 0}
    return {d: found.get(d, empty) for d in definition_ids}


def readable_run(
    db: DBSession, run_id: str, user_id: int, is_admin: bool, lock: bool = False
) -> AgentRun:
    """A run the caller may see: theirs (any, for admins), or, when the caller is a
    run, only one of its children."""
    run = db.get(AgentRun, run_id, with_for_update=lock)
    parent = calling_run(get_access_token())
    if (
        run is None
        or (run.user_id != user_id and not is_admin)
        or (parent is not None and run.parent_run_id != parent)
    ):
        raise ValueError(f"unknown run {run_id}")
    return run


def unknown_field_hint(error: ValidationError) -> str:
    """The accepted fields, when the spec has a top-level field AgentSpec lacks."""
    if not any(e["type"] == "extra_forbidden" and len(e["loc"]) == 1 for e in error.errors()):
        return ""
    return (
        f"\naccepted fields: {', '.join(AgentSpec.model_fields)} (only system_prompt is "
        "required); a definition's name and description are agent_upsert arguments, "
        "not spec fields"
    )


def parse_spec(spec: dict[str, Any]) -> AgentSpec:
    try:
        return AgentSpec.model_validate(spec)
    except ValidationError as e:
        raise ValueError(f"invalid spec: {e}{unknown_field_hint(e)}") from e


def inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    """`node` with each local $ref replaced by its definition, for clients that can't
    follow refs into a parameter's nested schema."""
    if isinstance(node, list):
        return [inline_refs(item, defs) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        rest = {k: v for k, v in node.items() if k != "$ref"}
        return inline_refs(defs[node["$ref"].rsplit("/", 1)[-1]], defs) | rest
    return {k: inline_refs(v, defs) for k, v in node.items() if k != "$defs"}


def spec_json_schema() -> dict[str, Any]:
    """AgentSpec's JSON schema as the tools take it: provider_key also by name."""
    schema = AgentSpec.model_json_schema()
    inlined = inline_refs(schema, schema.get("$defs", {}))
    inlined["properties"]["provider_key"] = {
        "anyOf": [{"type": "integer", "minimum": 1}, {"type": "string"}, {"type": "null"}],
        "default": None,
        "description": "One of your agent_key_list keys, by id or name; "
        "omit for your default key for the model's provider",
    }
    return inlined


# Validated by parse_spec; the schema only tells clients the fields.
SpecInput = Annotated[dict[str, Any], WithJsonSchema(spec_json_schema())]


def keyed_spec(db: DBSession, user_id: int, spec: dict[str, Any]) -> AgentSpec:
    """`spec` parsed, with a provider_key given by name replaced by that key's id."""
    ref = spec.get("provider_key")
    if isinstance(ref, str):
        spec = spec | {"provider_key": resolve_key_ref(db, user_id, ref).id}
    return parse_spec(spec)


async def invisible_tools(spec: AgentSpec) -> list[str]:
    names = [n for n in spec.tools if n not in BUILTIN_TOOLS]
    if not names:
        return []
    token, scopes = caller_token()
    async with memory_tools(token, scopes) as tools:
        visible = {t.name for t in await tools.list()}
    return [n for n in names if n not in visible]


def foreign_servers(spec: AgentSpec) -> list[str]:
    user_id, _ = caller()
    with make_session() as db:
        return [
            f"MCP server {s.server_id} is not yours"
            for s in spec.mcp_servers
            if (found := db.get(ExternalMCPServer, s.server_id)) is None
            or found.user_id != user_id
        ]


def provider_key_errors(spec: AgentSpec) -> list[str]:
    """Why no key of the caller's can pay for runs of `spec`.

    A key its provider rejected still counts here: it can be replaced without
    editing the agent, and the run says so if it isn't.
    """
    user_id, _ = caller()
    with make_session() as db:
        try:
            choose_key(db, user_id, spec.model, spec.provider_key, require_ok=False)
        except KeyUnavailable as e:
            return [str(e)]
        return []


async def check_spec(spec: AgentSpec) -> None:
    """Workers act with their own credentials, so a spec only they run isn't held to
    the caller's keys, tools or servers."""
    if not uses_server(spec):
        if errors := spec_errors(spec):
            raise ValueError("; ".join(errors))
        return
    errors = (
        spec_errors(spec)
        + provider_key_errors(spec)
        + [
            f"tool '{n}' is not visible to you (missing scope?)"
            for n in await invisible_tools(spec)
        ]
        + foreign_servers(spec)
    )
    if errors:
        raise ValueError("; ".join(errors))


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_AGENTS))
async def upsert(
    name: str, spec: SpecInput, description: str = "", definition_id: int | None = None
) -> dict:
    """Create or update an agent definition.

    `spec` fields: system_prompt, model ("anthropic/claude-sonnet-5-5"), max_output_tokens
    (omit for the most each run can afford, up to 16000), reasoning (off|low|medium|high),
    temperature, provider_extras, tools (memory tool names and builtins web_search/web_fetch),
    mcp_servers ([{server_id, tools}]), finish ({"mode": "answer"} or
    {"mode": "tool", "tools": [...]}), result_schema, max_turns, max_cost_usd,
    max_cost_usd_per_day, timeout_sec, provider_key (one of your agent_key_list keys, by id
    or name; stored as its id; omit for your default key for the model's provider),
    retention_days (how long its finished runs are kept; omit for AGENT_RUN_RETENTION_DAYS),
    placement (who executes its runs: server_only (default); runner_only, for your own
    workers (credentials with the agents:work scope), which claim them with agent_work_claim; or
    server_first / runner_first, which move a run nobody has claimed fallback_after
    minutes (1..720) after it was placed to the other side; omit fallback_after for never), requires (what a worker
    needs to take its runs: {"models": [globs], "tools": ["server:tool" globs],
    "flags": [...]}). A spec only workers run isn't checked against your keys or tools.
    Errors name every problem at once.
    """
    user_id, _ = caller()
    name = name.strip()
    if not name:
        raise ValueError("name is required")
    if len(name) > MAX_NAME_CHARS:
        raise ValueError(f"name must be at most {MAX_NAME_CHARS} characters")
    with make_session() as db:
        parsed = keyed_spec(db, user_id, spec)
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
    """Delete an agent definition with its subscriptions, cancelling its
    timers. Its past runs keep their frozen spec."""
    user_id, _ = caller()
    with make_session() as db:
        definition = owned_definition(db, definition_id, user_id)
        # Subscriptions go first, explicitly: fire_subscription locks a subscription before its
        # definition, and deleting in the same order can't deadlock against it.
        subscription_ids = [
            t.id
            for t in db.query(Subscription)
            .filter(Subscription.definition_id == definition_id)
            .with_for_update()
        ]
        db.query(Subscription).filter(Subscription.id.in_(subscription_ids)).delete()
        timers = db.query(Event).filter(
            Event.kind == TIMER,
            Event.owner_id == user_id,
            Event.routed_at.is_(None),
            Event.handler.contains({"agent": definition_id}),
        )
        for timer in timers.with_for_update():
            finish(timer, SKIPPED_CANCELLED)
        db.delete(definition)
    return {"deleted": definition_id}


@agent_mcp.tool(annotations=READ_ONLY)
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
        summaries = subscription_summaries(db, [d.id for d in definitions])
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
                    "subscriptions": summaries[d.id],
                    "changed_tools": changed_tools(db, d),
                    "problems": stored_spec_problems(db, user_id, d.spec),
                }
            )
    return {"definitions": result}


PROVIDER_NAMES = {"anthropic": "Anthropic", "openai": "OpenAI"}


@agent_mcp.tool(annotations=READ_ONLY)
# The keys page lists these for everyone whose keys pay for them, not only agent users.
@visible_when(require_scopes(SCOPE_AGENTS, SCOPE_WRITE))
async def system_list() -> dict:
    """The system agents memory runs for you (summaries, search steps): the model
    each runs on using your personal defaults, then shared system defaults,
    and whether a usable key exists. They are defined by memory and can't be edited."""
    user_id, _ = caller()
    with make_session() as db:
        selected = {a.slug: system_key(db, user_id, a) for a in SYSTEM_AGENTS.values()}
        providers = {slug: key.provider if key else None for slug, key in selected.items()}

    def entry(a: SystemAgent) -> dict:
        provider = providers[a.slug]
        return {
            "slug": a.slug,
            "title": a.title,
            "description": a.description,
            "use": a.use,
            "model": a.model_for(provider) if provider else a.models[0],
            "provider": provider or a.providers[0],
            "has_key": provider is not None,
            "problem": None
            if provider
            else f"Skipped until you add a default {' or '.join(PROVIDER_NAMES.get(p, p) for p in a.providers)} key.",
        }

    return {"agents": [entry(a) for a in SYSTEM_AGENTS.values()]}


@agent_mcp.tool(annotations=READ_ONLY)
@visible_when(require_scopes(SCOPE_AGENTS))
async def fetch(definition_id: int) -> dict:
    """One of your agent definitions."""
    user_id, _ = caller()
    with make_session() as db:
        definition = owned_definition(db, definition_id, user_id)
        return definition.serialize() | {"changed_tools": changed_tools(db, definition)}


def named_definition(db: DBSession, user_id: int, name: str) -> AgentDefinition | None:
    return (
        db.query(AgentDefinition)
        .filter(AgentDefinition.user_id == user_id, AgentDefinition.name == name)
        .one_or_none()
    )


def resolve_agent(
    db: DBSession, user_id: int, agent: int | str | dict
) -> tuple[AgentSpec, AgentDefinition | None, str | None]:
    """The spec to run for `agent`, with the definition or slug it came from.

    A name that is both your definition's and a prebuilt agent's slug runs your
    definition; "system:" slugs always mean the system agent.
    """
    if isinstance(agent, dict):
        return keyed_spec(db, user_id, agent), None, None
    if isinstance(agent, int):
        definition = owned_definition(db, agent, user_id)
        return parse_spec(definition.spec), definition, None
    if agent not in SYSTEM_AGENTS and (definition := named_definition(db, user_id, agent)):
        return parse_spec(definition.spec), definition, None
    return code_agent(db, user_id, agent), None, agent


def placement_overrides(
    placement: str | None, fallback_after: int | None, requires: dict | None
) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if placement is not None:
        overrides |= {"placement": placement, "fallback_after": fallback_after}
    elif fallback_after is not None:
        overrides["fallback_after"] = fallback_after
    if requires is not None:
        overrides["requires"] = requires
    return overrides


def require_notify_channel(db: DBSession, user_id: int) -> None:
    """Raise ValueError, naming why each channel is unusable, when none can notify the user."""
    user = db.get(User, user_id)
    if user is None:
        raise ValueError(f"user {user_id} not found")
    user_info = {"user": user_info_for(db, user)}
    if get_notification_channel(user_info, None) is not None:
        return
    reasons = "; ".join(f"{name}: {why}" for name, why in channel_problems(user_info).items())
    raise ValueError(
        f"notify needs a channel to reach you on, and none is usable ({reasons}). "
        "Start the run without notify and follow it with agent_wait or agent_list_runs(unseen=true)."
    )


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_AGENTS))
async def run(
    input: str,
    agent: int | str | SpecInput,
    placement: Placement | None = None,
    fallback_after: int | None = None,
    requires: dict | None = None,
    notify: bool = False,
) -> dict:
    """Start a run, paid with your key. Returns its run_id at once; follow it with agent_wait.

    `agent` is one of your definitions (its id or name), a prebuilt agent's slug
    (agent_catalog) or a system agent's (agent_system_list, "system:..."), or an
    inline spec, which runs without being saved. An inline spec takes the fields of
    agent_upsert's `spec` (listed in this parameter's schema); only system_prompt is
    required. A definition's name and description are not spec fields.

    With `notify`, memory notifies you (as meta_notify_user would: Discord, Slack or
    email) when the run ends, with its status, the start of its result and its run_id.
    It is refused, with the reason each channel can't be used, when none can reach you.
    Either way, agent_list_runs(unseen=true) lists your ended runs that neither
    agent_wait nor an unseen listing has handed you yet.

    `placement`, `fallback_after` and `requires` override the agent's own for this run
    (see agent_upsert); with `placement` given, an omitted fallback_after means never.
    Runs placed on workers wait for anyone with the agents:work scope to claim them, need
    no key of yours unless the server may take them, and cost you nothing. System agents
    always run on the server.

    Called by a run, this starts its child: it may use only the run's tools, runs on the
    server, nests at most AGENT_MAX_SPAWN_DEPTH deep, and gets half of what the run has
    left of its budget, which it holds until it finishes. A child spec's own max_cost_usd
    is capped at what the run has left; taking all of it ends the run budget_exceeded.
    """
    user_id, _ = caller()
    token, _ = caller_token()
    parent_id = calling_run(get_access_token())
    overrides = placement_overrides(placement, fallback_after, requires)
    with make_session() as db:
        parsed, definition, slug = resolve_agent(db, user_id, agent)
        if overrides and parent_id is not None:
            raise ValueError("runs started by runs can't override placement")
        if overrides and slug in SYSTEM_AGENTS:
            raise ValueError("system agents run on the server only")
        if notify and parent_id is not None:
            raise ValueError("runs started by runs can't ask to notify")
        if notify:
            require_notify_channel(db, user_id)
        if overrides:
            parsed = parse_spec(parsed.model_dump(mode="json") | overrides)
        await check_spec(parsed)
        parent = None
        if parent_id is not None:
            parent = db.get(AgentRun, parent_id)
            if parent is None:
                raise ValueError(f"the calling run {parent_id} no longer exists")
        # A run's session is bound to the run's API key, so its children act through it too.
        api_key = token_api_key(token, db)
        if parent is not None:
            trigger = TriggerInfo(kind="agent")
        else:
            trigger = TriggerInfo(kind="mcp" if api_key else "manual")
        try:
            started = enqueue_run(
                db,
                user_id=user_id,
                spec=parsed,
                input=input,
                trigger=trigger,
                definition=definition,
                agent=slug,
                api_key=api_key,
                parent=parent,
                notify=notify,
            )
        except (SpecInvalid, CapExceeded) as e:
            raise ValueError(str(e)) from e
        return {"run_id": started.id, "status": started.status}


@agent_mcp.tool(annotations=READ_ONLY)
@visible_when(require_scopes(SCOPE_AGENTS))
async def wait(
    run_id: str,
    after_seq: int = -1,
    wait: int = 0,
    limit: int = 100,
    result_only: bool = False,
) -> dict:
    """A run plus up to `limit` of its transcript steps with seq > after_seq.

    With wait > 0, blocks (up to AGENT_MAX_WAIT_SEC) until new steps appear or the
    run finishes; call again with the last seq you saw to follow a live run. With
    `result_only`, returns the run (its status, result and error) without its spec or
    transcript, and with wait > 0 blocks until it finishes. Handing you an ended run of
    yours marks it seen (agent_list_runs unseen), which is the only thing it writes.
    Called by a run, it reaches only that run's children.
    """
    user_id, is_admin = caller()
    by_run = calling_run(get_access_token()) is not None
    deadline = time.monotonic() + min(max(wait, 0), settings.AGENT_MAX_WAIT_SEC)
    while True:
        with make_session() as db:
            found = readable_run(db, run_id, user_id, is_admin)
            finished = found.status in TERMINAL_STATUSES
            if finished and found.user_id == user_id and not by_run:
                mark_seen(found)
            if result_only:
                payload = {"run": found.serialize()}
                steps = []
            else:
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
        if steps or finished or time.monotonic() >= deadline:
            return payload
        await asyncio.sleep(1)


def mark_seen(run: AgentRun) -> None:
    if run.seen_at is None:
        run.seen_at = datetime.now(timezone.utc)


@agent_mcp.tool(annotations=READ_ONLY)
@visible_when(require_scopes(SCOPE_AGENTS))
async def list_runs(
    definition_id: int | None = None,
    agent: str | None = None,
    status: str | None = None,
    limit: int = 50,
    before: str | None = None,
    before_id: str | None = None,
    unseen: bool = False,
) -> dict:
    """Your runs, newest first, optionally only a definition's or a prebuilt or system agent's (by slug). To page, pass the last run's `created_at` as `before` and its `id` as `before_id`.

    With `unseen`, only the runs you started with agent_run that have ended and that
    neither agent_wait nor an unseen listing has handed you yet. It marks the runs it
    returns seen, which is the only thing it writes, and returns them with their new
    seen_at; runs past `limit` or left out by other filters stay unseen. Called by a
    run, it marks nothing seen.
    """
    user_id, _ = caller()
    by_run = calling_run(get_access_token()) is not None
    with make_session() as db:
        query = db.query(AgentRun).filter(AgentRun.user_id == user_id)
        if unseen:
            query = query.filter(
                AgentRun.seen_at.is_(None),
                AgentRun.finished_at.is_not(None),
                AgentRun.parent_run_id.is_(None),
                AgentRun.trigger_kind.in_(STARTED_BY_CALLER),
            )
        if definition_id is not None:
            query = query.filter(AgentRun.definition_id == definition_id)
        if agent is not None:
            query = query.filter(AgentRun.agent == agent)
        if status is not None:
            query = query.filter(AgentRun.status == status)
        if before is not None:
            cutoff = datetime.fromisoformat(before)
            # Runs created in one transaction share a created_at; the id breaks the tie.
            older = AgentRun.created_at < cutoff
            if before_id is not None:
                older = or_(
                    older, and_(AgentRun.created_at == cutoff, AgentRun.id < before_id)
                )
            query = query.filter(older)
        runs = (
            query.order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .limit(min(max(limit, 1), 200))
            .all()
        )
        if unseen and not by_run:
            for r in runs:
                mark_seen(r)
        return {"runs": [r.serialize() for r in runs]}


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_AGENTS))
async def cancel(run_id: str) -> dict:
    """Cancel a run and the runs it started. Pending runs stop at once; running ones
    stop before their next model or tool call. Runs a worker holds end at once; the
    worker learns of it on renewal."""
    user_id, is_admin = caller()
    with make_session() as db:
        found = readable_run(db, run_id, user_id, is_admin, lock=True)
        cancel_tree(db, found, datetime.now(timezone.utc))
        db.flush()
        return found.serialize()


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_AGENTS))
async def star(run_id: str, starred: bool) -> dict:
    """Star a run to keep it past its retention (its agent's retention_days, else AGENT_RUN_RETENTION_DAYS)."""
    user_id, is_admin = caller()
    with make_session() as db:
        found = readable_run(db, run_id, user_id, is_admin)
        found.starred = starred
        db.flush()
        return found.serialize()


class WorkerUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: NonNegativeInt | None = None
    output_tokens: NonNegativeInt | None = None
    model: str | None = Field(default=None, max_length=200)
    # Zero is honest for a worker on a flat-rate subscription.
    cost_usd: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=6)] | None = None


# Candidates read per query while looking for a run the worker can take.
CLAIM_SCAN = 200
MAX_WORKER_LABEL_CHARS = 200
MAX_WORKER_ERROR_CHARS = 10_000
# What a worker needs to execute a run. An allow-list, so new spec fields and run
# columns (the owner's keys, servers, subscriptions) never reach workers by default.
WORKER_SPEC_FIELDS = (
    "system_prompt",
    "model",
    "max_output_tokens",
    "reasoning",
    "temperature",
    "tools",
    "finish",
    "result_schema",
    "max_turns",
    "timeout_sec",
    "requires",
)


def worker_caller() -> int:
    """The calling worker's user id; runs may never take work, which would escape their caps."""
    if calling_run(get_access_token()) is not None:
        raise ValueError("runs can't claim or submit work")
    user_id, _ = caller()
    return user_id


def leased_payload(run: AgentRun) -> dict:
    payload = {
        "id": run.id,
        "status": run.status,
        "input": run.input,
        "spec": {k: run.spec.get(k) for k in WORKER_SPEC_FIELDS},
        "lease_token": run.lease_token,
        "lease_expires_at": iso(run.lease_expires_at),
        "lease_lapses": run.lease_lapses,
        "worker_label": run.worker_label,
    }
    # A queue delivery has no spec; what it's about is its ref.
    return payload | ({"queue": run.queue, "ref": run.trigger_ref} if run.queue else {})


def lease_length(lease_sec: int | None) -> timedelta:
    requested = lease_sec or settings.AGENT_WORK_LEASE_DEFAULT_SEC
    return timedelta(
        seconds=min(
            max(requested, settings.AGENT_WORK_LEASE_MIN_SEC), settings.AGENT_WORK_LEASE_MAX_SEC
        )
    )


def hold_ends(run: AgentRun) -> datetime:
    assert run.started_at is not None
    return run.started_at + timedelta(seconds=settings.AGENT_WORK_HOLD_MAX_SEC)


def leased_run(db: DBSession, run_id: str, lease_token: str, user_id: int) -> AgentRun:
    run = db.get(AgentRun, run_id, with_for_update=True)
    if (
        run is None
        or run.user_id != user_id
        or run.executor != EXECUTOR_WORKERS
        or run.lease_token is None
    ):
        raise ValueError(f"unknown run {run_id}")
    if run.worker_user_id != user_id or not secrets.compare_digest(run.lease_token, lease_token):
        raise ValueError("lease token does not match this run's current claim")
    return run


def renew(run_id: str, lease_token: str, user_id: int, lease: timedelta) -> dict:
    with make_session() as db:
        run = leased_run(db, run_id, lease_token, user_id)
        if run.status == RunStatus.CANCELLED.value:
            return {"run": leased_payload(run), "cancel_requested": True}
        if run.status in TERMINAL_STATUSES:
            raise ValueError(f"the run already ended ({run.status}): {run.error}")
        now = datetime.now(timezone.utc)
        if hold_ends(run) <= now:
            run.status, run.finished_at, run.error = RunStatus.FAILED.value, now, HELD_OUT
            db.commit()
            raise ValueError(f"the run failed: {HELD_OUT}")
        # A lapsed lease nobody else claimed yet is taken back as it was.
        run.status = RunStatus.RUNNING.value
        run.lease_expires_at = min(now + lease, hold_ends(run))
        db.commit()
        return {"run": leased_payload(run), "cancel_requested": False}


def lease_run(run: AgentRun, user_id: int, worker: str | None, lease: timedelta) -> dict:
    now = datetime.now(timezone.utc)
    run.status = RunStatus.RUNNING.value
    run.lease_token = secrets.token_urlsafe(24)
    run.worker_user_id, run.worker_label = user_id, worker
    run.started_at = run.started_at or now
    run.lease_expires_at = min(now + lease, hold_ends(run))
    return leased_payload(run)


def take_next(
    user_id: int, token: str, declared: Declared, worker: str | None, lease: timedelta
) -> dict | None:
    """Scans the queue oldest first in CLAIM_SCAN batches, by (created_at, id) cursor,
    without locks, then locks only the run it picks, passing over it if another worker
    has it or took it since the scan. The requirement globs are matched here: either
    side may be a glob, which SQL can't match. Each distinct `requires` is matched once
    per scan, as one agent's runs share it and a declaration may be long. A run of
    subscription deliveries is rendered
    as its items are now (queues.rerender); one the claiming credential can't read all
    of is passed over for a worker that can."""
    with make_session() as db:
        user = db.get(User, user_id)
        assert user is not None
        key = token_api_key(token, db)
        cursor = None
        met: dict[str, bool] = {}
        while True:
            now = datetime.now(timezone.utc)
            claimable = (
                AgentRun.user_id == user_id,
                AgentRun.status == RunStatus.PENDING.value,
                AgentRun.executor == EXECUTOR_WORKERS,
                AgentRun.queue.is_(None),
                or_(
                    AgentRun.started_at.is_(None),
                    AgentRun.started_at
                    > now - timedelta(seconds=settings.AGENT_WORK_HOLD_MAX_SEC),
                ),
                *(() if can_read(user, key) else (AgentRun.trigger_kind != INGEST,)),
            )
            query = db.query(AgentRun.created_at, AgentRun.id, AgentRun.spec["requires"]).filter(
                *claimable
            )
            if cursor is not None:
                query = query.filter(tuple_(AgentRun.created_at, AgentRun.id) > cursor)
            batch = query.order_by(AgentRun.created_at, AgentRun.id).limit(CLAIM_SCAN).all()
            if not batch:
                return None
            for created_at, run_id, requires in batch:
                cursor = (created_at, run_id)
                needs = json.dumps(requires or {}, sort_keys=True)
                if needs not in met:
                    met[needs] = satisfies(Requirements.model_validate(requires or {}), declared)
                if not met[needs]:
                    continue
                run = (
                    db.query(AgentRun)
                    .filter(AgentRun.id == run_id, *claimable)
                    .with_for_update(skip_locked=True)
                    .one_or_none()
                )
                if run is None:
                    continue
                ready = not is_delivery(run) or ready_to_claim(db, run, user, key)
                if ready is None:
                    # Left, unlocked, for a worker whose key can read it.
                    db.rollback()
                    continue
                if not ready:
                    db.commit()
                    continue
                payload = lease_run(run, user_id, worker, lease)
                db.commit()
                return payload


def parse_capabilities(capabilities: dict | None) -> Declared:
    try:
        return Declared.model_validate(capabilities or {})
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()
        )
        raise ValueError(
            f"invalid capabilities: {problems} (capabilities takes models, tools and flags, "
            f"each a list of at most {MAX_DECLARED} globs of at most {MAX_GLOB_CHARS} "
            "characters)"
        ) from e


def require_read(user_id: int, token: str) -> None:
    with make_session() as db:
        user = db.get(User, user_id)
        assert user is not None
        if not can_read(user, token_api_key(token, db)):
            raise ValueError("claiming queue deliveries needs the read scope")


def take_from(
    user_id: int, token: str, patterns: list[str], worker: str | None, lease: timedelta
) -> dict | None:
    """Tries the caller's queues matching `patterns`, resolved now so a queue made
    since the last try counts, in claimable_queues' order."""
    with make_session() as db:
        user = db.get(User, user_id)
        assert user is not None
        key = token_api_key(token, db)
        for queue in claimable_queues(db, user, patterns):
            run = take_from_queue(db, user, queue, key)
            if run is None:
                continue
            payload = lease_run(run, user_id, worker, lease)
            db.commit()
            return payload
        return None


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_AGENTS_WORK))
async def work_claim(
    capabilities: dict | None = None,
    worker: str | None = None,
    lease_sec: int | None = None,
    wait: int = 0,
    run_id: str | None = None,
    lease_token: str | None = None,
    queues: list[str] | None = None,
) -> dict:
    """Take the oldest of your own runs placed on workers whose requirements you meet,
    to execute yourself with your own models and tools. Returns {"run": null} if none.

    With `queues`, a list of queue names or case-sensitive globs (e.g. ["messages",
    "slack-*"] or ["*"]), take instead the next delivery of your subscriptions whose
    handler is a matching queue (subscription_upsert), from the queue whose oldest
    pending, undeferred delivery is oldest first, whether or not your key can read it;
    this needs the read scope. Give `capabilities` too to take either kind, runs first:
    deliveries wait while any run is claimable, so keep a queue-only worker for queues
    that can't wait. Give neither to take only runs. It comes as a run with "queue" and "ref"
    ({"subscription_id", "item_ids", "changes": {"<item id>": [change tags]}, and
    "coalesced": {"<item id>": [ids of items whose deliveries were folded into it]}
    when any were}), its input rendered from the subscription's template and the items as
    they are now. Items the subscription can no longer read, or that one of its runs
    reported posting (agent_work_submit's `posted`), are left out and their
    deliveries skipped; ones only your key can't read are left for another worker,
    and those no worker's key can read wait and end `expired`. A
    subscription's order_by ("item" or "thread") holds a delivery back until an earlier one
    for the same item or thread is answered. A removed item's delivery carries its id
    with the change "removed"; a raw subscription's carries the webhook delivery
    (see subscription_upsert). Report on it with agent_work_submit as on
    any run; capabilities don't apply to it. agent_work_queues shows what is waiting.

    `capabilities` declares what you have, as {"models": [...], "tools": ["server:tool",
    ...], "flags": [...]}, each at most 1000 entries of at most 200 characters (globs
    allowed, e.g. "memory:*"); a run is yours to take when
    each of its spec's `requires` entries matches one of them. The run comes with its
    input, spec (system_prompt, model, tools, result_schema, limits, ...), a lease_token
    and lease_expires_at: finish it with agent_work_submit before the lease runs out
    (lease_sec, default AGENT_WORK_LEASE_DEFAULT_SEC, kept within
    AGENT_WORK_LEASE_MIN_SEC..AGENT_WORK_LEASE_MAX_SEC), or it goes back for your
    workers to claim; after AGENT_WORK_MAX_LAPSES lapses it fails. With wait > 0, blocks up to that
    many seconds (at most AGENT_MAX_WAIT_SEC) for a run. `worker` labels the run with
    this worker (at most 200 characters).

    To renew a lease, call again with the run's `run_id` and `lease_token`: the lease
    is extended by lease_sec, but never past AGENT_WORK_HOLD_MAX_SEC from the run's
    first claim, after which the run fails; "cancel_requested": true means it was cancelled,
    so stop.
    """
    user_id = worker_caller()
    token, _ = caller_token()
    lease = lease_length(lease_sec)
    if worker is not None and len(worker) > MAX_WORKER_LABEL_CHARS:
        raise ValueError(f"worker label is longer than {MAX_WORKER_LABEL_CHARS} characters")
    if (run_id is None) != (lease_token is None):
        raise ValueError("give both run_id and lease_token to renew a lease, or neither")
    if run_id is not None and lease_token is not None:
        return await asyncio.to_thread(renew, run_id, lease_token, user_id, lease)
    if queues == []:
        raise ValueError("queues is empty: give at least one queue name or glob, or leave it out")
    if queues is not None:
        await asyncio.to_thread(require_read, user_id, token)
    takes_runs = queues is None or capabilities is not None
    declared = parse_capabilities(capabilities)
    deadline = time.monotonic() + min(max(wait, 0), settings.AGENT_MAX_WAIT_SEC)
    while True:
        taken = None
        # Runs first: someone usually waits on a run and each costs its owner, while a
        # stream of queue deliveries would otherwise starve them.
        if takes_runs:
            taken = await asyncio.to_thread(take_next, user_id, token, declared, worker, lease)
        if taken is None and queues is not None:
            taken = await asyncio.to_thread(take_from, user_id, token, queues, worker, lease)
        if taken is not None:
            return {"run": taken}
        if time.monotonic() >= deadline:
            return {"run": None}
        await asyncio.sleep(1)


def worker_result(spec: dict[str, Any], result: Any) -> dict[str, Any]:
    """Stored as the server's runs store theirs: {"data": ...} against a result_schema,
    else {"text": ...}."""
    if len(json.dumps(result)) > settings.AGENT_WORK_MAX_RESULT_BYTES:
        raise ValueError(f"result is larger than {settings.AGENT_WORK_MAX_RESULT_BYTES} bytes")
    schema = spec.get("result_schema")
    if schema is None:
        if not isinstance(result, str):
            raise ValueError("result must be text: this run has no result_schema")
        return {"text": result}
    if errors := result_errors(schema, result):
        raise ValueError("result does not match result_schema: " + "; ".join(errors))
    return {"data": result}


def same_outcome(run: AgentRun, result: Any, error: str | None) -> bool:
    """Whether an ended run's outcome is the one being submitted: a retried submit."""
    if error is not None:
        return run.status == RunStatus.FAILED.value and run.error == error
    return run.status == RunStatus.DONE.value and run.result == worker_result(run.spec, result)


def record_outcome(
    run_id: str,
    lease_token: str,
    user_id: int,
    result: Any,
    error: str | None,
    usage: dict,
    posted: list[dict[str, str]],
) -> dict:
    with make_session() as db:
        run = leased_run(db, run_id, lease_token, user_id)
        if run.status in TERMINAL_STATUSES:
            if same_outcome(run, result, error):
                return run.serialize()
            raise ValueError(f"the run already ended ({run.status}): {run.error}")
        stored = None if result is None else worker_result(run.spec, result)
        run.status = (RunStatus.DONE if error is None else RunStatus.FAILED).value
        run.result, run.error = stored, error
        run.worker_usage = usage or None
        run.posted = posted or None
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        return run.serialize()


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_AGENTS_WORK))
async def work_submit(
    run_id: str,
    lease_token: str,
    result: Any = None,
    error: str | None = None,
    usage: dict | None = None,
    posted: list[dict] | None = None,
) -> dict:
    """Finish a run you claimed with agent_work_claim: either its `result` (text, or an
    object matching the run's result_schema) or an `error` (at most 10000 characters)
    saying why it couldn't be done.

    `lease_token` is the one agent_work_claim returned; it stops working once another
    worker claims the run. A result that doesn't match keeps your lease, so fix it and resubmit.
    Resubmitting the outcome you recorded returns it again; any other submit to a run
    that ended (cancelled, already answered, or failed by the server for lapsed or
    overlong leases) is refused, saying how it ended. `usage` optionally reports what you spent (input_tokens,
    output_tokens, model, cost_usd): it is shown on the run and never counted against
    your budgets. `posted` lists what you posted while working on it (at most 50 of
    {"source", "external_id"}: slack "channel:ts", discord message id, email
    Message-ID header (as email_send returns it), beeper message id); once Memory
    ingests them, they never match the subscription that started this run.
    """
    user_id = worker_caller()
    if (result is None) == (error is None):
        raise ValueError("give exactly one of result or error")
    if error is not None and len(error) > MAX_WORKER_ERROR_CHARS:
        raise ValueError(f"error is longer than {MAX_WORKER_ERROR_CHARS} characters")
    reported = WorkerUsage.model_validate(usage or {}).model_dump(mode="json", exclude_none=True)
    posts = POSTED.validate_python(posted or [])
    return await asyncio.to_thread(
        record_outcome,
        run_id,
        lease_token,
        user_id,
        result,
        error,
        reported,
        [p.model_dump() for p in posts],
    )


@agent_mcp.tool(annotations=READ_ONLY)
@visible_when(require_scopes(SCOPE_AGENTS_WORK))
async def work_queues() -> dict:
    """Your subscriptions' queues (agent_work_claim's `queues`), each with how many deliveries
    of its enabled subscriptions are waiting and the oldest one's age in seconds (null
    when none). Waiting includes deliveries a claim can't take yet: deferred an hour by
    a cap (see the subscription's `state.throttled`), in a batch whose window is still
    open, or held by order_by behind an earlier delivery whose run hasn't ended.
    Deliveries no worker's key can read wait here and end `expired`."""
    user_id = worker_caller()
    with make_session() as db:
        return {"queues": backlog(db, user_id)}


def capabilities_json(caps: Capabilities) -> dict:
    return asdict(caps) | {"reasoning_levels": sorted(caps.reasoning_levels)}


@agent_mcp.tool(annotations=READ_ONLY)
@visible_when(require_scopes(SCOPE_AGENTS))
async def catalog() -> dict:
    """What a definition can use: your visible tools, builtins, models, limits,
    your external MCP servers with their cached tools, and your provider keys;
    plus the prebuilt agents agent_run takes by slug."""
    token, scopes = caller_token()
    async with memory_tools(token, scopes) as tools:
        listed = await tools.list()
    return {
        "builtins": sorted(BUILTIN_TOOLS),
        "agents": [
            {"slug": a.slug, "title": a.title, "description": a.description}
            for a in PREBUILT_AGENTS.values()
        ],
        "tools": [
            {"name": t.name, "description": t.description, "read_only": t.read_only}
            for t in listed
            if not memory_tool_denied(t.name)
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
            "max_timeout_sec": settings.AGENT_MAX_TIMEOUT_SEC,
            "max_runs_per_hour": settings.AGENT_MAX_RUNS_PER_HOUR,
            "default_user_key_run_cost_usd": str(settings.AGENT_DEFAULT_USER_KEY_RUN_COST_USD),
            "max_spawn_depth": settings.AGENT_MAX_SPAWN_DEPTH,
        },
        "default_model": DEFAULT_AGENT_MODEL,
        "subscription_source_types": source_types(),
        "mcp_servers": user_servers(),
        "provider_keys": [k for keys in user_keys().values() for k in keys],
    }


async def exposure_warnings(spec: AgentSpec) -> list[str]:
    """Tools a subscription would hand to content nobody vetted: writes and egress."""
    egress = sorted(set(spec.tools) & BUILTIN_TOOLS)
    names = [n for n in spec.tools if n not in BUILTIN_TOOLS]
    writes = []
    if names:
        token, scopes = caller_token()
        async with memory_tools(token, scopes) as tools:
            read_only = {t.name for t in await tools.list() if t.read_only}
        writes = [n for n in names if n not in read_only]
    writes += external_writers(spec, caller()[0])
    if not (egress or writes):
        return []
    return [
        "this subscription feeds external content to an agent that can "
        f"write or reach outside memory ({', '.join(writes + egress)}); "
        "content could steer those tools, so keep the caps tight"
    ]


# --- External MCP servers -------------------------------------------------


def user_servers() -> list[dict]:
    user_id, _ = caller()
    with make_session() as db:
        servers = (
            db.query(ExternalMCPServer)
            .filter(ExternalMCPServer.user_id == user_id)
            .order_by(ExternalMCPServer.name)
            .all()
        )
        return [s.serialize() for s in servers]


def owned_server(db: DBSession, server_id: int, user_id: int) -> ExternalMCPServer:
    server = db.get(ExternalMCPServer, server_id)
    if server is None or server.user_id != user_id:
        raise ValueError(f"unknown MCP server {server_id}")
    return server


def server_changes_allowed(user_id: int) -> int:
    """Each change dials out to a user-chosen host, so they're rate-limited."""
    if not check_rate_limit(
        f"mcp-server-changes:{user_id}", settings.MCP_SERVER_CHANGES_PER_HOUR, 3600
    ):
        raise ValueError(
            "too many MCP server changes this hour (MCP_SERVER_CHANGES_PER_HOUR); try later"
        )
    return user_id


def serialized(server_id: int) -> dict:
    with make_session() as db:
        server = db.get(ExternalMCPServer, server_id)
        return server.serialize() if server else {}


def server_definitions(db: DBSession, user_id: int, server_id: int) -> list[str]:
    definitions = db.query(AgentDefinition).filter(AgentDefinition.user_id == user_id)
    return sorted(
        d.name
        for d in definitions
        if any(s.get("server_id") == server_id for s in d.spec.get("mcp_servers", []))
    )


@agent_mcp.tool(annotations=READ_ONLY)
@visible_when(require_scopes(SCOPE_AGENTS))
async def server_list() -> dict:
    """Your external MCP servers, with status and cached tools."""
    return {"servers": user_servers()}


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_AGENTS))
async def server_upsert(
    name: str,
    url: str,
    transport: str = "http",
    auth: dict | None = None,
    server_id: int | None = None,
) -> dict:
    """Register or update one of your external MCP servers and connect to it.

    `transport`: "http" (streamable HTTP) or "sse". `auth`: {"type": "auto" | "oauth" |
    "headers" | "none", "headers": {...}, "client_id", "client_secret", "scopes"};
    "auto" detects OAuth. Returns the server, `authorize_url` when you must authorize
    it in a browser, `secret_sent_to` (the host a client secret you entered goes to),
    and `error` when it couldn't connect. Changing the URL drops its tokens. Editing
    with `auth` omitted and the same URL and transport keeps its saved credentials and
    re-tests nothing: check the server's `status` and use agent_server_authorize to reconnect.
    """
    user_id = server_changes_allowed(caller()[0])
    name = name.strip()
    if not name or len(name) > MAX_NAME_CHARS:
        raise ValueError(f"name must be 1 to {MAX_NAME_CHARS} characters")
    if transport not in TRANSPORTS:
        raise ValueError(f"transport must be one of {', '.join(TRANSPORTS)}")
    try:
        parsed = AuthInput.model_validate(auth or {})
    except ValidationError as e:
        # Not str(e): pydantic echoes input values, and these hold secrets.
        raise ValueError(f"invalid auth: {describe(e)}") from e
    keep_credentials = False
    with make_session() as db:
        if server_id is None:
            count = (
                db.query(func.count(ExternalMCPServer.id))
                .filter(ExternalMCPServer.user_id == user_id)
                .scalar()
            )
            if count >= settings.MCP_MAX_SERVERS_PER_USER:
                raise ValueError(
                    f"you can register at most {settings.MCP_MAX_SERVERS_PER_USER} MCP "
                    "servers (MCP_MAX_SERVERS_PER_USER)"
                )
            server = ExternalMCPServer(
                user_id=user_id,
                auth_type="none",
                status=ServerStatus.PENDING_AUTH.value,
            )
            db.add(server)
        else:
            server = owned_server(db, server_id, user_id)
            same_server = (server.url, server.transport) == (url, transport)
            keep_credentials = auth is None and same_server
            if not same_server:
                # Credentials were issued for the old server.
                server.clear_tokens()
                server.clear_client()
                server.headers = None
        server.name, server.url, server.transport = name, url, transport
        try:
            db.flush()
        except IntegrityError as e:
            raise ValueError(f"you already have an MCP server named {name!r}") from e
        server_id = server.id
    if keep_credentials:
        return {
            "server": serialized(server_id),
            "authorize_url": None,
            "secret_sent_to": None,
            "error": None,
        }
    try:
        result = await configure(user_id, server_id, parsed)
    except MCPServerError as e:
        return {"server": serialized(server_id), "authorize_url": None, "error": str(e)}
    return {
        "server": serialized(server_id),
        "authorize_url": result.authorize_url,
        "secret_sent_to": result.secret_sent_to,
        "error": None,
    }


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_AGENTS))
async def server_authorize(server_id: int) -> dict:
    """A fresh URL to (re-)authorize one of your OAuth MCP servers in a browser."""
    user_id = server_changes_allowed(caller()[0])
    try:
        return {"authorize_url": await authorize(user_id, server_id)}
    except MCPServerError as e:
        raise ValueError(str(e)) from e


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_AGENTS))
async def server_refresh_tools(server_id: int) -> dict:
    """Re-list a server's tools: what was added, removed, changed or dropped (and why)."""
    user_id = server_changes_allowed(caller()[0])
    try:
        server = load_detached(server_id, user_id)
        diff = await refresh_tool_cache(server)
    except MCPServerError as e:
        raise ValueError(str(e)) from e
    save(server, TOOL_COLUMNS)
    return {"diff": diff, "server": serialized(server_id)}


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_AGENTS))
async def server_disconnect(server_id: int) -> dict:
    """Revoke (best effort) and forget a server's tokens; it stays registered."""
    user_id, _ = caller()
    try:
        server = load_detached(server_id, user_id)
    except MCPServerError as e:
        raise ValueError(str(e)) from e
    await revoke(server)
    server.clear_tokens()
    server.status = ServerStatus.PENDING_AUTH.value
    server.status_detail = "disconnected; authorize it again to use it"
    save(server, [*TOKEN_COLUMNS, *STATUS_COLUMNS])
    return serialized(server_id)


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_AGENTS))
async def server_delete(server_id: int) -> dict:
    """Delete one of your MCP servers. Refused while any of your agents use it."""
    user_id, _ = caller()
    with make_session() as db:
        owned_server(db, server_id, user_id)
        if users := server_definitions(db, user_id, server_id):
            raise ValueError(f"MCP server {server_id} is used by {', '.join(users)}")
    server = load_detached(server_id, user_id)
    await revoke(server)
    with make_session() as db:
        db.query(ExternalMCPServer).filter(ExternalMCPServer.id == server_id).delete()
    return {"deleted": server_id}


# --- LLM provider keys ----------------------------------------------------

# Each key check is one request to the provider; capped so the tool can't be
# used to test keys in bulk.
KEY_CHECKS_PER_HOUR = 20


def user_keys() -> dict[str, list[dict]]:
    user_id, _ = caller()
    with make_session() as db:
        keys = (
            db.query(LLMProviderKey)
            .filter(LLMProviderKey.user_id == user_id)
            .order_by(LLMProviderKey.provider, LLMProviderKey.label)
            .all()
        )
        return {
            p: [k.serialize() for k in keys if k.provider == p] for p in KEY_PROVIDERS
        }


def daily_cap(value: str) -> Decimal | None:
    """'' clears the cap."""
    if value == "":
        return None
    try:
        cap = Decimal(value)
    except InvalidOperation as e:
        raise ValueError("max_cost_usd_per_day must be a positive amount") from e
    if not cap.is_finite() or cap <= 0 or cap >= Decimal("1e6"):
        raise ValueError("max_cost_usd_per_day must be a positive amount")
    return cap


def checked_label(label: str | None, required: bool) -> str | None:
    if label is None and not required:
        return None
    label = (label or "").strip()
    if not label:
        raise ValueError("label is required")
    if len(label) > MAX_NAME_CHARS:
        raise ValueError(f"label must be at most {MAX_NAME_CHARS} characters")
    return label


def key_checks_allowed(user_id: int) -> None:
    if not check_rate_limit(f"llm-key-checks:{user_id}", KEY_CHECKS_PER_HOUR, 3600):
        raise ValueError("too many API key checks this hour; try later")


def label_taken(db: DBSession, user_id: int, label: str, key_id: int | None) -> bool:
    query = db.query(LLMProviderKey).filter(
        LLMProviderKey.user_id == user_id,
        LLMProviderKey.label == label,
    )
    if key_id is not None:
        query = query.filter(LLMProviderKey.id != key_id)
    return query.count() > 0


def provider_defaults(db: DBSession, user_id: int, provider: str):
    return db.query(LLMProviderKey).filter(
        LLMProviderKey.user_id == user_id,
        LLMProviderKey.provider == provider,
        LLMProviderKey.is_default,
    )


def make_default(db: DBSession, key: LLMProviderKey) -> None:
    provider_defaults(db, key.user_id, key.provider).filter(
        LLMProviderKey.id != key.id
    ).update({"is_default": False})
    db.flush()
    key.is_default = True


def promote_default(
    db: DBSession, user_id: int, provider: str, dropped: int
) -> LLMProviderKey | None:
    """Make the most recently used (else newest) other key the default, if none is."""
    db.flush()
    if provider_defaults(db, user_id, provider).count():
        return None
    successor = (
        db.query(LLMProviderKey)
        .filter(
            LLMProviderKey.user_id == user_id,
            LLMProviderKey.provider == provider,
            LLMProviderKey.id != dropped,
        )
        .order_by(
            LLMProviderKey.last_used_at.desc().nulls_last(),
            LLMProviderKey.created_at.desc(),
            LLMProviderKey.id.desc(),
        )
        .first()
    )
    if successor is not None:
        successor.is_default = True
    return successor


@agent_mcp.tool(annotations=READ_ONLY)
@visible_when(require_scopes(SCOPE_WRITE))
async def key_list() -> dict:
    """Your LLM provider API keys by provider (never the keys themselves, only their
    last 4 characters). They pay for your agent runs, LLM work on items you own, and
    the LLM steps of your searches."""
    return {"providers": user_keys()}


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_WRITE))
async def key_upsert(
    provider: str | None = None,
    label: str | None = None,
    key: str | None = None,
    is_default: bool | None = None,
    is_system_default: bool | None = None,
    max_cost_usd_per_day: str | None = None,
    key_ref: int | str | None = None,
) -> dict:
    """Add or update one of your LLM provider API keys.

    Creating needs provider (anthropic or openai), label and key. A key is stored
    only once its provider accepts it, and is never shown again. With key_ref (a key's id or
    name), omitted fields are kept and a new key is checked again. Your first key for a
    provider becomes its default; un-defaulting it makes your most recently used
    other key the default. max_cost_usd_per_day caps daily spend on this key (""
    removes the cap; with no cap here or on the agent, AGENT_DEFAULT_USER_KEY_DAILY_COST_USD
    applies). Admins may set is_system_default to fund system agents for everyone
    without a usable personal default, or clear it with false. There is one shared
    default per provider; clearing or deleting it does not promote another key.

    Prefer entering keys in the web UI: a key passed in a tool call stays in the AI
    client's transcript.
    """
    user_id, is_admin = caller()
    if is_system_default is not None and not is_admin:
        raise ValueError("only admins can set or clear system default keys")
    with make_session() as db:
        existing = resolve_key_ref(db, user_id, key_ref) if key_ref is not None else None
        key_id = existing.id if existing else None
        provider = existing.provider if existing else provider
    if provider not in KEY_PROVIDERS:
        raise ValueError(f"provider must be one of {', '.join(KEY_PROVIDERS)}")
    label = checked_label(label, required=existing is None)
    secret = (key or "").strip()
    if existing is None and not secret:
        raise ValueError("key is required")
    cap = daily_cap(max_cost_usd_per_day) if max_cost_usd_per_day is not None else None
    with make_session() as db:
        if label is not None and label_taken(db, user_id, label, key_id):
            raise ValueError(f"you already have a key named {label!r}")
    if secret:
        key_checks_allowed(user_id)
        await check_key(provider, secret)
    with make_session() as db:
        row = (
            resolve_key_ref(db, user_id, key_id)
            if key_id is not None
            else LLMProviderKey(user_id=user_id, provider=provider)
        )
        db.add(row)
        if label is not None:
            row.label = label
        if secret:
            row.key = secret
            row.status, row.status_detail = KeyStatus.OK.value, None
        if max_cost_usd_per_day is not None:
            row.max_cost_usd_per_day = cap
        try:
            db.flush()
            if is_system_default is not None:
                if is_system_default:
                    db.query(LLMProviderKey).filter(
                        LLMProviderKey.provider == provider,
                        LLMProviderKey.is_system_default,
                        LLMProviderKey.id != row.id,
                    ).update({"is_system_default": False})
                    db.flush()
                row.is_system_default = is_system_default
            first = provider_defaults(db, user_id, provider).count() == 0
            if is_default or (is_default is None and first):
                make_default(db, row)
            elif is_default is False and row.is_default:
                row.is_default = False
                promote_default(db, user_id, provider, row.id)
            db.flush()
        except IntegrityError as e:
            raise ValueError(f"your {provider} keys changed at the same time; try again") from e
        db.refresh(row)
        return row.serialize()


@agent_mcp.tool()
@visible_when(require_scopes(SCOPE_WRITE))
async def key_delete(key_ref: int | str) -> dict:
    """Delete one of your provider keys. Agents that pin it (`pinned_by`) fail
    until you pick another key for them. Deleting the default makes your most
    recently used other key for the provider the default (`new_default`)."""
    user_id, _ = caller()
    with make_session() as db:
        found = resolve_key_ref(db, user_id, key_ref)
        key_id, provider = found.id, found.provider
        definitions = db.query(AgentDefinition).filter(AgentDefinition.user_id == user_id)
        pinned_by = sorted(d.name for d in definitions if d.spec.get("provider_key") == key_id)
        db.delete(found)
        successor = promote_default(db, user_id, provider, key_id)
        new_default = successor.id if successor else None
    return {"deleted": key_id, "pinned_by": pinned_by, "new_default": new_default}
