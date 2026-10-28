"""Runs that start runs: an agent opts in with agent_run in its tools, and its
children stay inside its depth, budget, scopes and tools."""

import json
from decimal import Decimal
from unittest.mock import patch

import pytest

from memory.agents.caps import children_cost, daily_pools
from memory.agents import cancel
from memory.agents.enqueue import TriggerInfo, enqueue_run
from memory.agents.mcp_client import memory_tools
from memory.agents.runner import execute_run, mint_session
from memory.agents.spec import AgentSpec
from memory.common import settings
from memory.common.db.connection import make_session
from memory.api.MCP.servers import agent
from memory.common.db.models import AgentDefinition, AgentRun, APIKey
from tests.conftest import mcp_auth_context
from tests.memory.agents.fakes import DEFAULT_USAGE, PRICING, FakeHub, calls, reply

D = Decimal
PARENT_TOOLS = ["agent_run", "agent_wait", "agent_cancel", "meta_get_user"]
CHILD = {"system_prompt": "help", "model": "anthropic/m", "tools": ["meta_get_user"]}


def add_run(db, user, *, tools=PARENT_TOOLS, status="running", **fields):
    run = AgentRun(
        user_id=user.id,
        spec={
            "system_prompt": "x",
            "model": "anthropic/m",
            "max_output_tokens": 100,
            "tools": tools,
        },
        input="x",
        max_cost_usd=fields.pop("max_cost_usd", D("1")),
        trigger_kind="manual",
        status=status,
        scopes=fields.pop("scopes", ["*"]),
        **fields,
    )
    db.add(run)
    db.commit()
    return run


async def call_as_run(token: str, run_id: str, tool: str, arguments: dict) -> tuple[str, bool]:
    async with memory_tools(token, ["*"], run_id=run_id) as tools:
        return await tools.call(tool, arguments)


async def submit(token: str, parent_id: str, agent=CHILD) -> AgentRun:
    text, is_error = await call_as_run(token, parent_id, "agent_run", {"input": "go", "agent": agent})
    assert not is_error, text
    with make_session() as db:
        child = db.get(AgentRun, json.loads(text)["run_id"])
        assert child is not None
        db.expunge(child)
        return child


@pytest.mark.asyncio
async def test_a_child_records_its_lineage_and_narrower_credentials(
    db_session, admin_user, admin_keys, no_celery_dispatch
):
    key = APIKey.create(user_id=admin_user.id, name="k")
    db_session.add(key)
    db_session.commit()
    parent = add_run(db_session, admin_user, scopes=["read", "agents"], api_key_id=key.id)
    # The session the runner mints for a run is bound to the run's API key.
    token = mint_session(admin_user.id, 600, key.id)

    child = await submit(token, parent.id)

    assert (child.parent_run_id, child.depth, child.trigger_kind, child.api_key_id) == (
        parent.id, 1, "agent", key.id,
    )
    assert set(child.scopes) <= {"read", "agents"}


@pytest.mark.asyncio
async def test_a_call_from_a_run_that_no_longer_exists_is_refused(
    db_session, admin_user, admin_session, admin_keys, no_celery_dispatch
):
    text, is_error = await call_as_run(
        admin_session.token, "gone", "agent_run", {"input": "go", "agent": CHILD}
    )

    assert is_error
    assert "no longer exists" in text
    assert db_session.query(AgentRun).count() == 0


@pytest.mark.asyncio
async def test_children_stop_at_the_depth_limit(
    db_session, admin_user, admin_session, admin_keys, no_celery_dispatch, monkeypatch
):
    monkeypatch.setattr(settings, "AGENT_MAX_SPAWN_DEPTH", 2)
    parent = add_run(db_session, admin_user, depth=2)

    text, is_error = await call_as_run(
        admin_session.token, parent.id, "agent_run", {"input": "go", "agent": CHILD}
    )

    assert is_error
    assert "AGENT_MAX_SPAWN_DEPTH" in text
    assert db_session.query(AgentRun).count() == 1


@pytest.mark.asyncio
async def test_a_child_cannot_use_tools_its_parent_lacks(
    db_session, admin_user, admin_session, admin_keys, no_celery_dispatch
):
    parent = add_run(db_session, admin_user, tools=["agent_run"])

    text, is_error = await call_as_run(
        admin_session.token, parent.id, "agent_run", {"input": "go", "agent": CHILD | {"tools": ["web_fetch"]}}
    )

    assert is_error
    assert "web_fetch" in text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "child_cap,expected",
    [
        # The parent has 1 - 0.4 spent - 0.3 held by a live child = 0.3 left; a child
        # without its own cap gets half, so the parent can still afford to wait for it.
        (None, D("0.15")),
        ("0.1", D("0.1")),
        ("0.5", D("0.3")),
    ],
)
async def test_a_child_is_capped_by_what_its_parent_has_left(
    db_session, admin_user, admin_session, admin_keys, no_celery_dispatch, child_cap, expected
):
    parent = add_run(db_session, admin_user, cost_usd=D("0.4"))
    add_run(db_session, admin_user, parent_run_id=parent.id, depth=1, max_cost_usd=D("0.3"))

    child = await submit(admin_session.token, parent.id, CHILD | {"max_cost_usd": child_cap})

    assert child.max_cost_usd == expected


