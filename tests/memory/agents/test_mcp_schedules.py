"""Schedules that run an agent definition, through the scheduler tools."""
# pyright: reportFunctionMemberAccess=false

import pytest

from memory.api.MCP.servers import agent, scheduler
from memory.common.db.models import AgentDefinition, AgentTrigger, APIKey, Event
from tests.conftest import mcp_auth_context

SPEC = {"system_prompt": "x", "model": "anthropic/m", "reasoning": "high"}


@pytest.fixture(autouse=True)
def may_run_agents(db_session, regular_user):
    regular_user.scopes = ["agents", "schedule", "schedule:write"]
    db_session.commit()


def definition(db, user, spec=SPEC, name="d"):
    d = AgentDefinition(user_id=user.id, name=name, spec=spec)
    db.add(d)
    db.commit()
    return d


@pytest.mark.asyncio
async def test_an_agent_schedule_is_created_updated_listed_and_goes_with_its_definition(
    db_session, regular_user, user_session, fake_providers
):
    d = definition(db_session, regular_user)
    other = definition(db_session, regular_user, name="other")

    with mcp_auth_context(user_session.token):
        created = await scheduler.upsert.fn(definition_id=d.id, input="digest", cron_expression="0 9 * * *")
        updated = await scheduler.upsert.fn(
            schedule_id=created["id"], input="weekly digest", cron_expression="0 9 * * 1"
        )
        listed = await scheduler.list_all.fn(definition_id=d.id)
        elsewhere = await scheduler.list_all.fn(definition_id=other.id)
        ingest_triggers = await agent.triggers.fn(definition_id=d.id)
        await agent.delete.fn(definition_id=d.id)

    assert (created["handler"], created["definition_id"], created["config"]) == (
        {"agent": d.id}, d.id, {"cron": "0 9 * * *", "input": "digest"},
    )
    assert updated["config"] == {"cron": "0 9 * * 1", "input": "weekly digest"}
    assert ([s["id"] for s in listed["schedules"]], elsewhere["schedules"]) == ([created["id"]], [])
    assert ingest_triggers == {"triggers": []}
    assert db_session.query(AgentTrigger).count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"cron_expression": "0 9 * * *", "input": ""}, "input is required"),
        ({"cron_expression": "0 9 * * *", "input": "x", "definition_id": 999_999}, "unknown agent definition"),
    ],
)
async def test_an_agent_schedule_is_validated(
    db_session, regular_user, user_session, fake_providers, kwargs, expected
):
    d = definition(db_session, regular_user)

    with mcp_auth_context(user_session.token):
        with pytest.raises(ValueError, match=expected):
            await scheduler.upsert.fn(**({"definition_id": d.id} | kwargs))


@pytest.mark.asyncio
async def test_running_an_agent_on_a_schedule_needs_the_agents_scope(
    db_session, regular_user, fake_providers
):
    d = definition(db_session, regular_user)
    key = APIKey.create(user_id=regular_user.id, scopes=["schedule", "schedule:write"])
    db_session.add(key)
    db_session.commit()

    with mcp_auth_context(key.key, scopes=["schedule", "schedule:write"]):
        with pytest.raises(ValueError, match="'agents' scope"):
            await scheduler.upsert.fn(definition_id=d.id, input="x", cron_expression="0 9 * * *")


@pytest.mark.asyncio
async def test_agent_trigger_tools_leave_schedules_alone(
    db_session, regular_user, user_session, fake_providers
):
    d = definition(db_session, regular_user)
    with mcp_auth_context(user_session.token):
        created = await scheduler.upsert.fn(definition_id=d.id, input="x", cron_expression="0 9 * * *")
        with pytest.raises(ValueError, match="unknown trigger"):
            await agent.trigger_delete.fn(trigger_id=created["id"])


@pytest.mark.asyncio
async def test_deleting_a_definition_cancels_its_waiting_timers(
    db_session, regular_user, user_session, fake_providers
):
    d = definition(db_session, regular_user)
    with mcp_auth_context(user_session.token):
        timer = await scheduler.upsert.fn(definition_id=d.id, input="x", scheduled_time="2999-01-01T09:00:00Z")
        await agent.delete.fn(definition_id=d.id)

    assert db_session.get_one(Event, timer["id"]).result == {"status": "skipped:cancelled"}
