# pyright: reportOptionalMemberAccess=false, reportOptionalSubscript=false, reportOptionalOperand=false
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from memory.agents import clock, subscriptions
from memory.common import settings
from memory.common.celery_app import SEND_TIME_NOTIFY, START_TIME_RUN
from memory.common.db.connection import make_session
from memory.common.db.models import AgentDefinition, Subscription, APIKey, Event

UTC = timezone.utc
NOW = datetime(2026, 11, 7, 12, 30, tzinfo=UTC)
NOTIFY = {"notify": {"channel": "email", "target": "me@example.com", "message": "stand up", "subject": "Daily"}}


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


def cron_subscription(db, user, handler=NOTIFY, cron="0 * * * *", next_at=None, **fields):
    subscription = Subscription(
        user_id=user.id,
        name=f"cron-{db.query(Subscription).count()}",
        kind="cron",
        handler=handler,
        definition_id=handler.get("agent"),
        config={"cron": cron, "input": "go"},
        next_at=next_at,
        **fields,
    )
    db.add(subscription)
    db.commit()
    return subscription


def tick(db, subscription, fire_at, missed=0):
    event = Event(
        kind="tick", subscription_id=subscription.id, owner_id=subscription.user_id, fire_at=fire_at,
        data={"missed": missed},
    )
    db.add(event)
    db.commit()
    return event


def timer(db, user, fire_at, handler=NOTIFY, **data):
    event = Event(kind="timer", owner_id=user.id, fire_at=fire_at, handler=handler, data=data)
    db.add(event)
    db.commit()
    return event


def ticks(db, subscription):
    db.expire_all()
    rows = db.query(Event).filter(Event.subscription_id == subscription.id).order_by(Event.fire_at)
    return [(e.fire_at, (e.data or {}).get("missed"), (e.result or {}).get("status")) for e in rows]


@pytest.fixture
def sent(monkeypatch):
    messages = []
    monkeypatch.setattr(clock, "deliver_notification", lambda p: messages.append(p) or True)
    return messages


@pytest.fixture
def told(monkeypatch):
    notices = []
    monkeypatch.setattr(clock, "tell_owner", lambda db, owner, subject, message: notices.append(subject))
    return notices


@pytest.mark.parametrize(
    "cron, tz, after, expected",
    [
        ("0 9 * * *", "UTC", utc(2026, 2, 4, 10), utc(2026, 2, 5, 9)),
        ("0 9 * * *", "UTC", utc(2026, 2, 4, 8), utc(2026, 2, 4, 9)),
        ("0 9 * * *", "Asia/Kolkata", utc(2026, 2, 4, 8), utc(2026, 2, 5, 3, 30)),
        # New York springs forward on 2026-03-08: 09:00 local is 14:00 UTC before, 13:00 after
        ("0 9 * * *", "America/New_York", utc(2026, 3, 6, 15), utc(2026, 3, 7, 14)),
        ("0 9 * * *", "America/New_York", utc(2026, 3, 7, 15), utc(2026, 3, 8, 13)),
        # 02:30 doesn't exist that night, so it fires an hour later on the wall clock
        ("30 2 * * *", "America/New_York", utc(2026, 3, 8, 5), utc(2026, 3, 8, 7, 30)),
        # New York falls back on 2026-11-01: 09:00 local moves from 13:00 to 14:00 UTC
        ("0 9 * * *", "America/New_York", utc(2026, 10, 31, 14), utc(2026, 11, 1, 14)),
        # 01:30 happens twice that night; it fires on the first and not again on the second
        ("30 1 * * *", "America/New_York", utc(2026, 11, 1, 4), utc(2026, 11, 1, 5, 30)),
        ("30 1 * * *", "America/New_York", utc(2026, 11, 1, 5, 31), utc(2026, 11, 2, 6, 30)),
        ("0 9 * * *", "Europe/London", utc(2026, 10, 24, 9), utc(2026, 10, 25, 9)),
    ],
)
def test_next_cron_follows_the_wall_clock(cron, tz, after, expected):
    assert clock.next_cron(cron, tz, after) == expected


