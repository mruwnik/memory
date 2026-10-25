"""The one way memory calls an LLM: one turn of an agent, paid with a user's key.

System agents are prompt-only, so a call is one provider-neutral turn; an agent
with a result schema answers through a forced submit_result call, which strict
tool use holds to the schema. Worker-safe: no API imports.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from functools import partial
from typing import Any

from memory.agents import caps
from memory.agents.provider_keys import (
    Credential,
    notify_key_rejected,
    reject_key,
    rejected_message,
)
from memory.agents.system import SystemAgent, system_agent
from memory.common.db.connection import make_session
from memory.common.db.models import AgentRun, AgentRunStep, KeyStatus, LLMProviderKey
from memory.common.db.models.agents import RunStatus
from memory.common.llms import PROVIDER_ERRORS
from memory.common.llms.neutral import Completion, NeutralMessage, Text, ToolCall
from memory.common.llms.registry import get_provider
from memory.common.llms.tools import ToolDefinition

logger = logging.getLogger(__name__)

SUBMIT_RESULT = "submit_result"
UNUSABLE_STOPS = {"max_output_tokens", "context_exceeded", "refused", "paused"}


class Record(StrEnum):
    FULL = "full"  # a run with its steps, which the user can open
    COST = "cost"  # only the spend, on the key's daily counter


class LLMError(Exception):
    """The call produced no result; the message is safe to show the key's owner."""


@dataclass(frozen=True)
class Result:
    text: str
    data: dict[str, Any] | None
    cost: Decimal
    run_id: str | None = None


def submit_result_tool(schema: dict[str, Any]) -> ToolDefinition:
    return ToolDefinition(
        name=SUBMIT_RESULT,
        description="Submit the final result of the task. A valid submission ends the run.",
        input_schema=schema,
        strict=True,
    )


def request_args(agent: SystemAgent, prompt: str) -> dict[str, Any]:
    """`acomplete` keyword arguments for one turn of `agent` on `prompt`."""
    tools = [submit_result_tool(agent.result_schema)] if agent.result_schema else []
    return {
        "system": agent.system_prompt,
        "messages": [NeutralMessage(role="user", content=[Text(text=prompt)])],
        "replay": [None],
        "tools": tools,
        "tool_choice": "required" if tools else "auto",
        "params": agent.params(),
    }


def parse_result(
    agent: SystemAgent, completion: Completion
) -> tuple[str, dict[str, Any] | None]:
    if completion.stop_reason in UNUSABLE_STOPS:
        raise LLMError(f"{agent.slug} stopped early ({completion.stop_reason})")
    text = completion.message.text()
    if agent.result_schema is None:
        return text, None
    submitted = next(
        (
            b
            for b in completion.message.content
            if isinstance(b, ToolCall) and b.name == SUBMIT_RESULT
        ),
        None,
    )
    if submitted is None:
        raise LLMError(f"{agent.slug} answered without submitting a result")
    return text, submitted.input


def allowance(key: Credential) -> Decimal:
    with make_session() as db:
        row = db.get(LLMProviderKey, key.id)
        if row is None:
            raise LLMError(f"API key '{key.label}' was deleted")
        if row.status != KeyStatus.OK:
            raise LLMError(rejected_message(row))
        try:
            return caps.key_allowance(db, row)
        except caps.CapExceeded as e:
            raise LLMError(str(e)) from e


def refuse(key: Credential) -> None:
    if reject_key(key.id, key.key):
        notify_key_rejected(key)


def charge(key: Credential, agent: SystemAgent, cost: Decimal) -> None:
    with make_session() as db:
        caps.record_spend(db, key_id=key.id, agent=agent.slug, cost=cost)
        db.commit()


