import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from memory.agents import provider_keys
from memory.agents.enqueue import TriggerInfo, enqueue_run
from memory.agents.provider_keys import choose_key
from memory.agents.runner import execute_run
from memory.agents.spec import AgentSpec
from memory.agents.toolhub import ToolHubError
from memory.common import settings
from memory.common.db.connection import make_session
from memory.common.db.models import (
    AgentRun,
    AgentRunStep,
    APIKey,
    LLMProviderKey,
    UserSession,
)
from memory.common.llms.neutral import (
    AgentUsage,
    Completion,
    NeutralMessage,
    Text,
    ToolCall,
    ToolResult,
)
from tests.memory.agents.fakes import (
    DEFAULT_USAGE,
    FAKE_MODELS,
    PRICING,
    FakeHub,
    calls,
    key_forbidden,
    key_rejected,
    reply,
)
from tests.memory.agents.test_provider_keys import SECRET, add_key


def make_run(db, user, model, max_cost="1", **spec):
    run = AgentRun(
        user_id=user.id,
        scopes=list(user.scopes),
        spec={
            "system_prompt": "be useful",
            "model": model,
            "reasoning": "high",
            "max_output_tokens": 4000,
        }
        | spec,
        input="do it",
        max_cost_usd=Decimal(max_cost),
        trigger_kind="manual",
        provider_key_id=choose_key(db, user.id, model, None).id,
    )
    db.add(run)
    db.commit()
    return run.id


def load(db, run_id):
    db.expire_all()
    run = db.get(AgentRun, run_id)
    steps = (
        db.query(AgentRunStep)
        .filter(AgentRunStep.run_id == run_id)
        .order_by(AgentRunStep.seq)
        .all()
    )
    return run, steps


def hub(**extra):
    return FakeHub(
        {
            "echo": lambda i: (i.get("text", ""), False),
            "fail": lambda i: ("boom", True),
            "send": lambda i: ("sent", False),
        }
        | extra
    )


def stopped(stop_reason, text="cut"):
    message = NeutralMessage(role="assistant", content=[Text(text=text)])
    return Completion(message, None, stop_reason, AgentUsage(), Decimal(0))


