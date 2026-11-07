"""The scheduler tools: cron subscriptions with notify handlers, and timers."""
# pyright: reportFunctionMemberAccess=false

from datetime import datetime, timedelta, timezone

import pytest

from memory.api.MCP.servers import scheduler
from memory.common import settings
from memory.common.db import connection as db_connection
from memory.common.db.models import AgentTrigger, APIKey, Event
from tests.conftest import mcp_auth_context

MESSAGE = dict(
    message="hi",
    notification_channel="email",
    notification_target="a@example.com",
    topic="Daily ping",
)


@pytest.fixture(autouse=True)
def reset_db_cache():
    db_connection._engine = None
    db_connection._session_factory = None
    yield
    db_connection._engine = None
    db_connection._session_factory = None


def schedule_key(db, user, scopes=("schedule", "schedule:write")):
    key = APIKey.create(user_id=user.id, scopes=list(scopes))
    db.add(key)
    db.commit()
    return key


@pytest.mark.asyncio
async def test_a_recurring_message_is_a_cron_subscription_in_the_owners_timezone(
    db_session, regular_user, user_session
):
    regular_user.timezone = "Asia/Kolkata"
    db_session.commit()

    with mcp_auth_context(user_session.token):
        result = await scheduler.upsert.fn(cron_expression="0 9 * * *", **MESSAGE)

    assert (result["kind"], result["config"], result["enabled"], result["api_key_id"]) == (
        "cron", {"cron": "0 9 * * *", "input": ""}, True, None,
    )
    assert result["handler"] == {
        "notify": {"channel": "email", "target": "a@example.com", "message": "hi", "subject": "Daily ping"}
    }
    assert result["name"] == f"schedule-{result['id']}"
    assert datetime.fromisoformat(result["next_at"]).astimezone(timezone.utc).strftime("%H:%M") == "03:30"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scheduled_time, fires",
    [
        ("2999-01-01T09:00:00Z", datetime(2999, 1, 1, 9, tzinfo=timezone.utc)),
        ("2999-01-01T09:00:00+01:00", datetime(2999, 1, 1, 8, tzinfo=timezone.utc)),
        ("2999-01-01T09:00:00", datetime(2999, 1, 1, 3, 30, tzinfo=timezone.utc)),
    ],
)
async def test_a_one_off_message_is_a_timer(db_session, regular_user, user_session, scheduled_time, fires):
    regular_user.timezone = "Asia/Kolkata"
    db_session.commit()

    with mcp_auth_context(user_session.token):
        result = await scheduler.upsert.fn(scheduled_time=scheduled_time, **MESSAGE)

    timer = db_session.get_one(Event, result["id"])
    assert (result["kind"], timer.kind, timer.fire_at, timer.handler["notify"]["message"]) == (
        "timer", "timer", fires, "hi",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs, match",
    [
        (dict(MESSAGE, cron_expression="0 9 * * *", scheduled_time="2999-01-01T09:00:00Z"), "exactly one"),
        (MESSAGE, "exactly one"),
        (dict(MESSAGE, cron_expression="*/1 * * * *"), "too short"),
        (dict(cron_expression="0 9 * * *", message="x"), "needs notification_channel"),
        (dict(MESSAGE, cron_expression="0 9 * * *", notification_channel="sms"), "must be one of"),
        (dict(MESSAGE, cron_expression="0 9 * * *", notification_target="not-an-email"), "No email address"),
        (dict(MESSAGE, scheduled_time="2000-01-01T09:00:00Z"), "in the future"),
        (dict(MESSAGE, scheduled_time="2999-01-01T09:00:00Z", enabled=False), "can't be created paused"),
        (dict(MESSAGE, cron_expression="0 9 * * *", definition_id=1, input="x"), "not both"),
    ],
)
async def test_create_refuses(db_session, regular_user, user_session, kwargs, match):
    with mcp_auth_context(user_session.token):
        with pytest.raises(ValueError, match=match):
            await scheduler.upsert.fn(**kwargs)
    assert db_session.query(AgentTrigger).count() == db_session.query(Event).count() == 0


@pytest.mark.asyncio
async def test_update_changes_in_place_and_revalidates_the_target(db_session, regular_user, user_session):
    with mcp_auth_context(user_session.token):
        created = await scheduler.upsert.fn(cron_expression="0 9 * * *", **MESSAGE)
        updated = await scheduler.upsert.fn(schedule_id=created["id"], message="renamed", name="ping")
        with pytest.raises(ValueError, match="not a known person, channel, or user id"):
            await scheduler.upsert.fn(schedule_id=created["id"], notification_channel="slack")

    assert (updated["id"], updated["name"], updated["handler"]["notify"]) == (
        created["id"], "ping",
        {"channel": "email", "target": "a@example.com", "message": "renamed", "subject": "Daily ping"},
    )


@pytest.mark.asyncio
async def test_pausing_clears_the_next_tick_and_enabling_reschedules_it(db_session, regular_user, user_session):
    with mcp_auth_context(user_session.token):
        created = await scheduler.upsert.fn(cron_expression="0 9 * * *", **MESSAGE)
        trigger = db_session.get_one(AgentTrigger, created["id"])
        trigger.state = {"tripped": {"reason": "x", "at": "2026-01-01T00:00:00+00:00"}, "failures": 3}
        db_session.commit()
        paused = await scheduler.upsert.fn(schedule_id=created["id"], enabled=False)
        resumed = await scheduler.upsert.fn(schedule_id=created["id"], enabled=True)

    assert (paused["enabled"], paused["next_at"]) == (False, None)
    assert (resumed["enabled"], resumed["next_at"] is not None, resumed["state"]) == (True, True, {})


