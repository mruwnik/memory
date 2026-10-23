# pyright: reportFunctionMemberAccess=false
import pytest

from memory.api.MCP.servers import agent
from memory.common import settings
from memory.common.db.models import AgentDefinition, APIKey, ScheduledTask
from tests.conftest import mcp_auth_context

SPEC = {"system_prompt": "x", "model": "fake-rich/m", "reasoning": "high"}


def definition(db, user, spec=SPEC):
    d = AgentDefinition(user_id=user.id, name="d", spec=spec)
    db.add(d)
    db.commit()
    return d


@pytest.mark.asyncio
async def test_schedule_creates_lists_updates_and_deletes(
    db_session, regular_user, user_session, fake_providers
):
    d = definition(db_session, regular_user)

    with mcp_auth_context(user_session.id):
        created = await agent.schedule.fn(
            definition_id=d.id, input="digest", cron_expression="0 9 * * *"
        )
        updated = await agent.schedule.fn(
            definition_id=d.id,
            task_id=created["id"],
            input="weekly digest",
            cron_expression="0 9 * * 1",
            enabled=False,
        )
        listed = await agent.schedules.fn(definition_id=d.id)
        await agent.unschedule.fn(task_id=created["id"])

    assert (created["task_type"], created["data"], created["enabled"]) == (
        "agent_run",
        {"definition_id": d.id, "api_key_id": None, "input": "digest"},
        True,
    )
    assert (updated["data"]["input"], updated["cron_expression"], updated["enabled"]) == (
        "weekly digest",
        "0 9 * * 1",
        False,
    )
    assert [t["id"] for t in listed["schedules"]] == [created["id"]]
    assert db_session.query(ScheduledTask).count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"cron_expression": "*/1 * * * *"}, "too short"),
        ({}, "exactly one of cron_expression or scheduled_time"),
        ({"cron_expression": "0 9 * * *", "input": ""}, "input is required"),
    ],
)
async def test_schedule_validates(
    db_session, regular_user, user_session, fake_providers, kwargs, expected
):
    d = definition(db_session, regular_user)

    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match=expected):
            await agent.schedule.fn(definition_id=d.id, **({"input": "x"} | kwargs))


@pytest.mark.asyncio
async def test_schedule_counts_toward_the_task_cap(
    db_session, regular_user, user_session, fake_providers, monkeypatch
):
    monkeypatch.setattr(settings, "MAX_SCHEDULED_TASKS_PER_USER", 1)
    d = definition(db_session, regular_user)

    with mcp_auth_context(user_session.id):
        await agent.schedule.fn(definition_id=d.id, input="a", cron_expression="0 9 * * *")
        with pytest.raises(ValueError, match="Maximum"):
            await agent.schedule.fn(
                definition_id=d.id, input="b", cron_expression="0 10 * * *"
            )


@pytest.mark.asyncio
async def test_other_users_definitions_and_schedules_are_unknown(
    db_session, admin_user, regular_user, user_session, fake_providers
):
    theirs = definition(db_session, admin_user)
    task = ScheduledTask(
        user_id=admin_user.id,
        task_type="agent_run",
        cron_expression="0 9 * * *",
        data={"definition_id": theirs.id, "input": "x"},
    )
    db_session.add(task)
    db_session.commit()

    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match="unknown agent definition"):
            await agent.schedule.fn(
                definition_id=theirs.id, input="x", cron_expression="0 9 * * *"
            )
        with pytest.raises(ValueError, match="unknown agent definition"):
            await agent.schedules.fn(definition_id=theirs.id)
        with pytest.raises(ValueError, match="unknown schedule"):
            await agent.unschedule.fn(task_id=task.id)


@pytest.mark.asyncio
async def test_schedules_are_paused_and_resumed_without_restating_input(
    db_session, regular_user, user_session, fake_providers
):
    d = definition(db_session, regular_user)

    with mcp_auth_context(user_session.id):
        created = await agent.schedule.fn(
            definition_id=d.id, input="digest", cron_expression="0 9 * * *"
        )
        paused = await agent.schedule.fn(
            definition_id=d.id, task_id=created["id"], enabled=False
        )
        resumed = await agent.schedule.fn(
            definition_id=d.id, task_id=created["id"], enabled=True
        )

    assert (paused["enabled"], resumed["enabled"]) == (False, True)
    assert resumed["data"]["input"] == "digest"


@pytest.mark.asyncio
async def test_resuming_respects_the_task_cap(
    db_session, regular_user, user_session, fake_providers, monkeypatch
):
    d = definition(db_session, regular_user)
    with mcp_auth_context(user_session.id):
        paused = await agent.schedule.fn(
            definition_id=d.id, input="a", cron_expression="0 9 * * *", enabled=False
        )
        await agent.schedule.fn(definition_id=d.id, input="b", cron_expression="0 10 * * *")
    monkeypatch.setattr(settings, "MAX_SCHEDULED_TASKS_PER_USER", 1)

    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match="Maximum"):
            await agent.schedule.fn(definition_id=d.id, task_id=paused["id"], enabled=True)


@pytest.mark.asyncio
async def test_renaming_the_definition_refreshes_the_topic_on_update(
    db_session, regular_user, user_session, fake_providers
):
    d = definition(db_session, regular_user)
    with mcp_auth_context(user_session.id):
        created = await agent.schedule.fn(
            definition_id=d.id, input="a", cron_expression="0 9 * * *"
        )
        d.name = "renamed"
        db_session.commit()
        updated = await agent.schedule.fn(definition_id=d.id, task_id=created["id"], input="b")

    assert (created["topic"], updated["topic"]) == ("agent: d", "agent: renamed")


@pytest.mark.asyncio
async def test_deleting_a_definition_deletes_its_schedules(
    db_session, regular_user, user_session, fake_providers
):
    d = definition(db_session, regular_user)
    with mcp_auth_context(user_session.id):
        await agent.schedule.fn(definition_id=d.id, input="a", cron_expression="0 9 * * *")
        await agent.delete.fn(definition_id=d.id)

    assert db_session.query(ScheduledTask).count() == 0


def scoped_key(db, user, scopes):
    key = APIKey.create(user_id=user.id, name="k", scopes=scopes)
    db.add(key)
    db.commit()
    return key


@pytest.mark.asyncio
async def test_a_key_cannot_schedule_tools_it_could_not_run(
    db_session, regular_user, fake_providers
):
    d = definition(db_session, regular_user, SPEC | {"tools": ["core_search"]})
    key = scoped_key(db_session, regular_user, ["agents"])

    with mcp_auth_context(key.key, scopes=["agents"]):
        with pytest.raises(ValueError, match="tool 'core_search' is not visible to you"):
            await agent.schedule.fn(
                definition_id=d.id, input="a", cron_expression="0 9 * * *"
            )


@pytest.mark.asyncio
async def test_a_schedule_remembers_the_key_that_made_it(
    db_session, regular_user, fake_providers
):
    d = definition(db_session, regular_user, SPEC | {"tools": ["core_search"]})
    key = scoped_key(db_session, regular_user, ["agents", "read"])

    with mcp_auth_context(key.key, scopes=["agents", "read"]):
        created = await agent.schedule.fn(
            definition_id=d.id, input="a", cron_expression="0 9 * * *"
        )

    assert created["data"]["api_key_id"] == key.id