@pytest.mark.asyncio
@pytest.mark.parametrize("model", FAKE_MODELS)
async def test_answer_mode_returns_the_final_text(
    db_session, regular_user, fake_providers, model
):
    run_id = make_run(db_session, regular_user, model)
    fake_providers.script = [reply("42")]

    await execute_run(run_id, hub().factory())

    run, steps = load(db_session, run_id)
    assert (run.status, run.result, run.error) == ("done", {"text": "42"}, None)
    assert [s.kind for s in steps] == ["user", "assistant"]
    assert run.num_turns == 1
    assert run.cost_usd == Decimal("0.00012")
    assert run.usage == {
        "input_tokens": 100,
        "output_tokens": 10,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "web_searches": 0,
        "web_fetches": 0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("model", FAKE_MODELS)
async def test_tool_results_feed_the_next_turn_with_replay(
    db_session, regular_user, fake_providers, model
):
    run_id = make_run(db_session, regular_user, model)
    fake_providers.script = [calls(("t1", "echo", {"text": "hi"})), reply("done")]

    await execute_run(run_id, hub().factory())

    run, steps = load(db_session, run_id)
    assert [s.kind for s in steps] == ["user", "assistant", "tool_results", "assistant"]
    assert steps[2].content[0]["text"] == "hi"
    assert isinstance(steps[2].content[0]["duration_ms"], int)
    second = fake_providers.requests[1]
    assert [m.role for m in second["messages"]] == ["user", "assistant", "user"]
    provider = model.split("/")[0]
    assert second["replay"] == [None, {"provider": provider, "blocks": []}, None]


@pytest.mark.asyncio
@pytest.mark.parametrize("model", FAKE_MODELS)
async def test_successful_finish_tool_ends_the_run_and_skips_later_calls(
    db_session, regular_user, fake_providers, model
):
    run_id = make_run(
        db_session,
        regular_user,
        model,
        tools=["send", "echo"],
        finish={"mode": "tool", "tools": ["send"]},
    )
    fake_providers.script = [
        calls(("t1", "send", {"to": "x"}), ("t2", "echo", {"text": "late"}))
    ]
    h = hub()

    await execute_run(run_id, h.factory())

    run, steps = load(db_session, run_id)
    assert run.status == "done"
    assert run.result == {"tool": "send", "input": {"to": "x"}, "output": "sent"}
    assert h.calls == ["send"]
    assert steps[-1].content[1]["text"] == "skipped: the run already finished"


@pytest.mark.asyncio
@pytest.mark.parametrize("model", FAKE_MODELS)
async def test_failed_finish_tool_goes_back_to_the_model(
    db_session, regular_user, fake_providers, model
):
    run_id = make_run(
        db_session,
        regular_user,
        model,
        tools=["fail", "send"],
        finish={"mode": "tool", "tools": ["fail", "send"]},
    )
    fake_providers.script = [calls(("t1", "fail", {})), calls(("t2", "send", {}))]

    await execute_run(run_id, hub().factory())

    run, _ = load(db_session, run_id)
    assert (run.status, run.result["tool"]) == ("done", "send")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model,expected_choices",
    [
        ("anthropic/m", ["auto", "required", "required"]),
        ("openai/m", ["auto", "auto", "auto"]),
    ],
)
async def test_reminders_then_failure_and_tool_choice_by_capability(
    db_session, regular_user, fake_providers, model, expected_choices
):
    run_id = make_run(
        db_session,
        regular_user,
        model,
        tools=["send"],
        finish={"mode": "tool", "tools": ["send"]},
    )
    fake_providers.script = [reply("thinking"), reply("still"), reply("nope")]

    await execute_run(run_id, hub().factory())

    run, steps = load(db_session, run_id)
    assert run.status == "failed"
    assert "without calling a finish tool (send) after 2 reminders" in run.error
    assert [r["tool_choice"] for r in fake_providers.requests] == expected_choices
    assert [s.kind for s in steps].count("user") == 3


@pytest.mark.asyncio
async def test_tool_choice_is_forced_only_on_the_turn_after_a_reminder(
    db_session, regular_user, fake_providers
):
    run_id = make_run(
        db_session,
        regular_user,
        "anthropic/m",
        tools=["send", "echo"],
        finish={"mode": "tool", "tools": ["send"]},
    )
    fake_providers.script = [
        reply("a"),
        calls(("t1", "echo", {})),
        reply("b"),
        reply("c"),
    ]

    await execute_run(run_id, hub().factory())

    run, _ = load(db_session, run_id)
    assert run.status == "failed"
    assert [r["tool_choice"] for r in fake_providers.requests] == [
        "auto",
        "required",
        "auto",
        "required",
    ]


@pytest.mark.asyncio
async def test_provider_executed_results_are_truncated_like_any_other(
    db_session, regular_user, fake_providers, monkeypatch
):
    monkeypatch.setattr(settings, "AGENT_TOOL_RESULT_MAX_CHARS", 10)
    run_id = make_run(db_session, regular_user, "anthropic/m", tools=["web_search"])
    message = NeutralMessage(
        role="assistant",
        content=[
            ToolCall(
                id="s1", name="web_search", input={"query": "q"}, executed_by="provider"
            ),
            ToolResult(tool_call_id="s1", name="web_search", text="y" * 25),
            Text(text="found it"),
        ],
    )
    fake_providers.script = [
        Completion(message, None, "end_turn", AgentUsage(), Decimal(0))
    ]

    await execute_run(run_id, hub().factory())

    _, steps = load(db_session, run_id)
    assert steps[1].content[1]["text"] == "y" * 10 + "\n[truncated: 15 more chars]"


