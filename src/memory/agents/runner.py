"""The agent loop.

Provider-neutral: it sees only NeutralMessages, Capabilities and a ToolHub.
Every step is persisted as it happens so the dashboard can follow a live run.
"""

import asyncio
import json
import logging
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func

from memory.agents.call import SUBMIT_RESULT, submit_result_tool
from memory.agents.provider_keys import (
    KeyUnavailable,
    key_for_run,
    notify_key_rejected,
    reject_key,
    rejected_message,
)
from memory.agents.spec import (
    AgentSpec,
    finish_tool_names,
    generation_params,
    output_rate,
    result_errors,
)
from memory.agents.toolhub import ToolHub, open_toolhub
from memory.agents.trim import View, trim
from memory.common import settings
from memory.common.db.connection import make_session
from memory.common.db.models import (
    AgentDefinition,
    AgentRun,
    AgentRunStep,
    LLMProviderKey,
    UserSession,
)
from memory.common.db.models.agents import RunStatus
from memory.common.llms.neutral import (
    MTOK,
    AgentUsage,
    Block,
    ModelInfo,
    NeutralMessage,
    Replay,
    Text,
    ToolCall,
    ToolResult,
)
from memory.common.llms.registry import get_provider
from memory.common.llms.tools import ToolDefinition
from memory.common.mcp_oauth import MCPServerError
from memory.common.tokens import CHARS_PER_TOKEN

logger = logging.getLogger(__name__)

MAX_REMINDERS = 2
KEEP_RECENT_RESULTS = 3
SESSION_GRACE = timedelta(minutes=5)
HubFactory = Callable[..., AbstractAsyncContextManager[ToolHub]]


class RunEnded(Exception):
    """Ends the loop with a final status other than done."""

    def __init__(self, status: RunStatus, error: str):
        super().__init__(error)
        self.status = status
        self.error = error


@dataclass(frozen=True)
class Claim:
    spec: dict[str, Any]
    user_id: int
    scopes: list[str]
    input: str
    max_cost: Decimal
    # When the run's definition was last saved; None for ad-hoc runs.
    saved_at: datetime | None = None
    provider_key_id: int | None = None


def now() -> datetime:
    return datetime.now(timezone.utc)


def claim(run_id: str) -> Claim | None:
    with make_session() as db:
        run = db.get(AgentRun, run_id, with_for_update=True)
        if run is None:
            logger.error("agent run %s not found", run_id)
            return None
        if run.status != RunStatus.PENDING:
            # A redelivery after worker loss; SWEEP_AGENT_RUNS settles it, never a re-run.
            logger.warning(
                "agent run %s is %s, not pending; not executing", run_id, run.status
            )
            return None
        run.status = RunStatus.RUNNING.value
        run.started_at = now()
        definition = db.get(AgentDefinition, run.definition_id) if run.definition_id else None
        claimed = Claim(
            run.spec,
            run.user_id,
            list(run.scopes),
            run.input,
            run.max_cost_usd,
            definition.updated_at if definition else None,
            run.provider_key_id,
        )
        db.commit()
        return claimed


def mint_session(user_id: int, timeout_sec: int) -> str:
    with make_session() as db:
        session = UserSession(
            user_id=user_id,
            expires_at=(now() + timedelta(seconds=timeout_sec) + SESSION_GRACE).replace(
                tzinfo=None
            ),
        )
        db.add(session)
        db.commit()
        return session.id


def delete_session(token: str) -> None:
    with make_session() as db:
        db.query(UserSession).filter(UserSession.id == token).delete()
        db.commit()