@pytest.mark.asyncio
async def test_a_parent_with_nothing_left_starts_no_child(
    db_session, admin_user, admin_session, admin_keys, no_celery_dispatch
):
    parent = add_run(db_session, admin_user, cost_usd=D("1"))

    text, is_error = await call_as_run(
        admin_session.token, parent.id, "agent_run", {"input": "go", "agent": CHILD}
    )

    assert is_error
    assert "parent run" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["agent_wait", "agent_cancel"])
async def test_a_run_reaches_only_its_own_children(
    db_session, admin_user, admin_session, tool
):
    parent = add_run(db_session, admin_user)
    child = add_run(db_session, admin_user, parent_run_id=parent.id, depth=1, status="done")
    other = add_run(db_session, admin_user, status="done")

    own_text, own_error = await call_as_run(admin_session.token, parent.id, tool, {"run_id": child.id})
    other_text, other_error = await call_as_run(admin_session.token, parent.id, tool, {"run_id": other.id})

    assert not own_error, own_text
    assert other_error
    assert f"unknown run {other.id}" in other_text


@pytest.mark.asyncio
async def test_cancelling_a_run_cancels_its_live_descendants(
    db_session, admin_user, admin_session, no_celery_dispatch
):
    parent = add_run(db_session, admin_user)
    child = add_run(db_session, admin_user, parent_run_id=parent.id, depth=1)
    grandchild = add_run(db_session, admin_user, parent_run_id=child.id, depth=2, status="pending")
    finished = add_run(db_session, admin_user, parent_run_id=parent.id, depth=1, status="done")

    with patch.object(cancel.app.control, "revoke") as revoke:
        with mcp_auth_context(admin_session.token):
            await agent.cancel.fn(run_id=parent.id)

    db_session.expire_all()
    states = {
        r.id: (r.status, r.cancel_requested_at is not None)
        for r in db_session.query(AgentRun)
    }
    assert states == {
        parent.id: ("running", True),
        child.id: ("running", True),
        grandchild.id: ("cancelled", True),
        finished.id: ("done", False),
    }
    revoke.assert_called_once_with(grandchild.id)


@pytest.mark.parametrize(
    "children,expected",
    [
        ([], D("0")),
        # A live child holds its whole cap; a finished one counts what it spent.
        ([("running", "0.1", "0.5")], D("0.5")),
        ([("done", "0.1", "0.5")], D("0.1")),
        ([("done", "0.1", "0.5"), ("pending", "0", "0.2")], D("0.3")),
    ],
)
def test_children_cost(db_session, regular_user, children, expected):
    parent = add_run(db_session, regular_user)
    for status, cost, cap in children:
        add_run(
            db_session, regular_user, parent_run_id=parent.id, depth=1,
            status=status, cost_usd=D(cost), max_cost_usd=D(cap),
        )

    assert children_cost(db_session, parent.id) == expected


def test_a_finished_childs_own_live_children_still_count(db_session, regular_user):
    parent = add_run(db_session, regular_user)
    child = add_run(
        db_session, regular_user, parent_run_id=parent.id, depth=1,
        status="done", cost_usd=D("0.1"), max_cost_usd=D("0.6"),
    )
    add_run(db_session, regular_user, parent_run_id=child.id, depth=2, max_cost_usd=D("0.4"))

    assert children_cost(db_session, parent.id) == D("0.5")


def test_a_definitions_daily_pool_counts_its_runs_descendants(db_session, regular_user, fake_providers):
    definition = AgentDefinition(user_id=regular_user.id, name="d", spec={})
    db_session.add(definition)
    db_session.commit()
    parent = add_run(db_session, regular_user, definition_id=definition.id, cost_usd=D("1"))
    child = add_run(db_session, regular_user, parent_run_id=parent.id, depth=1, cost_usd=D("2"))
    add_run(db_session, regular_user, parent_run_id=child.id, depth=2, cost_usd=D("3"))

    pools = daily_pools(
        db_session,
        spec_daily_cap=D("10"),
        definition=definition,
        api_key=None,
        provider_key=fake_providers.keys["anthropic"],
    )

    assert {p.label: p.spent for p in pools}["definition pool"] == D("6")


@pytest.mark.asyncio
async def test_a_parent_can_wait_for_its_child_and_ending_cancels_it(
    db_session, regular_user, fake_providers, no_celery_dispatch
):
    parent = add_run(db_session, regular_user, status="pending", scopes=["*"])
    parent.provider_key_id = fake_providers.keys["anthropic"].id
    db_session.commit()
    started = []

    def spawn(_):
        with make_session() as db:
            found = db.get(AgentRun, parent.id)
            assert found is not None
            child = enqueue_run(
                db, user_id=regular_user.id, spec=AgentSpec.model_validate(CHILD),
                input="go", trigger=TriggerInfo(kind="agent"), parent=found,
            )
            started.append(child.id)
        return "started", False

    fake_providers.script = [
        calls(("t1", "spawn", {})),
        calls(("t2", "wait", {})),
        reply("done"),
    ]

    hub = FakeHub({"spawn": spawn, "wait": lambda _: ("still running", False)})
    with patch.object(cancel.app.control, "revoke") as revoke:
        await execute_run(parent.id, hub.factory())

    db_session.expire_all()
    finished = db_session.get(AgentRun, parent.id)
    child = db_session.get(AgentRun, started[0])
    assert finished is not None and child is not None
    assert (finished.status, len(fake_providers.requests)) == ("done", 3)
    # Half of what the parent had left after its first turn.
    assert (child.status, child.max_cost_usd) == ("cancelled", (1 - PRICING.cost(DEFAULT_USAGE)) / 2)
    revoke.assert_called_once_with(child.id)