@pytest.mark.asyncio
@pytest.mark.parametrize("model", FAKE_MODELS)
async def test_submit_result_is_validated_against_the_schema(
    db_session, regular_user, fake_providers, model
):
    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
        "required": ["n"],
    }
    run_id = make_run(db_session, regular_user, model, result_schema=schema)
    fake_providers.script = [
        calls(("t1", "submit_result", {"n": "x"})),
        calls(("t2", "submit_result", {"n": 3})),
    ]

    await execute_run(run_id, hub().factory())

    run, steps = load(db_session, run_id)
    assert (run.status, run.result) == ("done", {"data": {"n": 3}})
    assert steps[2].content[0]["is_error"] is True
    assert "invalid result" in steps[2].content[0]["text"]
    assert [t.name for t in fake_providers.requests[0]["tools"]] == [
        "echo",
        "fail",
        "send",
        "submit_result",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "spec,max_cost,expected",
    [
        ({"max_turns": 2}, "1", "max_turns (2) reached"),
        ({}, "0.0002", "reached this run's cap"),
    ],
)
async def test_budgets_stop_the_run(
    db_session, regular_user, fake_providers, spec, max_cost, expected
):
    run_id = make_run(
        db_session, regular_user, "anthropic/m", max_cost=max_cost, **spec
    )
    fake_providers.script = [
        calls(("t1", "echo", {})),
        calls(("t2", "echo", {})),
        calls(("t3", "echo", {})),
    ]

    await execute_run(run_id, hub().factory())

    run, _ = load(db_session, run_id)
    assert run.status == "budget_exceeded"
    assert expected in run.error


@pytest.mark.asyncio
async def test_each_turn_may_only_spend_what_is_left(
    db_session, regular_user, fake_providers
):
    # The first turn's input is estimated from the system prompt ("be useful"),
    # the input ("do it") and the tool list ("[]"): 16 chars, 4 tokens, $0.000004.
    # The $0.000996 left of a $0.001 cap buys 498 output tokens at $2/MTok.
    run_id = make_run(db_session, regular_user, "anthropic/m", max_cost="0.001")
    fake_providers.script = [stopped("max_output_tokens")]

    await execute_run(run_id, FakeHub({}).factory())

    run, _ = load(db_session, run_id)
    assert fake_providers.requests[0]["params"].max_output_tokens == 498
    assert run.status == "budget_exceeded"
    assert "the run's remaining budget" in run.error


@pytest.mark.asyncio
async def test_timeout_is_a_budget(db_session, regular_user, fake_providers):
    run_id = make_run(db_session, regular_user, "anthropic/m", timeout_sec=1)

    async def slow():
        await asyncio.sleep(5)
        return reply("late")

    fake_providers.script = [slow]

    await execute_run(run_id, hub().factory())

    run, _ = load(db_session, run_id)
    assert run.status == "budget_exceeded"
    assert "timeout_sec=1" in run.error


def request_cancel(run_id):
    with make_session() as db:
        db.query(AgentRun).filter(AgentRun.id == run_id).update(
            {AgentRun.cancel_requested_at: datetime.now(timezone.utc)}
        )
        db.commit()


@pytest.mark.asyncio
async def test_cancel_before_the_first_turn(db_session, regular_user, fake_providers):
    run_id = make_run(db_session, regular_user, "anthropic/m")
    request_cancel(run_id)

    await execute_run(run_id, hub().factory())

    run, _ = load(db_session, run_id)
    assert run.status == "cancelled"
    assert fake_providers.requests == []


