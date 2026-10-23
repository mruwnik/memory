"""The one way any trigger starts an agent run."""

from dataclasses import dataclass, field
from typing import Any

from memory.agents.caps import CapExceeded, resolve_run_cap
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
from memory.common.db.models import AgentDefinition, AgentRun, APIKey, User


class SpecInvalid(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass(frozen=True)
class TriggerInfo:
    kind: str  # manual | mcp | cron | ingest | relative | webhook | discord | chain | check
    ref: dict[str, Any] = field(default_factory=dict)
    dedupe_key: str | None = None


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
    The dedupe check-then-insert can race; the unique partial index is the
    backstop (a racing duplicate raises IntegrityError).
    """
    if len(input) > MAX_TEXT_CHARS:
        raise ValueError(f"input is longer than {MAX_TEXT_CHARS} characters")
    if errors := spec_errors(spec):
        raise SpecInvalid(errors)
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

    max_cost = resolve_run_cap(
        db,
        user_id=user_id,
        run_cap=spec.max_cost_usd,
        spec_daily_cap=spec.max_cost_usd_per_day if definition is not None else None,
        definition=definition,
        api_key=api_key,
    )
    output_tokens = resolve_max_output_tokens(spec, model_info(spec.model), max_cost)
    if output_tokens < 1:
        raise CapExceeded(
            f"${max_cost} left in this run's budget can't pay for any output on {spec.model}"
        )
    snapshot = spec.model_copy(update={"max_output_tokens": output_tokens})
    user = db.get(User, user_id)
    if user is None:
        raise ValueError(f"unknown user {user_id}")
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
        # The scopes this principal has over HTTP, so a run can do what its owner could.
        scopes=system_scopes(TokenLookup(user=user, api_key_record=api_key)),
    )
    db.add(run)
    db.commit()
    # The run id doubles as the Celery task id, so cancellation can revoke by run id.
    app.send_task(RUN_AGENT, args=[run.id], task_id=run.id)
    return run