@pytest.mark.parametrize(
    "cron, error",
    [
        ("not a cron", "invalid cron expression"),
        ("0 9 * * * *", "only 5-field"),
        ("* * * * *", "cron interval too short"),
    ],
)
def test_check_cron_refuses(cron, error):
    with pytest.raises(ValueError, match=error):
        clock.check_cron(cron)


TIMES_OF_DAY = [(0, 0), (1, 0), (8, 0), (9, 3), (12, 30), (23, 30)]


@pytest.mark.parametrize("hour, minute", TIMES_OF_DAY)
@pytest.mark.parametrize("cron", ["0 9,17 * * *", "59 0,23 * * *", "0 9 * * 1,2", "30 4 1 * *"])
def test_check_cron_allows_whenever_it_runs(monkeypatch, hour, minute, cron):
    monkeypatch.setattr(clock, "utcnow", lambda: utc(2026, 11, 7, hour, minute))

    assert clock.check_cron(cron) == cron


@pytest.mark.parametrize("hour, minute", TIMES_OF_DAY)
# The short gaps sit at 09:00-09:05, across midnight, and on Mondays only.
@pytest.mark.parametrize("cron", ["0,5 9 * * *", "0,59 0,23 * * *", "0,5 9 * * 1"])
def test_check_cron_refuses_a_short_gap_whenever_it_runs(monkeypatch, hour, minute, cron):
    monkeypatch.setattr(clock, "utcnow", lambda: utc(2026, 11, 7, hour, minute))

    with pytest.raises(ValueError, match="cron interval too short"):
        clock.check_cron(cron)


def test_a_due_cron_writes_one_tick_for_its_latest_time_counting_the_missed(db_session, regular_user):
    subscription = cron_subscription(db_session, regular_user, next_at=utc(2026, 11, 7, 9))

    written = clock.write_ticks(db_session, NOW, clock.Notices())
    db_session.commit()

    assert (written, ticks(db_session, subscription)) == (1, [(utc(2026, 11, 7, 12), 3, None)])
    assert subscription.next_at == utc(2026, 11, 7, 13)


def test_an_unscheduled_cron_is_scheduled_without_ticking(db_session, regular_user):
    subscription = cron_subscription(db_session, regular_user)

    clock.write_ticks(db_session, NOW, clock.Notices())
    db_session.commit()

    assert (ticks(db_session, subscription), subscription.next_at) == ([], utc(2026, 11, 7, 13))


def test_a_cron_set_under_a_longer_minimum_still_ticks(db_session, regular_user):
    subscription = cron_subscription(db_session, regular_user, cron="*/5 * * * *", next_at=utc(2026, 11, 7, 12, 30))

    clock.write_ticks(db_session, NOW, clock.Notices())
    db_session.commit()

    assert (subscription.enabled, len(ticks(db_session, subscription))) == (True, 1)


def test_a_new_tick_coalesces_the_ones_still_waiting(db_session, regular_user):
    subscription = cron_subscription(db_session, regular_user, next_at=utc(2026, 11, 7, 12))
    tick(db_session, subscription, utc(2026, 11, 7, 10), missed=1)

    clock.write_ticks(db_session, NOW, clock.Notices())
    db_session.commit()

    assert ticks(db_session, subscription) == [
        (utc(2026, 11, 7, 10), 1, "skipped:coalesced"),
        (utc(2026, 11, 7, 12), 2, None),
    ]


def test_a_tick_already_handed_off_is_not_coalesced(db_session, regular_user):
    subscription = cron_subscription(db_session, regular_user, next_at=utc(2026, 11, 7, 12))
    in_flight = tick(db_session, subscription, utc(2026, 11, 7, 11))
    in_flight.dispatched_at, in_flight.attempts = utc(2026, 11, 7, 11, 1), 1
    db_session.commit()

    clock.write_ticks(db_session, NOW, clock.Notices())
    db_session.commit()

    assert ticks(db_session, subscription) == [
        (utc(2026, 11, 7, 11), 0, None),
        (utc(2026, 11, 7, 12), 0, None),
    ]


def test_a_tick_is_written_once_per_time(db_session, regular_user):
    subscription = cron_subscription(db_session, regular_user, next_at=utc(2026, 11, 7, 12))
    clock.write_ticks(db_session, NOW, clock.Notices())
    db_session.commit()
    subscription.next_at = utc(2026, 11, 7, 12)
    db_session.commit()

    clock.write_ticks(db_session, NOW, clock.Notices())
    db_session.commit()

    assert len(ticks(db_session, subscription)) == 1