@pytest.mark.asyncio
async def test_cancel_between_tool_calls(db_session, regular_user, fake_providers):
    run_id = make_run(db_session, regular_user, "anthropic/m")
    h = hub(cancel_me=lambda i: (request_cancel(run_id), ("ok", False))[1])
    fake_providers.script = [calls(("t1", "cancel_me", {}), ("t2", "echo", {}))]

    await execute_run(run_id, h.factory())

    run, _ = load(db_session, run_id)
    assert run.status == "cancelled"
    assert h.calls == ["cancel_me"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stop_reason,expected",
    [
        ("max_output_tokens", "raise max_output_tokens"),
        ("context_exceeded", "AGENT_CONTEXT_TRIM_AT_TOKENS"),
        ("refused", "refused"),
    ],
)
async def test_unusable_stop_reasons_fail_with_the_fix(
    db_session, regular_user, fake_providers, stop_reason, expected
):
    run_id = make_run(db_session, regular_user, "anthropic/m")
    fake_providers.script = [stopped(stop_reason)]

    await execute_run(run_id, hub().factory())

    run, _ = load(db_session, run_id)
    assert run.status == "failed"
    assert expected in run.error


def paused() -> Completion:
    """A provider-paused turn: a search ran, the answer is still to come."""
    message = NeutralMessage(
        role="assistant",
        content=[
            ToolCall(id="s1", name="web_search", input={"query": "q"}, executed_by="provider"),
            ToolResult(tool_call_id="s1", name="web_search", text="- a <https://a>"),
        ],
    )
    return Completion(
        message, {"blocks": []}, "paused", DEFAULT_USAGE, PRICING.cost(DEFAULT_USAGE)
    )


@pytest.mark.asyncio
async def test_a_paused_turn_is_resumed_with_its_partial_content(
    db_session, regular_user, fake_providers
):
    run_id = make_run(db_session, regular_user, "anthropic/m", tools=["web_search"])
    fake_providers.script = [paused(), reply("found it")]

    await execute_run(run_id, hub().factory())

    run, _ = load(db_session, run_id)
    assert (run.status, run.result) == ("done", {"text": "found it"})
    resumed = fake_providers.requests[1]["messages"][-1]
    assert resumed.role == "assistant"
    assert resumed.content[0].id == "s1"


@pytest.mark.asyncio
async def test_each_resumed_request_goes_through_the_cost_cap(
    db_session, regular_user, fake_providers
):
    # The paused request costs $0.00012; resending its ~110 tokens would pass $0.0002.
    run_id = make_run(
        db_session, regular_user, "anthropic/m", max_cost="0.0002", tools=["web_search"]
    )
    fake_providers.script = [paused(), paused(), reply("found it")]

    await execute_run(run_id, hub().factory())

    run, _ = load(db_session, run_id)
    assert run.status == "budget_exceeded"
    assert len(fake_providers.requests) == 1


@pytest.mark.asyncio
async def test_provider_executed_calls_are_not_run_by_the_runner(
    db_session, regular_user, fake_providers
):
    run_id = make_run(db_session, regular_user, "anthropic/m", tools=["web_search"])
    message = NeutralMessage(
        role="assistant",
        content=[
            ToolCall(
                id="s1", name="web_search", input={"query": "q"}, executed_by="provider"
            ),
            ToolResult(tool_call_id="s1", name="web_search", text="- a <https://a>"),
            Text(text="found it"),
        ],
    )
    fake_providers.script = [
        Completion(message, None, "end_turn", AgentUsage(), Decimal(0))
    ]
    h = hub()

    await execute_run(run_id, h.factory())

    run, steps = load(db_session, run_id)
    assert (run.status, run.result) == ("done", {"text": "found it"})
    assert steps[1].content[1]["text"] == "- a <https://a>"
    assert h.calls == []


@pytest.mark.asyncio
async def test_only_pending_runs_execute(db_session, regular_user, fake_providers):
    run_id = make_run(db_session, regular_user, "anthropic/m")
    db_session.get(AgentRun, run_id).status = "running"
    db_session.commit()

    await execute_run(run_id, hub().factory())

    run, _ = load(db_session, run_id)
    assert run.status == "running"
    assert fake_providers.requests == []