def record_run(
    *,
    user: int,
    key: Credential,
    agent: SystemAgent,
    model: str,
    prompt: str,
    completion: Completion,
    data: dict[str, Any] | None,
    error: str | None,
    max_cost: Decimal,
    started: datetime,
) -> str:
    """Keep the turn as a finished run, which the key's daily pool already counts."""
    if error:
        result = None
    elif data is not None:
        result = {"data": data}
    else:
        result = {"text": completion.message.text()}
    with make_session() as db:
        run = AgentRun(
            user_id=user,
            definition_id=None,
            spec=agent.spec_dict(model),
            input=prompt,
            status=(RunStatus.FAILED if error else RunStatus.DONE).value,
            result=result,
            error=error,
            usage=completion.usage.to_dict(),
            cost_usd=completion.cost,
            max_cost_usd=max_cost,
            num_turns=1,
            trigger_kind="system",
            trigger_ref={"agent": agent.slug},
            provider_key_id=key.id,
            scopes=[],
            started_at=started,
            finished_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.flush()
        db.add(
            AgentRunStep(
                run_id=run.id,
                seq=0,
                kind="user",
                content=[Text(text=prompt).model_dump(mode="json")],
            )
        )
        db.add(
            AgentRunStep(
                run_id=run.id,
                seq=1,
                kind="assistant",
                content=[b.model_dump(mode="json") for b in completion.message.content],
                replay=completion.replay,
                usage=completion.usage.to_dict(),
                cost_usd=completion.cost,
            )
        )
        db.commit()
        return run.id


# Calls whose caller stopped waiting, kept alive until they are charged.
_detached: set[asyncio.Task] = set()


def log_unexpected_failure(agent: str, key_id: int, call: asyncio.Task) -> None:
    """A detached call's failure has no one to raise to. Retrieving it stops asyncio
    logging it with its text; only failures nobody expects (a lost charge, a bug)
    are kept, as `logger.exception` would, with no exception active in a callback."""
    if call.cancelled():
        return
    e = call.exception()
    if e is not None and not isinstance(e, (LLMError, TimeoutError, *PROVIDER_ERRORS)):
        logger.error(f"{agent} call on key {key_id} failed after it was sent", exc_info=e)


async def run_agent(
    agent: str, prompt: str, *, user: int, key: Credential, record: Record
) -> Result:
    """One turn of system agent `agent` on `prompt` for `user`, paid with their `key`.

    Raises LLMError when the key can't pay (another provider, deleted, rejected,
    its daily pool spent) or the model gave no usable answer; a turn that was
    billed is recorded either way, even when the caller gives up on it first (while
    the event loop lives on: `run_agent_sync`'s loop cancels it on exit).
    Transient provider errors (rate limits, 5xx, connection errors, timeouts)
    propagate unchanged.
    """
    found = system_agent(agent)
    model = found.model_for(key.provider)
    if model is None:
        raise LLMError(f"{found.slug} needs an {' or '.join(found.providers)} key")
    max_cost = await asyncio.to_thread(allowance, key)
    call = asyncio.ensure_future(
        complete(found, model, prompt, user=user, key=key, record=record, max_cost=max_cost)
    )
    _detached.add(call)
    call.add_done_callback(_detached.discard)
    call.add_done_callback(partial(log_unexpected_failure, found.slug, key.id))
    # A caller's timeout mustn't cancel a call the provider may already be billing.
    return await asyncio.shield(call)


async def complete(
    found: SystemAgent,
    model: str,
    prompt: str,
    *,
    user: int,
    key: Credential,
    record: Record,
    max_cost: Decimal,
) -> Result:
    provider = get_provider(model, api_key=key.key)
    started = datetime.now(timezone.utc)
    try:
        completion = await provider.acomplete(**request_args(found, prompt))
    except provider.KEY_REJECTED as e:
        await asyncio.to_thread(refuse, key)
        raise LLMError(rejected_message(key)) from e
    except provider.KEY_FORBIDDEN as e:
        logger.warning("%s: %s refused the request: %s", found.slug, model, e)
        raise LLMError(
            f"{key.provider} refused this request to {model}; "
            "the key may not have access to it"
        ) from e
    try:
        text, data = parse_result(found, completion)
        error = None
    except LLMError as e:
        text, data, error = "", None, str(e)
    run_id = None
    if record == Record.FULL:
        run_id = await asyncio.to_thread(
            record_run,
            user=user,
            key=key,
            agent=found,
            model=model,
            prompt=prompt,
            completion=completion,
            data=data,
            error=error,
            max_cost=max_cost,
            started=started,
        )
    else:
        await asyncio.to_thread(charge, key, found, completion.cost)
    if error:
        raise LLMError(error)
    return Result(text=text, data=data, cost=completion.cost, run_id=run_id)


def run_agent_sync(
    agent: str, prompt: str, *, user: int, key: Credential, record: Record
) -> Result:
    """`run_agent` for sync worker code, which never runs inside an event loop."""
    return asyncio.run(run_agent(agent, prompt, user=user, key=key, record=record))
