# pyright: reportFunctionMemberAccess=false
"""Runs placed on workers: claimed under a lease by anyone with the agents:work scope."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from memory.agents import cancel, work
from memory.agents.caps import spent_last_24h
from memory.agents.enqueue import TriggerInfo, enqueue_run
from memory.agents.runner import mint_session
from memory.agents.spec import AgentSpec
from memory.api.MCP.servers import agent
from memory.api.MCP.visibility import get_visibility_checker
from memory.common import settings
from memory.common.celery_app import RUN_AGENT
from memory.common.db.models import AgentRun, HumanUser, LLMProviderKey, UserSession
from memory.workers.tasks.maintenance import sweep_agent_runs
from tests.conftest import mcp_auth_context
from tests.memory.agents.test_child_runs import call_as_run

BASE = {"system_prompt": "check this", "model": "anthropic/m", "reasoning": "high"}
WORKERS = {"placement": "runner_only"}
SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


@pytest.fixture
def worker_session(db_session):
    """Someone else, who may only work on runs."""
    user = HumanUser(
        name="Worker", email="worker@example.com", password_hash="x", scopes=["agents:work"]
    )
    db_session.add(user)
    db_session.commit()
    session = UserSession(
        token="worker-session-token",
        user_id=user.id,
        expires_at=datetime.now() + timedelta(days=1),
    )
    db_session.add(session)
    db_session.commit()
    return session


@pytest.fixture
def no_revoke():
    with patch.object(cancel.app.control, "revoke") as revoke:
        yield revoke


def submit_run(db, user, **spec) -> str:
    run = enqueue_run(
        db,
        user_id=user.id,
        spec=AgentSpec.model_validate(BASE | WORKERS | spec),
        input="is the sky green?",
        trigger=TriggerInfo(kind="manual"),
    )
    return run.id


def backdate(db, run_id: str, **delta):
    run = db.get(AgentRun, run_id)
    run.created_at = run.placed_at = datetime.now(timezone.utc) - timedelta(**delta)
    db.commit()


def expire_lease(db, run_id: str):
    db.get(AgentRun, run_id).lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()


def stored(db, run_id: str) -> AgentRun:
    db.expire_all()
    found = db.get(AgentRun, run_id)
    assert found is not None
    return found


async def claim(**kwargs) -> dict:
    return await agent.work_claim.fn(**kwargs)


async def submit(run_id: str, lease_token: str, **kwargs) -> dict:
    return await agent.work_submit.fn(run_id=run_id, lease_token=lease_token, **kwargs)


@pytest.mark.asyncio
async def test_any_worker_claims_another_users_run_which_stays_its_producers(
    db_session, regular_user, user_session, worker_session
):
    run_id = submit_run(db_session, regular_user)

    with mcp_auth_context(worker_session.token):
        claimed = (await claim(worker="laptop"))["run"]

    assert (claimed["id"], claimed["input"], claimed["status"]) == (
        run_id, "is the sky green?", "running",
    )
    assert claimed["spec"]["system_prompt"] == "check this"
    assert claimed["lease_token"]
    # Only what executing it takes: nothing about its owner, their key or its trigger.
    assert set(claimed) == {
        "id", "status", "input", "spec", "lease_token", "lease_expires_at",
        "lease_lapses", "worker_label",
    }
    assert set(claimed["spec"]) == set(agent.WORKER_SPEC_FIELDS)
    found = stored(db_session, run_id)
    assert (found.user_id, found.worker_user_id, found.worker_label) == (
        regular_user.id, worker_session.user_id, "laptop",
    )
    with mcp_auth_context(user_session.token):
        seen = (await agent.wait.fn(run_id=run_id))["run"]
    assert (seen["worker_label"], seen["status"]) == ("laptop", "running")
    assert "lease_token" not in seen


@pytest.mark.asyncio
async def test_claim_takes_the_oldest_claimable_run_once(
    db_session, regular_user, worker_session, fake_providers, no_celery_dispatch
):
    first = submit_run(db_session, regular_user)
    second = submit_run(db_session, regular_user)
    backdate(db_session, first, minutes=1)
    # The server's runs are never handed to workers.
    submit_run(db_session, regular_user, placement="server_only")

    with mcp_auth_context(worker_session.token):
        claimed = [(await claim())["run"] for _ in range(3)]

    assert [c and c["id"] for c in claimed] == [first, second, None]


@pytest.mark.asyncio
@pytest.mark.transactional_db  # two real connections, so the row lock is real
async def test_claim_passes_over_a_run_another_worker_is_taking(
    db_session, regular_user, worker_session
):
    first = submit_run(db_session, regular_user)
    second = submit_run(db_session, regular_user)
    backdate(db_session, first, minutes=1)
    db_session.query(AgentRun).filter(AgentRun.id == first).with_for_update().one()

    with mcp_auth_context(worker_session.token):
        claimed = (await claim())["run"]

    db_session.rollback()
    assert claimed["id"] == second


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "requires,capabilities,matched",
    [
        ({}, None, True),
        ({"models": ["claude-haiku-*"]}, {"models": ["claude-haiku-4-5"]}, True),
        ({"models": ["claude-haiku-*"]}, None, False),
        ({"models": ["claude-haiku-*"]}, {"models": ["gpt-*"]}, False),
        (
            {"tools": ["memory:send_message"], "flags": ["web"]},
            {"tools": ["memory:*"], "flags": ["web", "filesystem"]},
            True,
        ),
        ({"tools": ["memory:send_message"], "flags": ["web"]}, {"tools": ["memory:*"]}, False),
    ],
)
async def test_claim_hands_out_only_runs_whose_requirements_are_met(
    db_session, regular_user, worker_session, requires, capabilities, matched
):
    run_id = submit_run(db_session, regular_user, requires=requires)

    with mcp_auth_context(worker_session.token):
        claimed = (await claim(capabilities=capabilities))["run"]

    assert (claimed is not None and claimed["id"] == run_id) is matched


@pytest.mark.asyncio
async def test_claiming_again_with_the_lease_token_renews_the_lease(
    db_session, regular_user, worker_session
):
    run_id = submit_run(db_session, regular_user)

    with mcp_auth_context(worker_session.token):
        first = (await claim(lease_sec=60))["run"]
        renewed = await claim(run_id=run_id, lease_token=first["lease_token"], lease_sec=600)

    assert renewed["cancel_requested"] is False
    assert renewed["run"]["lease_token"] == first["lease_token"]
    assert datetime.fromisoformat(renewed["run"]["lease_expires_at"]) > datetime.fromisoformat(
        first["lease_expires_at"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "requested,expected",
    [
        (None, settings.AGENT_WORK_LEASE_DEFAULT_SEC),
        (1, settings.AGENT_WORK_LEASE_MIN_SEC),
        (10**9, settings.AGENT_WORK_LEASE_MAX_SEC),
        (120, 120),
    ],
)
async def test_lease_length_is_clamped(db_session, regular_user, worker_session, requested, expected):
    run_id = submit_run(db_session, regular_user)

    with mcp_auth_context(worker_session.token):
        await claim(lease_sec=requested)

    found = stored(db_session, run_id)
    assert found.lease_expires_at and found.started_at
    assert found.lease_expires_at - found.started_at == timedelta(seconds=expected)


def hold_since(db, run_id: str, **delta):
    db.get(AgentRun, run_id).started_at = datetime.now(timezone.utc) - timedelta(**delta)
    db.commit()


@pytest.mark.asyncio
async def test_renewals_never_hold_a_run_past_the_hold_cap(
    db_session, regular_user, worker_session
):
    run_id = submit_run(db_session, regular_user)

    with mcp_auth_context(worker_session.token):
        token = (await claim())["run"]["lease_token"]
        hold_since(db_session, run_id, seconds=settings.AGENT_WORK_HOLD_MAX_SEC - 30)
        renewed = (await claim(run_id=run_id, lease_token=token, lease_sec=600))["run"]

    found = stored(db_session, run_id)
    assert found.started_at
    assert datetime.fromisoformat(renewed["lease_expires_at"]) == found.started_at + timedelta(
        seconds=settings.AGENT_WORK_HOLD_MAX_SEC
    )


@pytest.mark.asyncio
async def test_renewing_past_the_hold_cap_is_refused_and_fails_the_run(
    db_session, regular_user, worker_session
):
    run_id = submit_run(db_session, regular_user)

    with mcp_auth_context(worker_session.token):
        token = (await claim())["run"]["lease_token"]
        hold_since(db_session, run_id, seconds=settings.AGENT_WORK_HOLD_MAX_SEC + 1)
        with pytest.raises(ValueError, match="AGENT_WORK_HOLD_MAX_SEC"):
            await claim(run_id=run_id, lease_token=token)

    found = stored(db_session, run_id)
    assert found.status == "failed"
    assert "AGENT_WORK_HOLD_MAX_SEC" in (found.error or "")


@pytest.mark.asyncio
async def test_the_hold_cap_counts_from_the_runs_first_claim(
    db_session, regular_user, worker_session, admin_session
):
    run_id = submit_run(db_session, regular_user)

    with mcp_auth_context(worker_session.token):
        await claim()
    hold_since(db_session, run_id, hours=1)
    first_claim = stored(db_session, run_id).started_at
    expire_lease(db_session, run_id)
    work.sweep_worker_runs()
    with mcp_auth_context(admin_session.token):
        await claim()

    assert stored(db_session, run_id).started_at == first_claim


@pytest.mark.parametrize("status", ["running", "pending"])
def test_a_run_held_past_the_hold_cap_fails(db_session, regular_user, status):
    run_id = submit_run(db_session, regular_user)
    found = db_session.get(AgentRun, run_id)
    found.status, found.lease_token, found.worker_label = status, "t", "w"
    found.started_at = datetime.now(timezone.utc) - timedelta(
        seconds=settings.AGENT_WORK_HOLD_MAX_SEC + 60
    )
    found.lease_expires_at = found.started_at + timedelta(seconds=settings.AGENT_WORK_HOLD_MAX_SEC)
    db_session.commit()

    work.sweep_worker_runs()

    found = stored(db_session, run_id)
    assert found.status == "failed"
    assert "AGENT_WORK_HOLD_MAX_SEC" in (found.error or "")


@pytest.mark.asyncio
async def test_claim_finds_a_run_behind_many_it_cant_take(
    db_session, regular_user, worker_session, monkeypatch
):
    monkeypatch.setattr(agent, "CLAIM_SCAN", 2)
    for minutes in (5, 4, 3):
        backdate(db_session, submit_run(db_session, regular_user, requires={"flags": ["gpu"]}), minutes=minutes)
    run_id = submit_run(db_session, regular_user)

    with mcp_auth_context(worker_session.token):
        claimed = (await claim())["run"]

    assert claimed["id"] == run_id


@pytest.mark.asyncio
async def test_renewing_with_a_wrong_token_is_refused(db_session, regular_user, worker_session):
    run_id = submit_run(db_session, regular_user)

    with mcp_auth_context(worker_session.token):
        await claim()
        with pytest.raises(ValueError, match="lease token"):
            await claim(run_id=run_id, lease_token="bogus")


@pytest.mark.asyncio
async def test_a_cancel_shows_on_renewal_and_the_run_is_never_claimed_again(
    db_session, regular_user, user_session, worker_session, no_revoke
):
    run_id = submit_run(db_session, regular_user)

    with mcp_auth_context(worker_session.token):
        token = (await claim())["run"]["lease_token"]
    with mcp_auth_context(user_session.token):
        await agent.cancel.fn(run_id=run_id)
    with mcp_auth_context(worker_session.token):
        renewed = await claim(run_id=run_id, lease_token=token)
        again = await claim()
        with pytest.raises(ValueError, match=r"already ended \(cancelled\)"):
            await submit(run_id, token, result="too late")

    assert (renewed["cancel_requested"], renewed["run"]["status"]) == (True, "cancelled")
    assert again["run"] is None


@pytest.mark.asyncio
async def test_a_cancelled_pending_run_is_never_claimed(
    db_session, regular_user, user_session, worker_session, no_revoke
):
    run_id = submit_run(db_session, regular_user)

    with mcp_auth_context(user_session.token):
        await agent.cancel.fn(run_id=run_id)
    with mcp_auth_context(worker_session.token):
        claimed = await claim()

    found = stored(db_session, run_id)
    assert (claimed["run"], found.error) == (None, "cancelled before it started")
    no_revoke.assert_not_called()


@pytest.mark.asyncio
async def test_cancelling_a_lapsed_run_says_a_worker_held_it(
    db_session, regular_user, user_session, worker_session, no_revoke
):
    run_id = submit_run(db_session, regular_user)
    with mcp_auth_context(worker_session.token):
        await claim()
    expire_lease(db_session, run_id)
    work.sweep_worker_runs()
    error = "cancelled while a worker held it"

    with mcp_auth_context(user_session.token):
        await agent.cancel.fn(run_id=run_id)
    with mcp_auth_context(worker_session.token):
        claimed = await claim()

    found = stored(db_session, run_id)
    assert (claimed["run"], found.status, found.error) == (None, "cancelled", error)
    no_revoke.assert_not_called()


@pytest.mark.asyncio
async def test_a_lapsed_lease_lets_another_worker_claim(
    db_session, regular_user, worker_session, admin_session
):
    run_id = submit_run(db_session, regular_user)

    with mcp_auth_context(worker_session.token):
        stale = (await claim(worker="a"))["run"]
    expire_lease(db_session, run_id)
    work.sweep_worker_runs()
    with mcp_auth_context(admin_session.token):
        fresh = (await claim(worker="b"))["run"]
        done = await submit(run_id, fresh["lease_token"], result="no")
    with mcp_auth_context(worker_session.token):
        with pytest.raises(ValueError, match="lease token"):
            await submit(run_id, stale["lease_token"], result="late")

    assert (fresh["id"], fresh["worker_label"], fresh["lease_lapses"]) == (run_id, "b", 1)
    assert (done["status"], done["result"]) == ("done", {"text": "no"})


@pytest.mark.asyncio
async def test_a_run_whose_leases_keep_lapsing_fails(
    db_session, regular_user, worker_session, monkeypatch
):
    monkeypatch.setattr(settings, "AGENT_WORK_MAX_LAPSES", 2)
    run_id = submit_run(db_session, regular_user)

    with mcp_auth_context(worker_session.token):
        for _ in range(2):
            await claim()
            expire_lease(db_session, run_id)
            work.sweep_worker_runs()
        claimed = await claim()

    found = stored(db_session, run_id)
    assert claimed["run"] is None
    assert (found.status, found.lease_lapses) == ("failed", 2)
    assert "lease lapsed 2 times" in (found.error or "")


@pytest.mark.asyncio
async def test_a_result_that_misses_the_schema_keeps_the_lease(
    db_session, regular_user, worker_session
):
    run_id = submit_run(db_session, regular_user, result_schema=SCHEMA)

    with mcp_auth_context(worker_session.token):
        token = (await claim())["run"]["lease_token"]
        with pytest.raises(ValueError, match="answer"):
            await submit(run_id, token, result={"wrong": 1})
        still = stored(db_session, run_id).status
        done = await submit(run_id, token, result={"answer": "no"})

    assert still == "running"
    assert (done["status"], done["result"]) == ("done", {"data": {"answer": "no"}})


@pytest.mark.asyncio
async def test_resubmitting_returns_the_recorded_outcome(db_session, regular_user, worker_session):
    run_id = submit_run(db_session, regular_user)

    with mcp_auth_context(worker_session.token):
        token = (await claim())["run"]["lease_token"]
        first = await submit(run_id, token, error="could not tell")
        second = await submit(run_id, token, error="could not tell")
        with pytest.raises(ValueError, match=r"already ended \(failed\): could not tell"):
            await submit(run_id, token, result="changed my mind")

    assert first == second
    assert (second["status"], second["error"], second["result"]) == ("failed", "could not tell", None)


@pytest.mark.asyncio
async def test_submitting_to_a_run_the_server_ended_is_refused(
    db_session, regular_user, worker_session, monkeypatch
):
    monkeypatch.setattr(settings, "AGENT_WORK_MAX_LAPSES", 1)
    run_id = submit_run(db_session, regular_user)

    with mcp_auth_context(worker_session.token):
        token = (await claim())["run"]["lease_token"]
        expire_lease(db_session, run_id)
        work.sweep_worker_runs()
        with pytest.raises(ValueError, match=r"the run already ended \(failed\): lease lapsed"):
            await submit(run_id, token, error="gave up")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({}, "exactly one of result or error"),
        ({"result": "x", "error": "y"}, "exactly one of result or error"),
        ({"result": {"answer": "x"}}, "result must be text"),
        ({"result": "x", "usage": {"input_tokens": -1}}, "input_tokens"),
        ({"result": "x", "usage": {"bogus": 1}}, "bogus"),
        ({"result": "x" * (settings.AGENT_WORK_MAX_RESULT_BYTES + 1)}, "result is larger"),
        ({"error": "x" * (agent.MAX_WORKER_ERROR_CHARS + 1)}, "error is longer"),
        ({"result": "x", "usage": {"cost_usd": 0.1234567}}, "decimal places"),
        ({"result": "x", "usage": {"cost_usd": -1}}, "cost_usd"),
    ],
)
async def test_submit_rejects(db_session, regular_user, worker_session, kwargs, expected):
    run_id = submit_run(db_session, regular_user)

    with mcp_auth_context(worker_session.token):
        token = (await claim())["run"]["lease_token"]
        with pytest.raises(ValueError, match=expected):
            await submit(run_id, token, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("cost, shown", [(5.25, "5.25"), (0, "0")])
async def test_worker_reported_usage_is_shown_but_never_charged(
    db_session, regular_user, worker_session, cost, shown
):
    usage = {"input_tokens": 1200, "output_tokens": 300, "model": "claude-x", "cost_usd": cost}
    run_id = submit_run(db_session, regular_user)

    with mcp_auth_context(worker_session.token):
        token = (await claim())["run"]["lease_token"]
        done = await submit(run_id, token, result="ok", usage=usage)

    assert done["worker_usage"] == usage | {"cost_usd": shown}
    assert (done["cost_usd"], done["usage"]) == ("0.000000", {})
    assert spent_last_24h(db_session, AgentRun.user_id == regular_user.id) == 0


@pytest.mark.asyncio
async def test_a_worker_label_is_length_limited(db_session, regular_user, worker_session):
    submit_run(db_session, regular_user)

    with mcp_auth_context(worker_session.token):
        with pytest.raises(ValueError, match="worker label"):
            await claim(worker="w" * (agent.MAX_WORKER_LABEL_CHARS + 1))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,visible",
    [
        ("agent_work_claim", True),
        ("agent_work_submit", True),
        ("agent_run", False),
        ("agent_upsert", False),
        ("agent_wait", False),
        ("agent_list_runs", False),
        ("agent_cancel", False),
    ],
)
async def test_the_work_scope_reaches_only_the_work_tools(tool, visible):
    checker = get_visibility_checker(tool)
    assert checker is not None

    assert await checker({"scopes": ["agents:work"]}, None) is visible


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["agent_work_claim", "agent_work_submit"])
async def test_the_work_tools_need_the_work_scope(tool):
    checker = get_visibility_checker(tool)
    assert checker is not None

    assert await checker({"scopes": ["agents", "read", "write"]}, None) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("agent_work_claim", {}),
        ("agent_work_submit", {"run_id": "x", "lease_token": "y", "result": "z"}),
    ],
)
async def test_runs_cannot_claim_or_submit_work(db_session, admin_user, tool, arguments):
    parent = AgentRun(
        user_id=admin_user.id,
        spec=BASE,
        input="x",
        max_cost_usd=Decimal("1"),
        trigger_kind="manual",
        status="running",
        scopes=["*"],
    )
    db_session.add(parent)
    db_session.commit()
    token = mint_session(admin_user.id, 600, None)

    text, is_error = await call_as_run(token, parent.id, tool, arguments)

    assert is_error
    assert "runs can't claim or submit work" in text


@pytest.mark.parametrize(
    "spec,age_min,executor",
    [
        ({"placement": "server_first", "fallback_after": 10}, 20, "workers"),
        ({"placement": "server_first", "fallback_after": 10}, 5, "server"),
        ({"placement": "server_first"}, 10**5, "server"),
        ({"placement": "server_only"}, 10**5, "server"),
    ],
)
def test_sweep_moves_unclaimed_server_first_runs_to_workers(
    db_session, regular_user, fake_providers, no_celery_dispatch, spec, age_min, executor
):
    run_id = submit_run(db_session, regular_user, **spec)
    backdate(db_session, run_id, minutes=age_min)

    work.sweep_worker_runs()

    found = stored(db_session, run_id)
    assert (found.status, found.executor) == ("pending", executor)


def test_a_run_moved_to_workers_drops_its_server_budget(
    db_session, regular_user, fake_providers, no_celery_dispatch
):
    run_id = submit_run(db_session, regular_user, placement="server_first", fallback_after=10)
    backdate(db_session, run_id, minutes=20)

    work.sweep_worker_runs()

    found = stored(db_session, run_id)
    assert (found.executor, found.max_cost_usd, found.provider_key_id) == ("workers", 0, None)
    assert found.spec["max_output_tokens"] is None


@pytest.mark.parametrize(
    "spec,age_min,executor,dispatched",
    [
        ({"placement": "runner_first", "fallback_after": 10}, 20, "server", True),
        ({"placement": "runner_first", "fallback_after": 10}, 5, "workers", False),
        ({"placement": "runner_first"}, 60 * 24, "workers", False),
        ({"placement": "runner_only"}, 60 * 24, "workers", False),
    ],
)
def test_sweep_moves_unclaimed_runner_first_runs_to_the_server(
    db_session, regular_user, fake_providers, no_celery_dispatch, spec, age_min, executor, dispatched
):
    run_id = submit_run(db_session, regular_user, **spec)
    backdate(db_session, run_id, minutes=age_min)

    work.sweep_worker_runs()

    found = stored(db_session, run_id)
    assert (found.status, found.executor) == ("pending", executor)
    assert no_celery_dispatch.called is dispatched


def test_a_fallback_to_the_server_dispatches_the_run_with_a_fresh_budget(
    db_session, regular_user, fake_providers, no_celery_dispatch
):
    run_id = submit_run(db_session, regular_user, placement="runner_first", fallback_after=10)
    backdate(db_session, run_id, minutes=20)

    work.sweep_worker_runs()

    no_celery_dispatch.assert_called_once_with(RUN_AGENT, args=[run_id], task_id=run_id)
    found = stored(db_session, run_id)
    assert found.max_cost_usd > 0


def test_a_fallback_against_a_spent_pool_fails_the_run(
    db_session, regular_user, fake_providers, no_celery_dispatch, monkeypatch
):
    run_id = submit_run(db_session, regular_user, placement="runner_first", fallback_after=10)
    backdate(db_session, run_id, minutes=20)
    monkeypatch.setattr(settings, "AGENT_DEFAULT_USER_KEY_DAILY_COST_USD", Decimal(0))

    work.sweep_worker_runs()

    found = stored(db_session, run_id)
    assert found.status == "failed"
    assert "provider key pool" in (found.error or "")
    no_celery_dispatch.assert_not_called()


def test_a_fallen_back_run_waiting_for_the_server_is_not_orphaned(
    db_session, regular_user, fake_providers, no_celery_dispatch
):
    run_id = submit_run(db_session, regular_user, placement="runner_first", fallback_after=60)
    backdate(db_session, run_id, minutes=120)
    work.sweep_worker_runs()

    sweep_agent_runs()

    assert (stored(db_session, run_id).status, stored(db_session, run_id).executor) == (
        "pending", "server",
    )


@pytest.mark.asyncio
async def test_a_claimed_run_whose_lease_lapsed_stays_on_workers(
    db_session, regular_user, worker_session, fake_providers, no_celery_dispatch
):
    run_id = submit_run(db_session, regular_user, placement="runner_first", fallback_after=10)

    with mcp_auth_context(worker_session.token):
        await claim()
    expire_lease(db_session, run_id)
    backdate(db_session, run_id, minutes=20)
    work.sweep_worker_runs()

    found = stored(db_session, run_id)
    assert (found.status, found.executor) == ("pending", "workers")
    no_celery_dispatch.assert_not_called()


def test_a_fallback_the_server_cant_take_fails_the_run(
    db_session, regular_user, fake_providers, no_celery_dispatch
):
    run_id = submit_run(db_session, regular_user, placement="runner_first", fallback_after=10)
    backdate(db_session, run_id, minutes=20)
    db_session.query(LLMProviderKey).delete()
    db_session.commit()

    work.sweep_worker_runs()

    found = stored(db_session, run_id)
    assert found.status == "failed"
    assert "the server can't run it" in (found.error or "")
    no_celery_dispatch.assert_not_called()


@pytest.mark.parametrize(
    "spec,age_days,status",
    [
        ({}, 15, "failed"),
        ({}, 13, "pending"),
        ({"retention_days": 2}, 3, "failed"),
    ],
)
def test_sweep_ends_worker_runs_nobody_claimed_within_retention(
    db_session, regular_user, spec, age_days, status
):
    run_id = submit_run(db_session, regular_user, **spec)
    backdate(db_session, run_id, days=age_days)

    work.sweep_worker_runs()

    assert stored(db_session, run_id).status == status


def test_sweep_releases_lapsed_leases(db_session, regular_user):
    run_id = submit_run(db_session, regular_user)
    found = db_session.get(AgentRun, run_id)
    found.status, found.lease_token = "running", "t"
    found.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    found.started_at = found.lease_expires_at - timedelta(minutes=10)
    db_session.commit()

    work.sweep_worker_runs()

    found = stored(db_session, run_id)
    assert (found.status, found.lease_lapses) == ("pending", 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"placement": "runner_only"}, "runner_only"),
        ({"placement": "runner_first", "fallback_after": 30}, "runner_first"),
    ],
)
async def test_agent_run_may_override_the_placement_at_top_level(
    db_session, regular_user, user_session, fake_providers, no_celery_dispatch, overrides, expected
):
    with mcp_auth_context(user_session.token):
        started = await agent.run.fn(input="hi", agent=BASE, **overrides)

    found = stored(db_session, started["run_id"])
    assert (found.spec["placement"], found.executor) == (expected, "workers")


@pytest.mark.asyncio
async def test_system_agents_stay_on_the_server(
    db_session, regular_user, user_session, fake_providers
):
    with mcp_auth_context(user_session.token):
        with pytest.raises(ValueError, match="system agents run on the server"):
            await agent.run.fn(input="hi", agent="system:summarise", placement="runner_only")