def test_an_unparseable_cron_trips_its_subscription(db_session, regular_user):
    subscription = cron_subscription(db_session, regular_user, cron="not a cron", next_at=utc(2026, 11, 7, 9))
    notices = clock.Notices()

    clock.write_ticks(db_session, NOW, notices)
    db_session.commit()

    assert (subscription.enabled, "tripped" in subscription.state, len(notices.tripped)) == (False, True, 1)
    assert ticks(db_session, subscription) == []


def handoffs(no_celery_dispatch, task):
    return [c.kwargs["args"] for c in no_celery_dispatch.call_args_list if c.args[0] == task]


def test_run_clock_hands_a_due_notify_tick_to_its_task_which_sends_it(
    db_session, regular_user, sent, no_celery_dispatch
):
    now = datetime.now(UTC)
    subscription = cron_subscription(db_session, regular_user, next_at=now - timedelta(minutes=1))

    clock.run_clock()
    [[event_id]] = handoffs(no_celery_dispatch, SEND_TIME_NOTIFY)
    unsent = list(sent)
    status = clock.send_time_notify(event_id)
    again = clock.send_time_notify(event_id)

    assert (unsent, status, again) == ([], "sent", None)
    assert [(p.notification_channel, p.notification_target, p.message, p.topic) for p in sent] == [
        ("email", "me@example.com", "stand up", "Daily")
    ]
    assert [status for _, _, status in ticks(db_session, subscription)] == ["sent"]
    assert subscription.next_at > now


def test_a_hung_send_that_succeeds_late_keeps_the_failure_recorded_meanwhile(
    db_session, regular_user, monkeypatch
):
    event = timer(db_session, regular_user, datetime.now(UTC) - timedelta(seconds=1))

    def hangs_while_a_retry_gives_up(params):
        with make_session() as db:
            clock.finish(db.get_one(Event, event.id), "failed", error="interrupted")
            db.commit()
        return True

    monkeypatch.setattr(clock, "deliver_notification", hangs_while_a_retry_gives_up)

    assert clock.send_time_notify(event.id) is None
    db_session.refresh(event)
    assert event.result == {"status": "failed", "error": "interrupted"}


def test_a_crash_after_the_send_does_not_send_again(db_session, regular_user, sent, monkeypatch):
    event = timer(db_session, regular_user, datetime.now(UTC) - timedelta(seconds=1))

    def crash(*args):
        raise RuntimeError("worker died")

    with monkeypatch.context() as m:
        m.setattr(clock, "record_send", crash)
        with pytest.raises(RuntimeError):
            clock.send_time_notify(event.id)
    soon = clock.send_time_notify(event.id)
    db_session.refresh(event)
    event.result = event.result | {"at": (datetime.now(UTC) - clock.SEND_TIMEOUT).isoformat()}
    db_session.commit()
    later = clock.send_time_notify(event.id)

    db_session.refresh(event)
    assert (len(sent), soon, later, event.result["status"]) == (1, None, "failed", "failed")
    assert "not sent again" in event.result["error"]


def test_a_recurring_message_failing_three_times_running_trips(db_session, regular_user, monkeypatch, told):
    monkeypatch.setattr(clock, "deliver_notification", lambda p: False)
    subscription = cron_subscription(db_session, regular_user, next_at=datetime.now(UTC) + timedelta(hours=1))
    events = [tick(db_session, subscription, datetime.now(UTC) - timedelta(minutes=m)) for m in (3, 2, 1)]

    statuses = [clock.send_time_notify(e.id) for e in events]

    db_session.refresh(subscription)
    assert (statuses, subscription.enabled, subscription.state["failures"]) == (["failed"] * 3, False, 3)
    assert told == [f"Schedule paused: {subscription.name}"]


