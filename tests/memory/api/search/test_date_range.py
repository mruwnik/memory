from datetime import date, datetime, timezone

import pytest

from memory.api.search.date_range import date_bounds, source_ids_in_range
from memory.common.access_control import AccessFilter
from memory.common.db.models import (
    CalendarEvent,
    MailMessage,
    Meeting,
    Note,
    SlackChannel,
    SlackMessage,
    SlackWorkspace,
    Task,
)

UTC = timezone.utc
LONG_AGO = datetime(2020, 1, 1, tzinfo=UTC)


def at(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 10, day, hour, tzinfo=UTC)


@pytest.mark.parametrize(
    "start, end, expected",
    [
        (date(2026, 10, 21), date(2026, 10, 21), (at(20, 12), at(22, 12))),
        (date(2026, 10, 19), None, (at(18, 12), None)),
        (None, date(2026, 10, 22), (None, at(23, 12))),
        (None, None, (None, None)),
    ],
)
def test_date_bounds_cover_whole_days_with_timezone_slack(start, end, expected):
    assert date_bounds(start, end) == expected


@pytest.fixture
def items(db_session):
    workspace = SlackWorkspace(id="T1", name="WS")
    channel = SlackChannel(id="C1", workspace_id="T1", name="general", channel_type="channel")
    db_session.add_all([workspace, channel])
    db_session.flush()

    def sha(name: str) -> bytes:
        return name.encode().ljust(32, b"\0")

    # Every typed item was ingested long after (or before) its event time, so a
    # match proves the event column is used rather than inserted_at.
    rows = {
        "mail_in": MailMessage(sha256=sha("mail_in"), modality="test", sent_at=at(20), inserted_at=LONG_AGO),
        "mail_out": MailMessage(sha256=sha("mail_out"), modality="test", sent_at=at(1), inserted_at=at(20)),
        "slack_in": SlackMessage(
            sha256=sha("slack_in"), modality="message", message_ts=f"{at(20).timestamp()}",
            channel_id="C1", workspace_id="T1", inserted_at=LONG_AGO,
        ),
        "slack_out": SlackMessage(
            sha256=sha("slack_out"), modality="message", message_ts=f"{at(1).timestamp()}",
            channel_id="C1", workspace_id="T1", inserted_at=at(20),
        ),
        "event_future": CalendarEvent(
            sha256=sha("event_future"), event_title="sync", start_time=at(28), inserted_at=LONG_AGO,
        ),
        "meeting_in": Meeting(sha256=sha("meeting_in"), modality="test", meeting_date=at(20), inserted_at=LONG_AGO),
        "task_due_in": Task(sha256=sha("task_due_in"), modality="test", task_title="t", due_date=at(20), inserted_at=at(25)),
        "task_created_in": Task(sha256=sha("task_created_in"), modality="test", task_title="t", inserted_at=at(20)),
        "task_out": Task(sha256=sha("task_out"), modality="test", task_title="t", due_date=at(1), inserted_at=at(1)),
        "note_in": Note(sha256=sha("note_in"), modality="test", inserted_at=at(20)),
        "note_out": Note(sha256=sha("note_out"), modality="test", inserted_at=at(1)),
    }
    db_session.add_all(rows.values())
    db_session.commit()
    return {name: item.id for name, item in rows.items()}


@pytest.mark.parametrize(
    "start, end, expected",
    [
        (
            at(19, 0),
            at(21, 0),
            {"mail_in", "slack_in", "meeting_in", "task_due_in", "task_created_in", "note_in"},
        ),
        (at(27, 0), None, {"event_future"}),
        (None, at(2, 0), {"mail_out", "slack_out", "task_out", "note_out"}),
    ],
)
def test_source_ids_in_range_uses_each_types_event_time(db_session, items, start, end, expected):
    found = set(source_ids_in_range(db_session, start, end, None))

    assert found == {items[name] for name in expected}


def test_source_ids_in_range_requires_a_bound(db_session):
    with pytest.raises(ValueError):
        source_ids_in_range(db_session, None, None, None)


def test_source_ids_in_range_bounds_are_inclusive(db_session, items):
    found = source_ids_in_range(db_session, at(20), at(20), None)

    assert items["mail_in"] in found


def test_source_ids_in_range_applies_access_filter(db_session, items):
    db_session.get(Note, items["note_in"]).sensitivity = "public"
    db_session.commit()
    public_only = AccessFilter(conditions=[], include_public=True)

    found = source_ids_in_range(db_session, at(19, 0), at(21, 0), public_only)

    assert found == [items["note_in"]]