def append_step(
    run_id: str,
    kind: str,
    content: list[Any],
    replay: Replay | None = None,
    usage: AgentUsage | None = None,
    cost: Decimal | None = None,
) -> Decimal:
    """Persist one step; model turns also roll usage and cost into the run. Returns the run's cost."""
    with make_session() as db:
        run = db.get(AgentRun, run_id, with_for_update=True)
        assert run is not None
        seq = (
            db.query(func.coalesce(func.max(AgentRunStep.seq) + 1, 0))
            .filter(AgentRunStep.run_id == run_id)
            .scalar()
        )
        db.add(
            AgentRunStep(
                run_id=run_id,
                seq=seq,
                kind=kind,
                content=[b.model_dump(mode="json") for b in content],
                replay=replay,
                usage=usage.to_dict() if usage else None,
                cost_usd=cost,
            )
        )
        if usage is not None and cost is not None:
            run.usage = (AgentUsage.from_dict(run.usage) + usage).to_dict()
            run.cost_usd = run.cost_usd + cost
            run.num_turns += 1
        total = run.cost_usd
        db.commit()
        return total


def check_cancelled(run_id: str) -> None:
    with make_session() as db:
        requested = (
            db.query(AgentRun.cancel_requested_at)
            .filter(AgentRun.id == run_id)
            .scalar()
        )
    if requested is not None:
        raise RunEnded(RunStatus.CANCELLED, "cancelled by user")


def check_budget(spec: AgentSpec, turns: int, cost: Decimal, max_cost: Decimal) -> None:
    if cost >= max_cost:
        raise RunEnded(
            RunStatus.BUDGET_EXCEEDED,
            f"cost ${cost:.4f} reached this run's cap of ${max_cost:.4f}",
        )
    if turns >= spec.max_turns:
        raise RunEnded(
            RunStatus.BUDGET_EXCEEDED,
            f"max_turns ({spec.max_turns}) reached without finishing",
        )


def output_allowance(
    info: ModelInfo, estimate: int, cost: Decimal, max_cost: Decimal, max_output: int
) -> int:
    """The most output the next turn may produce without passing the run's cap."""
    input_cost = info.pricing.cost(AgentUsage(input_tokens=estimate))
    affordable = int((max_cost - cost - input_cost) * MTOK / output_rate(info))
    if affordable < 1:
        raise RunEnded(
            RunStatus.BUDGET_EXCEEDED,
            f"cost ${cost:.4f} plus the next turn's estimated input ${input_cost:.4f} "
            f"reached this run's cap of ${max_cost:.4f}",
        )
    return min(max_output, affordable)


def run_key(claimed: Claim) -> LLMProviderKey:
    try:
        return key_for_run(claimed.provider_key_id)
    except KeyUnavailable as e:
        raise RunEnded(RunStatus.FAILED, str(e)) from e


def key_refused(key: LLMProviderKey) -> RunEnded:
    """Ends a run whose provider refused its user's key, notifying only on the first refusal."""
    if reject_key(key.id, key.key):
        notify_key_rejected(key)
    return RunEnded(RunStatus.FAILED, rejected_message(key))


def finish(
    run_id: str, status: RunStatus, error: str | None, result: dict[str, Any] | None
) -> None:
    with make_session() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        run.status = status.value
        run.error = error
        run.result = result
        run.finished_at = now()
        db.commit()


def truncate(text: str) -> str:
    limit = settings.AGENT_TOOL_RESULT_MAX_CHARS
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n[truncated: {len(text) - limit} more chars]"


async def run_tool(
    hub: ToolHub, call: ToolCall, result_schema: dict[str, Any] | None
) -> ToolResult:
    if call.name == SUBMIT_RESULT and result_schema is not None:
        if errors := result_errors(result_schema, call.input):
            text = f"invalid result: {'; '.join(errors)}"
            return ToolResult(
                tool_call_id=call.id, name=call.name, text=text, is_error=True
            )
        return ToolResult(tool_call_id=call.id, name=call.name, text="result accepted")
    started = time.monotonic()
    try:
        result = await hub.call(call)
    except MCPServerError as e:
        raise RunEnded(RunStatus.FAILED, str(e)) from e
    elapsed = int((time.monotonic() - started) * 1000)
    return result.model_copy(
        update={"text": truncate(result.text), "duration_ms": elapsed}
    )