def test_a_send_resets_the_failure_count(db_session, regular_user, monkeypatch):
    subscription = cron_subscription(db_session, regular_user, next_at=datetime.now(UTC) + timedelta(hours=1))
    first, second = (tick(db_session, subscription, datetime.now(UTC) - timedelta(minutes=m)) for m in (2, 1))
    with monkeypatch.context() as m:
        m.setattr(clock, "deliver_notification", lambda p: False)
        clock.send_time_notify(first.id)
    monkeypatch.setattr(clock, "deliver_notification", lambda p: True)

    clock.send_time_notify(second.id)

    db_session.refresh(subscription)
    assert (subscription.enabled, "failures" in subscription.state) == (True, False)


def test_a_revoked_key_fails_the_tick_and_trips_the_subscription_once(db_session, regular_user, sent, told):
    key = APIKey.create(user_id=regular_user.id, name="k", scopes=["schedule"])
    key.revoked = True
    db_session.add(key)
    db_session.commit()
    subscription = cron_subscription(
        db_session, regular_user, api_key_id=key.id, via_api_key=True,
        next_at=datetime.now(UTC) + timedelta(hours=1),
    )
    first = tick(db_session, subscription, datetime.now(UTC) - timedelta(minutes=2))
    second = tick(db_session, subscription, datetime.now(UTC) - timedelta(minutes=1))

    clock.send_time_notify(first.id)
    clock.send_time_notify(second.id)

    db_session.refresh(subscription)
    db_session.refresh(first)
    db_session.refresh(second)
    assert (sent, subscription.enabled, "tripped" in subscription.state) == ([], False, True)
    assert first.result["status"] == "failed"
    assert second.result == {"status": "skipped:disabled"}
    assert told == [f"Schedule paused: {subscription.name}"]


def test_a_failed_timer_tells_its_owner(db_session, regular_user, monkeypatch, told):
    monkeypatch.setattr(clock, "deliver_notification", lambda p: False)
    event = timer(db_session, regular_user, datetime.now(UTC) - timedelta(seconds=1))

    clock.send_time_notify(event.id)

    db_session.refresh(event)
    assert (event.result["status"], told) == ("failed", ["Timer failed"])


def test_a_late_reminder_says_when_it_was_due(db_session, regular_user, sent):
    regular_user.timezone = "Asia/Kolkata"
    db_session.commit()
    event = timer(db_session, regular_user, datetime(2026, 11, 7, 3, 30, tzinfo=UTC))

    clock.send_time_notify(event.id)

    db_session.refresh(event)
    assert sent[0].message == "(Due 2026-11-07 09:00 IST)\n\nstand up"
    assert event.result["status"] == "sent" and event.result["late_sec"] > 0


def test_a_discord_target_that_is_no_id_fails_cleanly(db_session, regular_user, sent):
    key = APIKey.create(user_id=regular_user.id, name="k", scopes=["schedule"])
    db_session.add(key)
    db_session.commit()
    handler = {"notify": {"channel": "discord", "target": "general", "message": "hi"}}
    event = timer(db_session, regular_user, datetime.now(UTC), handler=handler, api_key_id=key.id, via_api_key=True)

    clock.send_time_notify(event.id)

    db_session.refresh(event)
    assert (sent, event.result) == ([], {"status": "failed", "error": "its Discord target is not a channel or user id"})


@pytest.mark.parametrize(
    "late, enabled, status",
    [
        (timedelta(hours=7), True, "skipped:late"),
        (timedelta(minutes=1), False, "skipped:disabled"),
    ],
)
def test_a_tick_that_should_not_run_is_skipped(
    db_session, regular_user, no_celery_dispatch, late, enabled, status
):
    subscription = cron_subscription(db_session, regular_user, enabled=enabled, next_at=datetime.now(UTC) + timedelta(hours=1))
    event = tick(db_session, subscription, datetime.now(UTC) - late)

    clock.run_clock()

    db_session.refresh(event)
    assert (event.result["status"], handoffs(no_celery_dispatch, SEND_TIME_NOTIFY)) == (status, [])


@pytest.mark.parametrize(
    "handler, task, status",
    [
        (NOTIFY, SEND_TIME_NOTIFY, None),
        ({"agent": 1}, None, "skipped:late"),
    ],
)
def test_only_a_late_agent_timer_is_skipped(db_session, regular_user, no_celery_dispatch, handler, task, status):
    event = timer(db_session, regular_user, datetime.now(UTC) - timedelta(hours=7), handler=handler)

    clock.run_clock()

    db_session.refresh(event)
    sends = [c.args[0] for c in no_celery_dispatch.call_args_list if c.args[0] in (SEND_TIME_NOTIFY, START_TIME_RUN)]
    assert (sends, (event.result or {}).get("status")) == ([task] if task else [], status)