@pytest.mark.asyncio
async def test_run_session_is_minted_for_the_owner_and_removed(
    db_session, regular_user, fake_providers
):
    run_id = make_run(db_session, regular_user, "anthropic/m")
    fake_providers.script = [reply("ok")]
    h = hub()

    await execute_run(run_id, h.factory())

    assert h.opened_with["user_id"] == regular_user.id
    assert h.opened_with["scopes"] == ["teams"]
    token = h.opened_with["session_token"]
    assert db_session.query(UserSession).filter(UserSession.id == token).count() == 0


def enqueued(db, user, api_key=None, **spec) -> str:
    full = {
        "system_prompt": "be useful",
        "model": "anthropic/m",
        "reasoning": "high",
    } | spec
    run = enqueue_run(
        db,
        user_id=user.id,
        spec=AgentSpec.model_validate(full),
        input="do it",
        trigger=TriggerInfo(kind="manual"),
        api_key=api_key,
    )
    return run.id


@pytest.mark.asyncio
async def test_a_run_can_use_the_tools_its_owner_can_use_over_http(
    db_session, regular_user, fake_providers
):
    # regular_user's own scopes are ["teams"]; meta_get_user needs "read", which
    # every session gets over HTTP.
    run_id = enqueued(db_session, regular_user, tools=["meta_get_user"])
    fake_providers.script = [calls(("t1", "meta_get_user", {})), reply("done")]

    await execute_run(run_id)

    run, steps = load(db_session, run_id)
    assert run.status == "done", run.error
    assert "regular@example.com" in steps[2].content[0]["text"]


@pytest.mark.asyncio
async def test_deleting_the_api_key_after_enqueue_does_not_widen_scopes(
    db_session, regular_user, fake_providers
):
    key = APIKey.create(user_id=regular_user.id, name="narrow", scopes=["read"])
    db_session.add(key)
    db_session.commit()
    run_id = enqueued(db_session, regular_user, api_key=key)
    db_session.delete(key)
    db_session.commit()
    fake_providers.script = [reply("ok")]
    h = hub()

    await execute_run(run_id, h.factory())

    assert h.opened_with["scopes"] == ["read"]


@pytest.mark.asyncio
async def test_unavailable_tools_fail_the_run_with_the_hubs_message(
    db_session, regular_user, fake_providers
):
    run_id = make_run(db_session, regular_user, "anthropic/m")

    def broken(**kwargs):
        raise ToolHubError(
            "tools unavailable: core_search (not visible to this user - missing scope?)"
        )

    await execute_run(run_id, broken)

    run, _ = load(db_session, run_id)
    assert run.status == "failed"
    assert "core_search (not visible to this user - missing scope?)" in run.error


@pytest.mark.asyncio
async def test_trimming_stubs_old_results_and_records_a_notice(
    db_session, regular_user, fake_providers, monkeypatch
):
    monkeypatch.setattr(settings, "AGENT_CONTEXT_TRIM_AT_TOKENS", 0)
    monkeypatch.setattr(settings, "AGENT_CONTEXT_TRIM_TO_TOKENS", 0)
    run_id = make_run(db_session, regular_user, "anthropic/m")
    fake_providers.script = [
        calls((f"t{i}", "echo", {"text": "x" * 400})) for i in range(4)
    ] + [reply("done")]

    await execute_run(run_id, hub().factory())

    run, steps = load(db_session, run_id)
    last = fake_providers.requests[-1]
    assert run.status == "done"
    assert "notice" in [s.kind for s in steps]
    assert last["messages"][2].content[0].text.startswith("[cleared: echo result")
    assert last["replay"] == [None] * len(last["messages"])
    assert steps[2].content[0]["text"] == "x" * 400  # stored transcript untouched


# --- user provider keys ---------------------------------------------------------


def on_key(db, user, key_id):
    run_id = make_run(db, user, "anthropic/m")
    run = db.get(AgentRun, run_id)
    run.provider_key_id = key_id
    db.commit()
    return run_id


@pytest.fixture
def notifications(monkeypatch):
    sent = []
    monkeypatch.setattr(
        provider_keys, "notify_user_now", lambda db, user, *args, **kw: sent.append((user.id, args))
    )
    return sent


