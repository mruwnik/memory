"""The one way any trigger starts an agent run."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from memory.agents.caps import (
    CapExceeded,
    check_hourly_rate,
    check_run_rate,
    check_trigger_rate,
    children_cost,
    resolve_run_cap,
)
from memory.agents.provider_keys import KeyUnavailable, choose_key
from memory.agents.spec import (
    MAX_TEXT_CHARS,
    AgentSpec,
    exposed_tools,
    model_info,
    resolve_max_output_tokens,
    spec_errors,
    starts_on_server,
    uses_server,
)
from memory.api.auth import TokenLookup, system_scopes
from memory.common import settings
from memory.common.access_control import narrow_scopes
from memory.common.celery_app import RUN_AGENT, app
from memory.common.db.connection import DBSession
from memory.common.db.models import (
    AgentDefinition,
    AgentRun,
    AgentTrigger,
    AgentTriggerMatch,
    APIKey,
    ExternalMCPServer,
    LLMProviderKey,
    ServerStatus,
    User,
)
from memory.common.db.models.agents import (
    EXECUTOR_SERVER,
    EXECUTOR_WORKERS,
    TERMINAL_STATUSES,
    RunStatus,
)
from memory.common.mcp_oauth import unusable_message
from memory.common.module_settings import enabled_modules
from memory.common.modules import server_module


class SpecInvalid(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass(frozen=True)
class TriggerInfo:
    kind: str  # manual | mcp | cron | ingest | relative | webhook | discord | chain
    ref: dict[str, Any] = field(default_factory=dict)
    # Unique per trigger when trigger_id is set, else per definition.
    dedupe_key: str | None = None
    trigger_id: int | None = None


def server_errors(db: DBSession, user_id: int, spec: AgentSpec) -> list[str]:
    """Why a run of `spec` by `user_id` couldn't use its external servers."""
    errors = []
    for external in spec.mcp_servers:
        server = db.get(ExternalMCPServer, external.server_id)
        if server is None or server.user_id != user_id:
            errors.append(f"MCP server {external.server_id} is not yours")
        elif server.status != ServerStatus.CONNECTED:
            errors.append(unusable_message(server))
    return errors


def disabled_server_errors(spec: AgentSpec) -> list[str]:
    """Only `spec.tools` is checked: external MCP tools are `mcp_<id>_...` and never match a server."""
    # memory.api.MCP mounts the agent server, which imports this module
    from memory.api.MCP.servers import effective_disabled_servers, tool_server

    disabled = effective_disabled_servers()
    off: dict[str, list[str]] = {}
    for tool in spec.tools:
        server = tool_server(tool)
        if server is not None and server in disabled:
            off.setdefault(server, []).append(tool)
    return [
        f"tools unavailable, {cause(server)}: {', '.join(tools)}"
        for server, tools in off.items()
    ]


def cause(server: str) -> str:
    module = server_module(server)
    if module is not None and module not in enabled_modules():
        return f"module {module} is off"
    return f"server {server} is disabled"


def missing_tools(db: DBSession, user_id: int, spec: AgentSpec) -> list[str]:
    problems = []
    for external in spec.mcp_servers:
        server = db.get(ExternalMCPServer, external.server_id)
        if server is None or server.user_id != user_id:
            continue
        offered = {t["name"] for t in server.tools}
        problems += [
            f"tool '{t}' is no longer offered by MCP server '{server.name}'"
            for t in external.tools
            if t not in offered
        ]
    return problems


def start_problems(
    db: DBSession, user_id: int, spec: AgentSpec
) -> tuple[LLMProviderKey | None, list[str]]:
    """The key that pays for a run of `spec`, and what stops one starting. Tools a
    server stopped offering are left out: the cached list only refreshes when a run
    opens the server, so refusing on it would block the agent for good. A spec the
    server never runs needs no key or servers."""
    if not uses_server(spec):
        return None, spec_errors(spec)
    try:
        key, key_problems = choose_key(db, user_id, spec.model, spec.provider_key), []
    except KeyUnavailable as e:
        key, key_problems = None, [str(e)]
    return key, (
        spec_errors(spec)
        + disabled_server_errors(spec)
        + key_problems
        + server_errors(db, user_id, spec)
    )


def run_problems(db: DBSession, user_id: int, spec: AgentSpec) -> list[str]:
    """Why `spec` can't run as configured; empty when it can."""
    problems = start_problems(db, user_id, spec)[1]
    return problems + missing_tools(db, user_id, spec) if uses_server(spec) else problems