def test_a_timer_is_handed_off_once_due_and_not_before(db_session, regular_user, no_celery_dispatch):
    due = timer(db_session, regular_user, datetime.now(UTC) - timedelta(seconds=1))
    later = timer(db_session, regular_user, datetime.now(UTC) + timedelta(hours=1))

    clock.run_clock()
    clock.run_clock()

    assert handoffs(no_celery_dispatch, SEND_TIME_NOTIFY) == [[due.id]]
    db_session.refresh(later)
    assert (later.dispatched_at, later.routed_at) == (None, None)


def test_an_agent_tick_is_sent_to_the_agents_queue_once_until_its_resend_is_due(
    db_session, regular_user, no_celery_dispatch
):
    definition = AgentDefinition(user_id=regular_user.id, name="d", spec={"system_prompt": "x"})
    db_session.add(definition)
    db_session.commit()
    subscription = cron_subscription(
        db_session, regular_user, handler={"agent": definition.id},
        next_at=datetime.now(UTC) + timedelta(hours=1),
    )
    event = tick(db_session, subscription, datetime.now(UTC) - timedelta(minutes=1))

    clock.run_clock()
    clock.run_clock()

    db_session.refresh(event)
    assert handoffs(no_celery_dispatch, START_TIME_RUN) == [[event.id]]
    assert (event.attempts, event.routed_at, event.dispatched_at is not None) == (1, None, True)


def test_a_handoff_is_resent_after_its_grace(db_session, regular_user, no_celery_dispatch):
    event = timer(db_session, regular_user, datetime.now(UTC) - timedelta(hours=1))
    event.attempts, event.dispatched_at = 1, datetime.now(UTC) - timedelta(minutes=3)
    db_session.commit()

    clock.run_clock()

    db_session.refresh(event)
    assert (event.attempts, handoffs(no_celery_dispatch, SEND_TIME_NOTIFY)) == (2, [[event.id]])


def test_a_second_clock_waits_for_the_first(db_session, db_engine, regular_user, no_celery_dispatch):
    subscription = cron_subscription(db_session, regular_user, next_at=datetime.now(UTC) - timedelta(minutes=1))
    event = timer(db_session, regular_user, datetime.now(UTC) - timedelta(seconds=1))
    with db_engine.connect() as other:
        other.execute(text("SELECT pg_advisory_lock(:key)"), {"key": clock.CLOCK_LOCK_KEY})
        result = clock.run_clock()
        other.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": clock.CLOCK_LOCK_KEY})

    db_session.refresh(event)
    assert (result, ticks(db_session, subscription), event.dispatched_at) == ({"ticks": 0, "delivered": 0}, [], None)


def test_the_item_router_leaves_ticks_and_timers_to_the_clock(db_session, regular_user):
    event = timer(db_session, regular_user, datetime.now(UTC) - timedelta(minutes=1))

    subscriptions.route_events()

    db_session.refresh(event)
    assert event.routed_at is None


def test_add_timer_keeps_to_the_per_user_cap(db_session, regular_user, monkeypatch):
    monkeypatch.setattr(settings, "MAX_TIMERS_PER_USER", 1)
    at = datetime.now(UTC) + timedelta(hours=1)
    clock.add_timer(db_session, regular_user.id, at, NOTIFY, None)

    with pytest.raises(ValueError, match="MAX_TIMERS_PER_USER"):
        clock.add_timer(db_session, regular_user.id, at, NOTIFY, None)


def test_reschedule_user_moves_crons_onto_the_new_timezone(db_session, regular_user):
    subscription = cron_subscription(db_session, regular_user, cron="0 9 * * *", next_at=utc(2999, 1, 1, 9))
    paused = cron_subscription(db_session, regular_user, cron="0 9 * * *", enabled=False, next_at=None)

    clock.reschedule_user(db_session, regular_user.id, "Asia/Kolkata")

    assert (subscription.next_at.astimezone(UTC).hour, subscription.next_at.astimezone(UTC).minute) == (3, 30)
    assert paused.next_at is None