@pytest.mark.asyncio
async def test_runs_on_a_users_key_call_the_provider_with_it(
    db_session, regular_user, fake_providers
):
    key = add_key(db_session, regular_user, value="sk-users-own")
    run_id = on_key(db_session, regular_user, key.id)
    fake_providers.script = [reply("ok")]

    await execute_run(run_id, hub().factory())

    run, _ = load(db_session, run_id)
    db_session.refresh(key)
    assert run.status == "done", run.error
    assert fake_providers.requests[0]["api_key"] == "sk-users-own"
    assert key.last_used_at is not None


@pytest.mark.asyncio
async def test_a_rejected_key_is_marked_invalid_and_notified_once(
    db_session, regular_user, fake_providers, notifications, caplog
):
    key = add_key(db_session, regular_user, label="mine")
    first = on_key(db_session, regular_user, key.id)
    second = on_key(db_session, regular_user, key.id)
    fake_providers.script = [key_rejected, key_rejected]

    await execute_run(first, hub().factory())
    await execute_run(second, hub().factory())

    runs = [load(db_session, r)[0] for r in (first, second)]
    db_session.refresh(key)
    assert [r.status for r in runs] == ["failed", "failed"]
    assert all("API key 'mine' was rejected by anthropic" in (r.error or "") for r in runs)
    assert "401 invalid x-api-key" not in (runs[0].error or "")
    assert (key.status, len(notifications)) == ("invalid", 1)
    assert SECRET not in caplog.text


@pytest.mark.asyncio
async def test_a_rejection_does_not_invalidate_a_key_replaced_mid_run(
    db_session, regular_user, fake_providers, notifications
):
    key = add_key(db_session, regular_user)
    run_id = on_key(db_session, regular_user, key.id)

    async def replaced_then_rejected():
        with make_session() as db:
            db.query(LLMProviderKey).filter(LLMProviderKey.id == key.id).one().key = (
                "sk-replacement"
            )
            db.commit()
        return await key_rejected()

    fake_providers.script = [replaced_then_rejected]

    await execute_run(run_id, hub().factory())

    db_session.refresh(key)
    assert (load(db_session, run_id)[0].status, key.status, notifications) == ("failed", "ok", [])


@pytest.mark.asyncio
async def test_a_deleted_key_fails_its_runs(
    db_session, regular_user, fake_providers
):
    key = add_key(db_session, regular_user)
    run_id = on_key(db_session, regular_user, key.id)
    db_session.delete(key)
    db_session.commit()

    await execute_run(run_id, hub().factory())

    run, _ = load(db_session, run_id)
    assert (run.status, run.error) == (
        "failed",
        "the API key this run was set to use was deleted; pick another key for the agent",
    )
    assert fake_providers.requests == []


@pytest.mark.asyncio
async def test_a_forbidden_request_fails_only_that_run(
    db_session, regular_user, fake_providers, notifications, caplog
):
    key = add_key(db_session, regular_user, label="mine")
    run_id = on_key(db_session, regular_user, key.id)
    fake_providers.script = [key_forbidden]

    await execute_run(run_id, hub().factory())

    run, _ = load(db_session, run_id)
    db_session.refresh(key)
    assert (run.status, key.status, notifications) == ("failed", "ok", [])
    assert run.error == (
        "the provider refused this request to anthropic/m; the API key may not have "
        "access to this model or feature"
    )
    assert "model not allowed for this key" in caplog.text


@pytest.mark.asyncio
async def test_an_empty_key_fails_the_run(
    db_session, regular_user, fake_providers
):
    key = add_key(db_session, regular_user, value="")
    run_id = on_key(db_session, regular_user, key.id)
    fake_providers.script = [reply("ok")]

    await execute_run(run_id, hub().factory())

    assert load(db_session, run_id)[0].status == "failed"
    assert fake_providers.requests == []