def stored_spec_problems(db: DBSession, user_id: int, spec: dict[str, Any]) -> list[str]:
    """Why a stored spec can't run, including one that no longer validates."""
    try:
        parsed = AgentSpec.model_validate(spec)
    except ValidationError as e:
        details = "; ".join(
            f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()
        )
        return [f"invalid spec: {details}"]
    return run_problems(db, user_id, parsed)


def child_problems(parent: AgentRun, spec: AgentSpec) -> list[str]:
    """Why `parent` can't start a run of `spec`: a child stays inside its parent's
    depth limit and tools."""
    problems = []
    if parent.status in TERMINAL_STATUSES:
        problems.append(f"the parent run is {parent.status}")
    if parent.depth + 1 > settings.AGENT_MAX_SPAWN_DEPTH:
        problems.append(
            f"runs started by runs nest at most {settings.AGENT_MAX_SPAWN_DEPTH} deep "
            "(AGENT_MAX_SPAWN_DEPTH)"
        )
    if spec.placement != "server_only":
        problems.append("runs started by runs execute on the server (placement server_only)")
    allowed = set(exposed_tools(AgentSpec.model_validate(parent.spec)))
    if extra := [t for t in exposed_tools(spec) if t not in allowed]:
        problems.append(f"tools the parent run doesn't have: {', '.join(extra)}")
    return problems


def parent_left(db: DBSession, parent: AgentRun) -> Decimal:
    """What `parent` can still hand a child: its cap less its own and its children's cost.
    Locks the parent until the child is committed, so siblings can't share one remainder."""
    db.query(AgentRun).filter(AgentRun.id == parent.id).with_for_update().one()
    db.refresh(parent)
    left = parent.max_cost_usd - parent.cost_usd - children_cost(db, parent.id)
    if left <= 0:
        raise CapExceeded("the parent run's budget is spent or held by its other children")
    return left


def enqueue_run(
    db: DBSession,
    *,
    user_id: int,
    spec: AgentSpec,
    input: str,
    trigger: TriggerInfo,
    definition: AgentDefinition | None = None,
    agent: str | None = None,
    api_key: APIKey | None = None,
    parent: AgentRun | None = None,
    deliveries: Sequence[AgentTriggerMatch] = (),
) -> AgentRun:
    """Validate, cap, insert a pending run and dispatch it.

    Tool visibility is not checked here (it needs the owner's MCP identity):
    interactive callers check it up front and the runner re-checks at start.
    Runs of a trigger or definition hold their row locks until the new run is
    committed, so dedupe and the rate caps don't race; the unique partial
    indexes on (trigger_id, dedupe_key) and (definition_id, dedupe_key) remain
    the backstop.

    A `parent` run's child gets at most its scopes and tools, one more level of
    depth, and a cap no larger than what the parent has left (half of it, unless the
    child's spec sets max_cost_usd), which it holds until it finishes.

    `deliveries`, the trigger's claims the run is for, are linked to a new run in
    its transaction.
    """
    if len(input) > MAX_TEXT_CHARS:
        raise ValueError(f"input is longer than {MAX_TEXT_CHARS} characters")
    user = db.get(User, user_id)
    if user is None:
        raise ValueError(f"unknown user {user_id}")
    # The scopes this principal has over HTTP, so a run can do what its owner could.
    scopes = system_scopes(TokenLookup(user=user, api_key_record=api_key))
    if parent is not None:
        scopes = narrow_scopes(parent.scopes, scopes)
    # Checked here too so cron doesn't start runs that would fail at hub open.
    provider_key, problems = start_problems(db, user_id, spec)
    if parent is not None:
        problems += child_problems(parent, spec)
    if problems or (provider_key is None and uses_server(spec)):
        raise SpecInvalid(problems)
    # Serialise a trigger's and a definition's enqueues until this run is committed,
    # so concurrent starts can't all pass the rate checks or the dedupe lookup. The
    # trigger goes first, as in fire_trigger.
    subscription = (
        None
        if trigger.trigger_id is None
        # NO KEY UPDATE, as triggers.lock_trigger: claims being inserted hold key-share locks on it.
        else db.get(AgentTrigger, trigger.trigger_id, with_for_update={"key_share": True})
    )
    if trigger.trigger_id is not None and subscription is None:
        raise ValueError(f"unknown trigger {trigger.trigger_id}")
    if definition is not None:
        db.query(AgentDefinition).filter(
            AgentDefinition.id == definition.id
        ).with_for_update().one()
    if (existing := duplicate(db, trigger, definition)) is not None:
        return existing
    if subscription is not None:
        check_trigger_rate(db, subscription)
    if definition is not None:
        check_run_rate(db, definition, spec.max_runs_per_hour)
    elif agent is not None:
        check_hourly_rate(
            db,
            (AgentRun.user_id == user_id) & (AgentRun.agent == agent),
            spec.max_runs_per_hour,
            "agent",
            "max_runs_per_hour",
        )
    if provider_key is None or not starts_on_server(spec):
        # Workers spend their own credentials; a fallback to the server is budgeted then.
        max_cost = Decimal(0)
    else:
        max_cost = server_budget(db, spec, definition, api_key, provider_key, parent)
    run = AgentRun(
        user_id=user_id,
        definition_id=definition.id if definition else None,
        agent=agent,
        spec=spec.model_dump(mode="json"),
        input=input,
        max_cost_usd=max_cost,
        trigger_kind=trigger.kind,
        trigger_ref=trigger.ref,
        dedupe_key=trigger.dedupe_key,
        trigger_id=trigger.trigger_id,
        api_key_id=api_key.id if api_key else None,
        provider_key_id=provider_key.id if provider_key else None,
        scopes=scopes,
        parent_run_id=parent.id if parent else None,
        depth=parent.depth + 1 if parent else 0,
        executor=EXECUTOR_SERVER if starts_on_server(spec) else EXECUTOR_WORKERS,
    )
    db.add(run)
    for d in deliveries:
        d.run = run
    db.commit()
    if run.executor == EXECUTOR_SERVER:
        dispatch(run.id)
    return run