@pytest.mark.asyncio
async def test_enabling_keeps_to_the_trigger_cap(db_session, regular_user, user_session, monkeypatch):
    monkeypatch.setattr(settings, "AGENT_MAX_TRIGGERS_PER_USER", 1)
    with mcp_auth_context(user_session.token):
        paused = await scheduler.upsert.fn(cron_expression="0 9 * * *", enabled=False, **MESSAGE)
        await scheduler.upsert.fn(cron_expression="0 10 * * *", **MESSAGE)
        with pytest.raises(ValueError, match="AGENT_MAX_TRIGGERS_PER_USER"):
            await scheduler.upsert.fn(schedule_id=paused["id"], enabled=True)


@pytest.mark.asyncio
async def test_a_key_may_change_only_what_it_set_up(db_session, regular_user, user_session):
    key = schedule_key(db_session, regular_user)
    with mcp_auth_context(user_session.token):
        mine = await scheduler.upsert.fn(cron_expression="0 9 * * *", **MESSAGE)

    with mcp_auth_context(key.key, scopes=["schedule", "schedule:write"]):
        made = await scheduler.upsert.fn(cron_expression="0 10 * * *", **MESSAGE)
        with pytest.raises(ValueError, match="another credential"):
            await scheduler.upsert.fn(schedule_id=mine["id"], message="x")
        with pytest.raises(ValueError, match="another credential"):
            await scheduler.delete.fn(schedule_id=mine["id"])
        with pytest.raises(ValueError, match="another credential"):
            await scheduler.upsert.fn(schedule_id=mine["id"], enabled=False)

    assert (made["api_key_id"], made["via_api_key"]) == (key.id, True)


@pytest.mark.asyncio
async def test_a_schedule_whose_key_stopped_working_can_be_paused_by_its_owner(
    db_session, regular_user, user_session
):
    key = schedule_key(db_session, regular_user)
    with mcp_auth_context(key.key, scopes=["schedule", "schedule:write"]):
        made = await scheduler.upsert.fn(cron_expression="0 10 * * *", **MESSAGE)
    key.revoked = True
    db_session.commit()

    with mcp_auth_context(user_session.token):
        paused = await scheduler.upsert.fn(schedule_id=made["id"], enabled=False)

    assert paused["enabled"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change, moves",
    [
        ({"message": "renamed"}, False),
        ({"name": "renamed"}, False),
        ({"cron_expression": "0 10 * * *"}, True),
    ],
)
async def test_next_tick_moves_only_with_the_cron(db_session, regular_user, user_session, change, moves):
    far = datetime(2999, 1, 1, 9, tzinfo=timezone.utc)
    with mcp_auth_context(user_session.token):
        created = await scheduler.upsert.fn(cron_expression="0 9 * * *", **MESSAGE)
        trigger = db_session.get_one(AgentTrigger, created["id"])
        trigger.next_at = far
        db_session.commit()
        updated = await scheduler.upsert.fn(schedule_id=created["id"], **change)

    assert (datetime.fromisoformat(updated["next_at"]) != far) is moves


@pytest.mark.asyncio
async def test_list_delete_and_cancel(db_session, regular_user, user_session):
    with mcp_auth_context(user_session.token):
        cron = await scheduler.upsert.fn(cron_expression="0 9 * * *", **MESSAGE)
        timer = await scheduler.upsert.fn(scheduled_time="2999-01-01T09:00:00Z", **MESSAGE)
        listed = await scheduler.list_all.fn()
        await scheduler.delete.fn(timer_id=timer["id"])
        await scheduler.delete.fn(schedule_id=cron["id"])
        after = await scheduler.list_all.fn()
        with_fired = await scheduler.list_all.fn(include_fired=True)
        with pytest.raises(ValueError, match="already fired"):
            await scheduler.delete.fn(timer_id=timer["id"])

    assert ([s["id"] for s in listed["schedules"]], [t["id"] for t in listed["timers"]]) == (
        [cron["id"]], [timer["id"]],
    )
    assert after == {"schedules": [], "timers": []}
    assert [(t["id"], t["result"]) for t in with_fired["fired"]] == [
        (timer["id"], {"status": "skipped:cancelled"})
    ]
    assert db_session.get_one(Event, timer["id"]).result == {"status": "skipped:cancelled"}


@pytest.mark.asyncio
async def test_history_lists_ticks_newest_first(db_session, regular_user, user_session):
    with mcp_auth_context(user_session.token):
        cron = await scheduler.upsert.fn(cron_expression="0 9 * * *", **MESSAGE)
    now = datetime.now(timezone.utc)
    db_session.add_all(
        Event(kind="tick", trigger_id=cron["id"], owner_id=regular_user.id, fire_at=at,
              data={"missed": m}, result=r)
        for at, m, r in [(now - timedelta(days=1), 0, {"status": "sent"}), (now, 2, None)]
    )
    db_session.commit()

    with mcp_auth_context(user_session.token):
        history = await scheduler.history.fn(schedule_id=cron["id"])

    assert [(h["missed"], h["result"]) for h in history] == [(2, None), (0, {"status": "sent"})]
