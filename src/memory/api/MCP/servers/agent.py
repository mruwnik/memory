"""MCP subserver for agent definitions and runs.

Every tool acts as the caller. A run always executes as the definition's owner
(only owners may run a definition), with the caller's API-key scopes when one
was used.
"""

import asyncio
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from memory.agents.caps import CapExceeded
from memory.agents.enqueue import SpecInvalid, TriggerInfo, enqueue_run
from memory.agents.mcp_client import memory_tools
from memory.agents.spec import (
    DEFAULT_AGENT_MODEL,
    MAX_NAME_CHARS,
    AgentSpec,
    is_denied,
    spec_errors,
)
from memory.api.auth import lookup_api_key
from memory.api.MCP.access import get_mcp_current_user
from memory.api.MCP.visibility import require_scopes, visible_when
from memory.common import settings
from memory.common.access_control import has_admin_scope
from memory.common.celery_app import app as celery_app
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import (
    AgentDefinition,
    AgentRun,
    AgentRunStep,
)
from memory.common.db.models.agents import TERMINAL_STATUSES, RunStatus
from memory.common.llms.neutral import BUILTIN_TOOLS, Capabilities
from memory.common.llms.registry import PROVIDERS, all_models, split_model
from memory.common.scopes import SCOPE_AGENTS

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
    """Delete an agent definition. Its past runs keep their frozen spec."""
    user_id, _ = caller()
    with make_session() as db:
        db.delete(owned_definition(db, definition_id, user_id))
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
        result = []
        for d in definitions:
            last = (
                db.query(AgentRun)
                .filter(AgentRun.definition_id == d.id)
                .order_by(AgentRun.created_at.desc())
                .first()
            )
            result.append(
                d.serialize() | {"last_run": last.serialize() if last else None}
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
        },
        "default_model": DEFAULT_AGENT_MODEL,
    }
