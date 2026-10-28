"""The one way memory calls an LLM: one turn of an agent, paid with a user's key.

System agents are prompt-only, so a call is one provider-neutral turn; an agent
with a result schema answers through a forced submit_result call, which strict
tool use holds to the schema. Worker-safe: no API imports.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
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
from memory.common.db.models import KeyStatus, LLMProviderKey, SummaryRequest
from memory.common.llms import PROVIDER_ERRORS
from memory.common.llms.neutral import Completion, NeutralMessage, Text, ToolCall
from memory.common.llms.registry import get_provider, split_model
from memory.common.llms.tools import ToolDefinition

logger = logging.getLogger(__name__)

SUBMIT_RESULT = "submit_result"
UNUSABLE_STOPS = {"max_output_tokens", "context_exceeded", "refused", "paused"}


class LLMError(Exception):
    """The call produced no result; the message is safe to show the key's owner."""


@dataclass(frozen=True)
class Result:
    text: str
    data: dict[str, Any] | None
    cost: Decimal


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


def check_allowance(key: Credential) -> None:
    with make_session() as db:
        row = db.get(LLMProviderKey, key.id)
        if row is None:
            raise LLMError(f"API key '{key.label}' was deleted")
        if row.status != KeyStatus.OK:
            raise LLMError(rejected_message(row))
        try:
            caps.key_allowance(db, row)
        except caps.CapExceeded as e:
            raise LLMError(str(e)) from e


def refuse(key: Credential) -> None:
    if reject_key(key.id, key.key):
        notify_key_rejected(key)


def charge(key: Credential, agent: SystemAgent, cost: Decimal) -> None:
    with make_session() as db:
        caps.record_spend(db, key_id=key.id, agent=agent.slug, cost=cost)
        db.commit()


# When each (provider, model, key) was last logged. The log reaches the operator,
# so a run of items against an unavailable model must not log once per item.
reported_unavailable: dict[tuple[str, str, int], float] = {}
REPORT_INTERVAL_SEC = 3600


def report_model_unavailable(key: Credential, model: str, agent_slug: str | None = None) -> None:
    """Log at ERROR, at most hourly per (provider, model, key), that a key can't reach a model.

    A 404 is a retired model or a key that can't use it; every key failing is the
    first, one key failing is the second. `model` is the provider's bare name.
    """
    now = time.monotonic()
    dedupe = (key.provider, model, key.id)
    if now - reported_unavailable.get(dedupe, -REPORT_INTERVAL_SEC) < REPORT_INTERVAL_SEC:
        return
    reported_unavailable[dedupe] = now
    with make_session() as db:
        pending = (
            db.query(SummaryRequest)
            .filter(SummaryRequest.provider_key_id == key.id, SummaryRequest.status == "pending")
            .count()
        )
    logger.error(
        "model %s unavailable to %s key %s (agent %s, %s pending summaries)",
        model,
        key.provider,
        key.id,
        agent_slug,
        pending,
    )


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


async def run_agent(agent: str, prompt: str, *, key: Credential) -> Result:
    """One turn of system agent `agent` on `prompt`, paid with `key`.

    The spend goes on the key's hourly bucket, not the run ledger: these calls are
    memory's own (summaries, search steps), too many and too small to keep.
    Raises LLMError when the key can't pay (another provider, deleted, rejected,
    its daily pool spent) or the model gave no usable answer; a turn that was
    billed is charged either way, even when the caller gives up on it first (while
    the event loop lives on: `run_agent_sync`'s loop cancels it on exit).
    Transient provider errors (rate limits, 5xx, connection errors, timeouts)
    propagate unchanged.
    """
    found = system_agent(agent)
    model = found.model_for(key.provider)
    if model is None:
        raise LLMError(f"{found.slug} needs an {' or '.join(found.providers)} key")
    await asyncio.to_thread(check_allowance, key)
    call = asyncio.ensure_future(complete(found, model, prompt, key=key))
    _detached.add(call)
    call.add_done_callback(_detached.discard)
    call.add_done_callback(partial(log_unexpected_failure, found.slug, key.id))
    # A caller's timeout mustn't cancel a call the provider may already be billing.
    return await asyncio.shield(call)


async def complete(found: SystemAgent, model: str, prompt: str, *, key: Credential) -> Result:
    provider = get_provider(model, api_key=key.key)
    try:
        completion = await provider.acomplete(**request_args(found, prompt))
    except provider.KEY_REJECTED as e:
        await asyncio.to_thread(refuse, key)
        raise LLMError(rejected_message(key)) from e
    except provider.MODEL_UNAVAILABLE as e:
        await asyncio.to_thread(report_model_unavailable, key, split_model(model)[1], found.slug)
        raise LLMError(f"{model} is unavailable to this {key.provider} key") from e
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
    await asyncio.to_thread(charge, key, found, completion.cost)
    if error:
        raise LLMError(error)
    return Result(text=text, data=data, cost=completion.cost)


def run_agent_sync(agent: str, prompt: str, *, key: Credential) -> Result:
    """`run_agent` for sync worker code, which never runs inside an event loop."""
    return asyncio.run(run_agent(agent, prompt, key=key))
