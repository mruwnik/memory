"""Tests for transcript provider sync tasks."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from celery.schedules import crontab

from memory.common.celery_app import build_beat_schedule
from memory.common.db.models import Person, TranscriptAccount
from memory.common.db.models.source_items import Meeting
from memory.common.content_processing import create_content_hash
from memory.parsers.fireflies import FirefliesError
from memory.workers.tasks import transcripts
from memory.workers.tasks.meetings import link_attendees


SAMPLE_FULL_TRANSCRIPT = {
    "id": "01TESTNEW0000000000000",
    "title": "Test Meeting",
    "dateString": "2026-04-29T15:15:00.000Z",
    "duration": 30.0,
    "cal_id": "evt-1",
    "meeting_attendees": [
        {"email": "daniel@equistamp.com"},
        {"email": "chris@equistamp.com"},
    ],
    "speakers": [{"id": 0, "name": "Daniel"}],
    "sentences": [
        {"speaker_name": "Daniel", "text": "Hello world."},
    ],
    "summary": {"overview": "test", "action_items": "", "keywords": []},
}


def _build_full_transcript(meeting_id: str, date_string: str) -> dict:
    return {
        **SAMPLE_FULL_TRANSCRIPT,
        "id": meeting_id,
        "dateString": date_string,
    }


@pytest.fixture
def patch_session(db_session):
    """Replace make_session in the transcripts task with the test db_session."""

    @contextmanager
    def _session():
        yield db_session

    with patch("memory.workers.tasks.transcripts.make_session", _session):
        yield db_session


@pytest.fixture
def transcript_account(db_session, regular_user):
    account = TranscriptAccount(
        user_id=regular_user.id,
        name="Test Fireflies",
        provider="fireflies",
    )
    account.api_key = "test-key"
    db_session.add(account)
    db_session.commit()
    return account


@pytest.fixture
def patch_send_task():
    """Capture process_meeting dispatches as a list of kwargs."""
    sent: list[dict] = []
    fake_result = MagicMock(id="task-1")
    with patch.object(transcripts.app, "send_task") as send:
        send.side_effect = lambda name, kwargs=None: (
            sent.append({"name": name, "kwargs": kwargs}) or fake_result
        )
        yield sent


def _add_meeting(db_session, account_id: int, when: datetime, external: str) -> Meeting:
    meeting = Meeting(
        title=f"Existing {external}",
        content=f"text-{external}",
        sha256=create_content_hash(f"meeting:{external}"),
        modality="meeting",
        mime_type="text/plain",
        size=100,
        meeting_date=when,
        external_id=external,
        transcript_account_id=account_id,
    )
    db_session.add(meeting)
    db_session.commit()
    return meeting


# ---------------------------------------------------------------------------
# dispatch_transcript
# ---------------------------------------------------------------------------


def test_dispatch_transcript_skips_empty_text(transcript_account, patch_send_task):
    transcript = {**SAMPLE_FULL_TRANSCRIPT, "sentences": []}
    task_id = transcripts.dispatch_transcript(transcript, transcript_account)
    assert task_id is None
    assert patch_send_task == []


def test_dispatch_transcript_passes_account_id_and_tags(
    transcript_account, patch_send_task, db_session
):
    transcript_account.tags = ["team-equistamp"]
    db_session.commit()

    task_id = transcripts.dispatch_transcript(
        SAMPLE_FULL_TRANSCRIPT, transcript_account
    )
    assert task_id == "task-1"
    assert len(patch_send_task) == 1
    kwargs = patch_send_task[0]["kwargs"]
    assert kwargs["transcript_account_id"] == transcript_account.id
    assert kwargs["tags"] == ["team-equistamp"]
    assert kwargs["source_tool"] == "fireflies"
    assert kwargs["external_id"] == "01TESTNEW0000000000000"
    assert kwargs["calendar_event_uid"] == "evt-1"
    assert kwargs["attendee_emails"] == [
        "daniel@equistamp.com",
        "chris@equistamp.com",
    ]


# ---------------------------------------------------------------------------
# walk_window (the single fetch primitive, paginated up to max_pages)
# ---------------------------------------------------------------------------


def test_walk_window_returns_oldest_first_across_pages():
    """Pagination: each page is newest-first, page 0 is newest. Final list
    must be oldest-first overall."""
    page_size = transcripts.DEFAULT_PAGE_LIMIT
    # page0 is newest-first within itself: page0[0] is the newest item of all.
    page0 = [{"id": f"new-{i}"} for i in range(page_size)]
    # page1 is the next page (skip=50); page1[2] is the very oldest item.
    page1 = [{"id": f"old-{i}"} for i in range(3)]

    fake_client = MagicMock()
    fake_client.list_transcripts.side_effect = [page0, page1]

    result = transcripts.walk_window(
        fake_client, datetime(2026, 1, 1, tzinfo=timezone.utc), max_pages=10
    )
    # Oldest-first overall: oldest = page1[2], newest = page0[0].
    assert [r["id"] for r in result[:3]] == ["old-2", "old-1", "old-0"]
    assert [r["id"] for r in result[-3:]] == ["new-2", "new-1", "new-0"]
    assert len(result) == page_size + 3


def test_walk_window_respects_max_pages():
    page_size = transcripts.DEFAULT_PAGE_LIMIT
    fake_client = MagicMock()
    fake_client.list_transcripts.side_effect = lambda **kw: [
        {"id": f"x-{kw['skip']}-{i}"} for i in range(page_size)
    ]
    transcripts.walk_window(
        fake_client, datetime(2026, 1, 1, tzinfo=timezone.utc), max_pages=3
    )
    assert fake_client.list_transcripts.call_count == 3


# ---------------------------------------------------------------------------
# fireflies_walk (the dispatcher that combines fetch + filter + dispatch)
# ---------------------------------------------------------------------------


def test_fireflies_walk_full_path_paginates(
    db_session, transcript_account, patch_send_task
):
    """The walk paginates until a partial page."""
    page_size = transcripts.DEFAULT_PAGE_LIMIT
    pages = [
        [
            {"id": f"P1-{i:02d}", "dateString": "2026-04-15T00:00:00.000Z"}
            for i in range(page_size)
        ],
        [
            {"id": f"P2-{i:02d}", "dateString": "2026-04-15T00:00:00.000Z"}
            for i in range(5)
        ],
    ]
    fake_client = MagicMock()
    fake_client.list_transcripts.side_effect = lambda **kw: pages.pop(0) if pages else []
    fake_client.get_transcript.side_effect = lambda mid: _build_full_transcript(
        mid, "2026-04-15T00:00:00.000Z"
    )

    with patch(
        "memory.workers.tasks.transcripts.FirefliesClient", return_value=fake_client
    ):
        count = transcripts.fireflies_walk(
            transcript_account,
            db_session,
            floor=datetime(2026, 4, 1, tzinfo=timezone.utc),
        )

    assert count == page_size + 5
    assert fake_client.list_transcripts.call_count == 2


def test_fireflies_walk_skips_known_external_ids(
    db_session, transcript_account, patch_send_task
):
    """The prefilter must avoid get_transcript calls for known IDs."""
    existing_date = datetime(2026, 4, 1, tzinfo=timezone.utc)
    _add_meeting(db_session, transcript_account.id, existing_date, "01KNOWN")

    fake_client = MagicMock()
    fake_client.list_transcripts.return_value = [
        {"id": "01KNOWN", "dateString": "2026-04-01T00:00:00.000Z"},
        {"id": "01NEW", "dateString": "2026-04-02T00:00:00.000Z"},
    ]
    fake_client.get_transcript.side_effect = lambda mid: _build_full_transcript(
        mid, "2026-04-02T00:00:00.000Z"
    )

    with patch(
        "memory.workers.tasks.transcripts.FirefliesClient", return_value=fake_client
    ):
        count = transcripts.fireflies_walk(
            transcript_account,
            db_session,
            floor=datetime(2026, 3, 1, tzinfo=timezone.utc),
        )

    assert count == 1
    fake_client.get_transcript.assert_called_once_with("01NEW")


@pytest.mark.parametrize(
    "stub, fetched",
    [
        ({"cal_id": "evt-1", "title": "Other title"}, []),
        ({"title": "Existing 01KNOWN", "participants": ["Ann@example.com,bo@example.com"]}, []),
        ({"title": "Existing 01KNOWN", "participants": ["bo@example.com"]}, ["01DUP"]),
        # The owner is in every call they record.
        ({"title": "Existing 01KNOWN", "participants": ["regular@example.com", "bo@example.com"]}, ["01DUP"]),
        # Restarted after the stored recording ended.
        ({"cal_id": "evt-1", "dateString": "2026-04-01T17:01:00.000Z"}, ["01DUP"]),
    ],
)
def test_fireflies_walk_skips_second_recordings_of_stored_calls(
    db_session, transcript_account, patch_send_task, stub, fetched
):
    stored = _add_meeting(db_session, transcript_account.id, datetime(2026, 4, 1, 16, 30, tzinfo=timezone.utc), "01KNOWN")
    stored.source_tool = "fireflies"
    stored.calendar_event_uid = "evt-1"
    stored.duration_minutes = 30
    stored.people = [
        Person(identifier="ann", display_name="Ann", contact_info={"email": "ann@example.com"}),
        Person(identifier="reg", display_name="Reg", contact_info={"email": "regular@example.com"}),
    ]
    db_session.commit()

    fake_client = MagicMock()
    fake_client.list_transcripts.return_value = [
        {"id": "01DUP", "dateString": "2026-04-01T16:30:14.000Z", "duration": 20.0, **stub},
    ]
    fake_client.get_transcript.side_effect = lambda mid: _build_full_transcript(mid, "2026-04-01T16:30:14.000Z")

    with patch("memory.workers.tasks.transcripts.FirefliesClient", return_value=fake_client):
        transcripts.fireflies_walk(transcript_account, db_session, floor=datetime(2026, 3, 1, tzinfo=timezone.utc))

    assert [c.args[0] for c in fake_client.get_transcript.call_args_list] == fetched


def test_known_external_ids_is_global(
    db_session, transcript_account, regular_user
):
    """Pre-filter is intentionally *global* (not per-account) to match the
    global partial unique index on ``meeting.external_id`` and
    ``process_meeting``'s global idempotency check. If two accounts share an
    upstream transcript, the second account's prefilter must see the first's
    Meeting row — otherwise it would call get_transcript only to have
    process_meeting short-circuit on the global unique constraint."""
    other_account = TranscriptAccount(
        user_id=regular_user.id,
        name="Other",
        provider="fireflies",
    )
    other_account.api_key = "k"
    db_session.add(other_account)
    db_session.commit()

    _add_meeting(
        db_session,
        other_account.id,
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        "01SHARED",
    )

    # Either account asking about "01SHARED" sees it as known — global
    # scoping means the prefilter agrees with the unique index.
    assert transcripts.known_external_ids(db_session, ["01SHARED"]) == {"01SHARED"}


# ---------------------------------------------------------------------------
# Task wrappers (sync_transcript_account / rescan_transcript_account)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "task, lookback_days",
    [
        ("sync_transcript_account", transcripts.SYNC_LOOKBACK_DAYS),
        ("rescan_transcript_account", 30),
    ],
)
def test_walk_tasks_walk_back_their_lookback(
    db_session, transcript_account, patch_session, patch_send_task, monkeypatch, task, lookback_days
):
    monkeypatch.setattr(transcripts.settings, "TRANSCRIPTS_RESCAN_LOOKBACK_DAYS", 30)
    fake_client = MagicMock()
    fake_client.list_transcripts.return_value = []
    with patch(
        "memory.workers.tasks.transcripts.FirefliesClient", return_value=fake_client
    ):
        result = getattr(transcripts, task)(transcript_account.id)

    assert result["status"] == "completed"
    floor = fake_client.list_transcripts.call_args.kwargs["from_date"]
    expected = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    assert abs(floor - expected) < timedelta(minutes=1)


def test_sync_transcript_account_skips_inactive(
    db_session, transcript_account, patch_session
):
    transcript_account.active = False
    db_session.commit()

    result = transcripts.sync_transcript_account(transcript_account.id)
    assert result["status"] == "error"


def test_sync_transcript_account_unknown_provider(
    db_session, transcript_account, patch_session
):
    transcript_account.provider = "fireflies"
    db_session.commit()
    with patch.dict(transcripts.PROVIDERS, {}, clear=True):
        result = transcripts.sync_transcript_account(transcript_account.id)
    assert result["status"] == "error"
    assert "unsupported provider" in result["error"]
    db_session.refresh(transcript_account)
    assert "unsupported provider" in (transcript_account.sync_error or "")


def test_sync_transcript_account_records_fireflies_error(
    db_session, transcript_account, patch_session, patch_send_task
):
    fake_client = MagicMock()
    fake_client.list_transcripts.side_effect = FirefliesError("boom")
    with patch(
        "memory.workers.tasks.transcripts.FirefliesClient", return_value=fake_client
    ):
        result = transcripts.sync_transcript_account(transcript_account.id)
    assert result["status"] == "error"
    assert "boom" in result["error"]
    db_session.refresh(transcript_account)
    assert "boom" in (transcript_account.sync_error or "")


@pytest.mark.parametrize(
    "error", [FirefliesError("transient blip", retryable=True), RuntimeError("transient blip")]
)
def test_sync_transcript_account_propagates_unhandled_errors(
    error, db_session, transcript_account, patch_session, patch_send_task
):
    """Retryable and unexpected errors must propagate so Celery's autoretry_for
    catches them; sync_error is recorded in either case."""
    fake_client = MagicMock()
    fake_client.list_transcripts.side_effect = error
    with patch(
        "memory.workers.tasks.transcripts.FirefliesClient", return_value=fake_client
    ):
        with pytest.raises(type(error)):
            transcripts.sync_transcript_account(transcript_account.id)
    db_session.refresh(transcript_account)
    assert "transient blip" in (transcript_account.sync_error or "")


@pytest.fixture
def unpolled_accounts(db_session, regular_user):
    inactive = TranscriptAccount(
        user_id=regular_user.id, name="Inactive", provider="fireflies", active=False
    )
    inactive.api_key = "x"
    push_only = TranscriptAccount(
        user_id=regular_user.id, name="Push only", provider="generic"
    )
    db_session.add_all([inactive, push_only])
    db_session.commit()
    return [inactive, push_only]


def test_daily_sync_is_on_the_clock():
    """An interval entry first fires one interval after beat starts, and beat's
    schedule file doesn't survive a container recreate, so a daily interval
    would slip with every deploy."""
    entry = build_beat_schedule()["sync-all-transcript-accounts"]
    assert isinstance(entry["schedule"], crontab)


def test_fan_out_dispatches_only_active_polling_accounts(
    db_session, transcript_account, unpolled_accounts, patch_session
):
    with patch.object(
        transcripts.sync_transcript_account, "delay", return_value=MagicMock(id="t-1")
    ) as delay:
        result = transcripts.sync_all_transcript_accounts()

    assert [r["account_id"] for r in result] == [transcript_account.id]
    delay.assert_called_once_with(transcript_account.id)


# ---------------------------------------------------------------------------
# ingest_transcript_meeting (push notification -> one-meeting fetch)
# ---------------------------------------------------------------------------


def test_ingest_transcript_meeting_fetches_and_dispatches(
    transcript_account, patch_session, patch_send_task
):
    fake_client = MagicMock()
    fake_client.get_transcript.return_value = SAMPLE_FULL_TRANSCRIPT
    with patch(
        "memory.workers.tasks.transcripts.FirefliesClient", return_value=fake_client
    ):
        result = transcripts.ingest_transcript_meeting(
            transcript_account.id, SAMPLE_FULL_TRANSCRIPT["id"]
        )

    assert result["status"] == "queued"
    fake_client.get_transcript.assert_called_once_with(SAMPLE_FULL_TRANSCRIPT["id"])
    assert [c["kwargs"]["external_id"] for c in patch_send_task] == [
        SAMPLE_FULL_TRANSCRIPT["id"]
    ]


def test_ingest_transcript_meeting_skips_known_meeting(
    db_session, transcript_account, patch_session, patch_send_task
):
    _add_meeting(db_session, transcript_account.id, datetime.now(timezone.utc), "KNOWN")
    fake_client = MagicMock()
    with patch(
        "memory.workers.tasks.transcripts.FirefliesClient", return_value=fake_client
    ):
        result = transcripts.ingest_transcript_meeting(transcript_account.id, "KNOWN")

    assert result["status"] == "exists"
    fake_client.get_transcript.assert_not_called()
    assert patch_send_task == []


def test_ingest_transcript_meeting_refetches_reprocessed_meeting(
    db_session, transcript_account, patch_session, patch_send_task
):
    _add_meeting(db_session, transcript_account.id, datetime.now(timezone.utc), "KNOWN")
    fake_client = MagicMock()
    fake_client.get_transcript.return_value = {**SAMPLE_FULL_TRANSCRIPT, "id": "KNOWN"}
    with patch(
        "memory.workers.tasks.transcripts.FirefliesClient", return_value=fake_client
    ):
        result = transcripts.ingest_transcript_meeting(
            transcript_account.id, "KNOWN", reprocessed=True
        )

    assert result["status"] == "queued"
    assert [(c["kwargs"]["external_id"], c["kwargs"]["replace"]) for c in patch_send_task] == [
        ("KNOWN", True)
    ]


def test_ingest_transcript_meeting_clears_earlier_push_error(
    db_session, transcript_account, patch_session, patch_send_task
):
    transcript_account.record_sync("bad key")
    db_session.commit()
    fake_client = MagicMock()
    fake_client.get_transcript.return_value = SAMPLE_FULL_TRANSCRIPT
    with patch(
        "memory.workers.tasks.transcripts.FirefliesClient", return_value=fake_client
    ):
        transcripts.ingest_transcript_meeting(transcript_account.id, SAMPLE_FULL_TRANSCRIPT["id"])

    db_session.refresh(transcript_account)
    assert transcript_account.sync_error is None
    assert transcript_account.sync_failing_since is None


@pytest.mark.parametrize("field, value", [("active", False), ("provider", "generic")])
def test_ingest_transcript_meeting_ignores_unusable_account(
    db_session, transcript_account, patch_session, patch_send_task, field, value
):
    setattr(transcript_account, field, value)
    db_session.commit()
    fake_client = MagicMock()
    with patch(
        "memory.workers.tasks.transcripts.FirefliesClient", return_value=fake_client
    ):
        result = transcripts.ingest_transcript_meeting(transcript_account.id, "M1")

    assert result["status"] == "error"
    fake_client.get_transcript.assert_not_called()


def failing_fireflies(retryable: bool):
    fake_client = MagicMock()
    fake_client.get_transcript.side_effect = FirefliesError("bad key", retryable=retryable)
    return patch(
        "memory.workers.tasks.transcripts.FirefliesClient", return_value=fake_client
    )


def test_ingest_transcript_meeting_records_persistent_error_without_retry(
    db_session, transcript_account, patch_session, patch_send_task
):
    with failing_fireflies(retryable=False):
        result = transcripts.ingest_transcript_meeting(transcript_account.id, "M1")

    assert result == {"status": "error", "error": "bad key"}
    db_session.refresh(transcript_account)
    assert transcript_account.sync_error == "bad key"


def test_ingest_transcript_meeting_records_and_raises_transient_error(
    db_session, transcript_account, patch_session, patch_send_task
):
    with failing_fireflies(retryable=True), pytest.raises(FirefliesError):
        transcripts.ingest_transcript_meeting(transcript_account.id, "M1")

    db_session.refresh(transcript_account)
    assert transcript_account.sync_error == "bad key"


# ---------------------------------------------------------------------------
# Email-based attendee linking (covers the changes in meetings.py)
# ---------------------------------------------------------------------------


def test_link_attendees_via_email_creates_person(db_session):
    """The email-based linking path creates a Person from a clean email."""

    meeting = Meeting(
        title="Email Linking",
        content="x",
        sha256=create_content_hash("meeting:email-link"),
        modality="meeting",
        mime_type="text/plain",
        size=10,
    )
    db_session.add(meeting)
    db_session.commit()

    result = link_attendees(
        db_session,
        meeting,
        attendee_emails=["new@example.com"],
        create_missing=True,
    )
    assert result["created"] == 1
    person = (
        db_session.query(Person)
        .filter(Person.contact_info["email"].astext == "new@example.com")
        .first()
    )
    assert person is not None


def test_link_attendees_via_email_links_existing_person(db_session):
    """If a Person already has the email, no new Person is created."""

    person = Person(
        identifier="alice_existing",
        display_name="Alice",
        contact_info={"email": "alice@example.com"},
    )
    meeting = Meeting(
        title="Email Linking 2",
        content="y",
        sha256=create_content_hash("meeting:email-link-2"),
        modality="meeting",
        mime_type="text/plain",
        size=10,
    )
    db_session.add_all([person, meeting])
    db_session.commit()

    result = link_attendees(
        db_session,
        meeting,
        attendee_emails=["alice@example.com"],
        create_missing=True,
    )
    assert result["linked"] == 1
    assert result["created"] == 0
    assert person in meeting.attendees


def test_link_attendees_paired_name_and_email_create_one_person(db_session):
    """Regression: name+email for the same attendee must produce exactly one
    Person, not one per code path."""

    meeting = Meeting(
        title="Paired",
        content="z",
        sha256=create_content_hash("meeting:paired"),
        modality="meeting",
        mime_type="text/plain",
        size=10,
    )
    db_session.add(meeting)
    db_session.commit()

    result = link_attendees(
        db_session,
        meeting,
        attendee_emails=["a.l.smith@example.com"],
        attendee_names=["Alice Smith"],
        create_missing=True,
    )
    assert result["created"] == 1
    assert result["linked"] == 0
    assert len(meeting.attendees) == 1
    p = meeting.attendees[0]
    assert p.contact_info.get("email") == "a.l.smith@example.com"
    assert p.display_name == "Alice Smith"


@pytest.mark.parametrize("ann_name, expected_name", [("Ann", "Ann"), ("", "Ann Lee")])
def test_link_attendees_pairs_are_never_mixed_with_names(db_session, ann_name, expected_name):
    """Explicit pairs keep one person's email and name together even when
    the email-less names list happens to be the same length."""

    meeting = Meeting(
        title="Pairs",
        content="p",
        sha256=create_content_hash(f"meeting:pairs:{ann_name}"),
        modality="meeting",
        mime_type="text/plain",
        size=10,
    )
    db_session.add(meeting)
    db_session.commit()

    result = link_attendees(
        db_session,
        meeting,
        attendee_names=["Bob"],
        attendee_pairs=[("ann.lee@example.com", ann_name)],
        create_missing=True,
    )

    assert result["created"] == 2
    people = {p.display_name: p.contact_info or {} for p in meeting.attendees}
    assert people == {expected_name: {"email": "ann.lee@example.com"}, "Bob": {}}


def test_link_attendees_dedupes_repeated_emails(db_session):
    """A noisy upstream that lists the same email twice (different cases)
    should still produce a single Person and a single link."""

    meeting = Meeting(
        title="Dedup",
        content="zz",
        sha256=create_content_hash("meeting:dedup"),
        modality="meeting",
        mime_type="text/plain",
        size=10,
    )
    db_session.add(meeting)
    db_session.commit()

    result = link_attendees(
        db_session,
        meeting,
        attendee_emails=["Alice@example.com", "alice@example.com"],
        create_missing=True,
    )
    assert result["created"] == 1
    assert result["linked"] == 0
    assert len(meeting.attendees) == 1