def truncated_results(message: NeutralMessage) -> NeutralMessage:
    """Results the provider ran itself get the same size limit as ours."""
    content = [
        b.model_copy(update={"text": truncate(b.text)})
        if isinstance(b, ToolResult)
        else b
        for b in message.content
    ]
    return message.model_copy(update={"content": content})


def prompt_chars(system: str, input: str, tools: list[ToolDefinition]) -> int:
    """Size of the first request, which no reported usage covers yet."""
    schemas = [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in tools
    ]
    return len(system) + len(input) + len(json.dumps(schemas))


def finish_result(call: ToolCall, result: ToolResult) -> dict[str, Any]:
    if call.name == SUBMIT_RESULT:
        return {"data": call.input}
    return {"tool": call.name, "input": call.input, "output": result.text}


def remind(run_id: str, view: View, finish_tools: set[str]) -> int:
    names = ", ".join(sorted(finish_tools))
    message = NeutralMessage(
        role="user",
        content=[
            Text(
                text=f"You have not finished the task. Finish by calling one of: {names}."
            )
        ],
    )
    view.append(message)
    append_step(run_id, "user", message.content)
    return len(message.text()) // CHARS_PER_TOKEN


async def run_loop(
    run_id: str, spec: AgentSpec, claimed: Claim, token: str, hub_factory: HubFactory
) -> dict[str, Any]:
    key = run_key(claimed)
    provider = get_provider(spec.model, api_key=key.key)
    info = provider.model_info(provider.model)
    params = generation_params(spec)
    finish_tools = finish_tool_names(spec)
    async with hub_factory(
        spec=spec,
        user_id=claimed.user_id,
        scopes=claimed.scopes,
        session_token=token,
        capabilities=info.capabilities,
        saved_at=claimed.saved_at,
    ) as hub:
        # Fixed for the whole run: the tool list is part of the cached prefix.
        submit = [submit_result_tool(spec.result_schema)] if spec.result_schema else []
        tools = sorted(hub.tools() + submit, key=lambda t: t.name)
        view = View()
        first = NeutralMessage(role="user", content=[Text(text=claimed.input)])
        view.append(first)
        append_step(run_id, "user", first.content)
        if notices := hub.notices():
            append_step(run_id, "notice", [Text(text=n) for n in notices])
        estimate = (
            prompt_chars(spec.system_prompt, claimed.input, tools) // CHARS_PER_TOKEN
        )
        reminders = turns = 0
        reminded = False
        cost = Decimal(0)

        while True:
            check_cancelled(run_id)
            stubbed = trim(
                view,
                estimated_tokens=estimate,
                trim_at=settings.AGENT_CONTEXT_TRIM_AT_TOKENS,
                trim_to=settings.AGENT_CONTEXT_TRIM_TO_TOKENS,
                keep_recent=KEEP_RECENT_RESULTS,
            )
            if stubbed:
                notice = [
                    Text(
                        text=f"trimmed {stubbed} tool results; reasoning history reset"
                    )
                ]
                append_step(run_id, "notice", notice)
            allowance = output_allowance(
                info, estimate, cost, claimed.max_cost, params.max_output_tokens
            )
            try:
                completion = await provider.acomplete(
                    system=spec.system_prompt,
                    messages=view.messages,
                    replay=view.replay,
                    tools=tools,
                    tool_choice=(
                        "required"
                        if reminded and info.capabilities.tool_choice_required
                        else "auto"
                    ),
                    params=replace(params, max_output_tokens=allowance),
                )
            except provider.KEY_REJECTED as e:
                raise key_refused(key) from e
            except provider.KEY_FORBIDDEN as e:
                logger.warning("agent run %s: %s refused the request: %s", run_id, spec.model, e)
                raise RunEnded(
                    RunStatus.FAILED,
                    f"the provider refused this request to {spec.model}; the API key "
                    "may not have access to this model or feature",
                ) from e
            turns += 1
            reminded = False
            message = truncated_results(completion.message)
            view.append(message, completion.replay)
            cost = append_step(
                run_id,
                "assistant",
                message.content,
                completion.replay,
                completion.usage,
                completion.cost,
            )
            estimate = completion.usage.context_tokens + completion.usage.output_tokens
            if (
                completion.stop_reason == "max_output_tokens"
                and allowance < params.max_output_tokens
            ):
                raise RunEnded(
                    RunStatus.BUDGET_EXCEEDED,
                    f"the model hit this turn's output limit ({allowance} tokens), which the "
                    "run's remaining budget set; raise the run's cost cap",
                )
            if completion.stop_reason == "max_output_tokens":
                raise RunEnded(
                    RunStatus.FAILED,
                    f"the model hit max_output_tokens ({params.max_output_tokens}) mid-turn; "
                    "raise max_output_tokens",
                )
            if completion.stop_reason == "context_exceeded":
                raise RunEnded(
                    RunStatus.FAILED,
                    "the conversation filled the model's context window; lower "
                    "AGENT_CONTEXT_TRIM_AT_TOKENS or max_output_tokens",
                )
            if completion.stop_reason == "refused":
                raise RunEnded(RunStatus.FAILED, "the model refused the request")
            if completion.stop_reason == "paused":
                # Resumed by the next request, which must pass the caps like any other.
                check_budget(spec, turns, cost, claimed.max_cost)
                continue

            pending = completion.message.tool_calls()
            if not pending:
                if not finish_tools:
                    return {"text": completion.message.text()}
                check_budget(spec, turns, cost, claimed.max_cost)
                reminders += 1
                if reminders > MAX_REMINDERS:
                    names = ", ".join(sorted(finish_tools))
                    raise RunEnded(
                        RunStatus.FAILED,
                        f"the model stopped without calling a finish tool ({names}) "
                        f"after {MAX_REMINDERS} reminders",
                    )
                estimate += remind(run_id, view, finish_tools)
                reminded = True
                continue

            results: list[Block] = []
            finished: tuple[ToolCall, ToolResult] | None = None
            try:
                for call in pending:
                    if finished is not None:
                        results.append(
                            ToolResult(
                                tool_call_id=call.id,
                                name=call.name,
                                text="skipped: the run already finished",
                                is_error=True,
                            )
                        )
                        continue
                    check_cancelled(run_id)
                    result = await run_tool(hub, call, spec.result_schema)
                    results.append(result)
                    if call.name in finish_tools and not result.is_error:
                        finished = (call, result)
            finally:
                # Persist whatever ran, even when cancellation stops the batch midway.
                if results:
                    view.append(NeutralMessage(role="user", content=results))
                    append_step(run_id, "tool_results", results)
            estimate += (
                sum(len(r.text) for r in results if isinstance(r, ToolResult))
                // CHARS_PER_TOKEN
            )
            if finished is not None:
                return finish_result(*finished)
            check_budget(spec, turns, cost, claimed.max_cost)


async def execute_run(run_id: str, hub_factory: HubFactory = open_toolhub) -> None:
    claimed = claim(run_id)
    if claimed is None:
        return
    status, error, result = RunStatus.FAILED, None, None
    token = None
    timeout_sec = None
    try:
        spec = AgentSpec.model_validate(claimed.spec)
        timeout_sec = spec.timeout_sec
        token = mint_session(claimed.user_id, spec.timeout_sec)
        async with asyncio.timeout(spec.timeout_sec):
            result = await run_loop(run_id, spec, claimed, token, hub_factory)
        status = RunStatus.DONE
    except RunEnded as e:
        status, error = e.status, e.error
    except TimeoutError:
        status = RunStatus.BUDGET_EXCEEDED
        error = f"timeout: the run exceeded timeout_sec={timeout_sec}"
    except Exception as e:
        logger.exception("agent run %s failed", run_id)
        error = f"{type(e).__name__}: {e}"
    finally:
        if token is not None:
            delete_session(token)
        finish(run_id, status, error, result)
