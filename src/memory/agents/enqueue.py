"""The one way any trigger starts an agent run."""

from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from memory.agents.caps import CapExceeded, check_run_rate, resolve_run_cap
from memory.agents.provider_keys import KeyUnavailable, choose_key
from memory.agents.spec import (
    MAX_TEXT_CHARS,
    AgentSpec,
    model_info,
    resolve_max_output_tokens,
    spec_errors,
)
from memory.common.celery_app import RUN_AGENT, app
from memory.common.db.connection import DBSession
from memory.api.auth import TokenLookup, system_scopes
from memory.common.db.models import (
    AgentDefinition,
    AgentRun,
    APIKey,
    ExternalMCPServer,
    LLMProviderKey,
    ServerStatus,
    User,
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
    dedupe_key: str | None = None


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
    opens the server, so refusing on it would block the agent for good."""
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
    return start_problems(db, user_id, spec)[1] + missing_tools(db, user_id, spec)


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


def enqueue_run(
    db: DBSession,
    *,
    user_id: int,
    spec: AgentSpec,
    input: str,
    trigger: TriggerInfo,
    definition: AgentDefinition | None = None,
    api_key: APIKey | None = None,
) -> AgentRun:
    """Validate, cap, insert a pending run and dispatch it.

    Tool visibility is not checked here (it needs the owner's MCP identity):
    interactive callers check it up front and the runner re-checks at start.
    Runs of a definition hold its row lock until the new run is committed,
    so dedupe and the rate cap don't race; the unique partial index on
    (definition_id, dedupe_key) remains the backstop.
    """
    if len(input) > MAX_TEXT_CHARS:
        raise ValueError(f"input is longer than {MAX_TEXT_CHARS} characters")
    user = db.get(User, user_id)
    if user is None:
        raise ValueError(f"unknown user {user_id}")
    # The scopes this principal has over HTTP, so a run can do what its owner could.
    scopes = system_scopes(TokenLookup(user=user, api_key_record=api_key))
    # Checked here too so cron doesn't start runs that would fail at hub open.
    provider_key, problems = start_problems(db, user_id, spec)
    if problems or provider_key is None:
        raise SpecInvalid(problems)
    if definition is not None:
        # Serialises a definition's enqueues until this run is committed, so
        # concurrent starts can't all pass the rate check or the dedupe lookup.
        db.query(AgentDefinition).filter(
            AgentDefinition.id == definition.id
        ).with_for_update().one()
    if definition is not None and trigger.dedupe_key is not None:
        existing = (
            db.query(AgentRun)
            .filter(
                AgentRun.definition_id == definition.id,
                AgentRun.dedupe_key == trigger.dedupe_key,
            )
            .one_or_none()
        )
        if existing is not None:
            return existing
    if definition is not None:
        check_run_rate(db, definition, spec.max_runs_per_hour)
    max_cost = resolve_run_cap(
        db,
        run_cap=spec.max_cost_usd,
        spec_daily_cap=spec.max_cost_usd_per_day if definition is not None else None,
        definition=definition,
        api_key=api_key,
        provider_key=provider_key,
    )
    output_tokens = resolve_max_output_tokens(spec, model_info(spec.model), max_cost)
    if output_tokens < 1:
        raise CapExceeded(
            f"${max_cost} left in this run's budget can't pay for any output on {spec.model}"
        )
    snapshot = spec.model_copy(update={"max_output_tokens": output_tokens})
    run = AgentRun(
        user_id=user_id,
        definition_id=definition.id if definition else None,
        spec=snapshot.model_dump(mode="json"),
        input=input,
        max_cost_usd=max_cost,
        trigger_kind=trigger.kind,
        trigger_ref=trigger.ref,
        dedupe_key=trigger.dedupe_key,
        api_key_id=api_key.id if api_key else None,
        provider_key_id=provider_key.id,
        scopes=scopes,
    )
    db.add(run)
    db.commit()
    # The run id doubles as the Celery task id, so cancellation can revoke by run id.
    app.send_task(RUN_AGENT, args=[run.id], task_id=run.id)
    return run