def duplicate(
    db: DBSession, trigger: TriggerInfo, definition: AgentDefinition | None
) -> AgentRun | None:
    """The run already started under `trigger`'s dedupe key, if any."""
    if trigger.dedupe_key is None:
        return None
    if trigger.trigger_id is not None:
        scope = AgentRun.trigger_id == trigger.trigger_id
    elif definition is not None:
        scope = (AgentRun.definition_id == definition.id) & AgentRun.trigger_id.is_(None)
    else:
        return None
    return (
        db.query(AgentRun).filter(scope, AgentRun.dedupe_key == trigger.dedupe_key).one_or_none()
    )


def dispatch(run_id: str) -> None:
    # The run id doubles as the Celery task id, so cancellation can revoke by run id.
    app.send_task(RUN_AGENT, args=[run_id], task_id=run_id)


def server_budget(
    db: DBSession,
    spec: AgentSpec,
    definition: AgentDefinition | None,
    api_key: APIKey | None,
    provider_key: LLMProviderKey,
    parent: AgentRun | None,
) -> Decimal:
    """The run's cap, which must buy at least one output token."""
    max_cost = resolve_run_cap(
        db,
        run_cap=spec.max_cost_usd,
        spec_daily_cap=spec.max_cost_usd_per_day if definition is not None else None,
        definition=definition,
        api_key=api_key,
        provider_key=provider_key,
    )
    if parent is not None:
        left = parent_left(db, parent)
        # Without its own cap a child gets half, so its parent can still afford to wait for it.
        max_cost = min(max_cost, left if spec.max_cost_usd is not None else left / 2)
    if resolve_max_output_tokens(spec, model_info(spec.model), max_cost) < 1:
        raise CapExceeded(
            f"${max_cost} left in this run's budget can't pay for any output on {spec.model}"
        )
    return max_cost


def fall_back_to_server(db: DBSession, run: AgentRun) -> None:
    """Hand a pending worker run to the server, checked and budgeted as of now: its
    owner's key, servers and pools may have changed since submit. Fails the run if the
    server can't take it. The caller holds the run's lock and commits; dispatch follows
    the commit."""
    spec = AgentSpec.model_validate(run.spec)
    provider_key, problems = start_problems(db, run.user_id, spec)
    budget = None
    if not problems and provider_key is not None:
        definition = db.get(AgentDefinition, run.definition_id) if run.definition_id else None
        api_key = db.get(APIKey, run.api_key_id) if run.api_key_id else None
        try:
            budget = server_budget(db, spec, definition, api_key, provider_key, None)
        except CapExceeded as e:
            problems = [str(e)]
    now = datetime.now(timezone.utc)
    if budget is None or provider_key is None:
        run.status = RunStatus.FAILED.value
        run.error = (
            f"no worker claimed it within {spec.fallback_after} minutes, and the server "
            f"can't run it: {'; '.join(problems)}"
        )
        run.finished_at = now
        return
    run.provider_key_id, run.max_cost_usd = provider_key.id, budget
    run.executor = EXECUTOR_SERVER
    run.placed_at = now
