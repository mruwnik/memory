"""Tests for transcript provider sync tasks."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from memory.common.db.models import Person, TranscriptAccount
from memory.common.db.models.source_items import Meeting
from memory.common.content_processing import create_content_hash
from memory.parsers.fireflies import FirefliesError
from memory.workers.tasks import transcripts


SAMPLE_FULL_TRANSCRIPT = {
    "id": "01TESTNEW0000000000000",
    "title": "Test Meeting",
    "dateString": "2026-04-29T15:15:00.000Z",
    "duration": 30.0,
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
# meeting_min_date
# ---------------------------------------------------------------------------


def test_meeting_min_date_empty(db_session, transcript_account):
    assert transcripts.meeting_min_date(db_session, transcript_account.id) is None


def test_meeting_min_date_with_meetings(db_session, transcript_account):
    older = datetime(2026, 1, 1, tzinfo=timezone.utc)
    newer = datetime(2026, 4, 1, tzinfo=timezone.utc)
    _add_meeting(db_session, transcript_account.id, older, "old")
    _add_meeting(db_session, transcript_account.id, newer, "new")

    assert (
        transcripts.meeting_min_date(db_session, transcript_account.id) == older
    )


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


def test_fireflies_walk_quick_path_single_page(
    db_session, transcript_account, patch_send_task
):
    """max_pages=1 → single API page, single skip=0 call."""
    fake_client = MagicMock()
    fake_client.list_transcripts.return_value = [
        {"id": "01ABC", "dateString": "2026-04-15T00:00:00.000Z"}
    ]
    fake_client.get_transcript.return_value = _build_full_transcript(
        "01ABC", "2026-04-15T00:00:00.000Z"
    )

    with patch(
        "memory.workers.tasks.transcripts.FirefliesClient", return_value=fake_client
    ):
        count = transcripts.fireflies_walk(
            transcript_account,
            db_session,
            floor=datetime(2026, 4, 1, tzinfo=timezone.utc),
            max_pages=1,
        )

    assert count == 1
    assert fake_client.list_transcripts.call_count == 1
    _, kwargs = fake_client.list_transcripts.call_args
    assert kwargs.get("skip", 0) == 0


def test_fireflies_walk_full_path_paginates(
    db_session, transcript_account, patch_send_task
):
    """max_pages>1 → walk_window paginates until partial page or cap."""
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
            max_pages=10,
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
            max_pages=1,
        )

    assert count == 1
    fake_client.get_transcript.assert_called_once_with("01NEW")


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
# quick_floor / full_floor (pure floor-computing helpers)
# ---------------------------------------------------------------------------


def test_quick_floor_uses_min_date_when_recent(db_session, transcript_account):
    """If min_date is more recent than the bootstrap window, it wins —
    quick sync doesn't re-fetch data we already have."""
    recent = datetime.now(timezone.utc) - timedelta(days=2)
    _add_meeting(db_session, transcript_account.id, recent, "recent")
    assert transcripts.quick_floor(transcript_account, db_session) == recent


def test_quick_floor_uses_bootstrap_when_db_empty(db_session, transcript_account):
    """Empty DB → bootstrap (now - QUICK_BOOTSTRAP_LOOKBACK_DAYS)."""
    before = datetime.now(timezone.utc)
    floor = transcripts.quick_floor(transcript_account, db_session)
    after = datetime.now(timezone.utc)
    expected_low = before - timedelta(days=transcripts.QUICK_BOOTSTRAP_LOOKBACK_DAYS)
    expected_high = after - timedelta(days=transcripts.QUICK_BOOTSTRAP_LOOKBACK_DAYS)
    assert expected_low <= floor <= expected_high


def test_quick_floor_uses_bootstrap_when_min_date_is_old(
    db_session, transcript_account
):
    """If min_date is ancient (years ago), the bootstrap floor (more recent)
    wins — don't waste calls walking through all of history every quick poll."""
    very_old = datetime.now(timezone.utc) - timedelta(days=400)
    _add_meeting(db_session, transcript_account.id, very_old, "very-old")
    floor = transcripts.quick_floor(transcript_account, db_session)
    expected_min = datetime.now(timezone.utc) - timedelta(
        days=transcripts.QUICK_BOOTSTRAP_LOOKBACK_DAYS + 1
    )
    assert floor > expected_min


def test_full_floor_uses_lookback_setting(
    db_session, transcript_account, monkeypatch
):
    """Full floor = now - TRANSCRIPTS_RESCAN_LOOKBACK_DAYS, regardless of
    min_date."""
    recent = datetime.now(timezone.utc) - timedelta(days=2)
    _add_meeting(db_session, transcript_account.id, recent, "recent")
    monkeypatch.setattr(
        transcripts.settings, "TRANSCRIPTS_RESCAN_LOOKBACK_DAYS", 30
    )

    floor = transcripts.full_floor(transcript_account, db_session)
    expected_min = datetime.now(timezone.utc) - timedelta(days=31)
    expected_max = datetime.now(timezone.utc) - timedelta(days=29)
    assert expected_min <= floor <= expected_max


def test_quick_sync_is_single_page(
    db_session, transcript_account, patch_session, patch_send_task
):
    """Quick sync (sync_transcript_account) uses max_pages=1 — single API
    call, no skip pagination."""
    page_size = transcripts.DEFAULT_PAGE_LIMIT
    fake_client = MagicMock()
    fake_client.list_transcripts.return_value = [
        {"id": f"01-{i}", "dateString": "2026-04-15T00:00:00.000Z"}
        for i in range(page_size)
    ]
    fake_client.get_transcript.side_effect = lambda mid: _build_full_transcript(
        mid, "2026-04-15T00:00:00.000Z"
    )

    with patch(
        "memory.workers.tasks.transcripts.FirefliesClient", return_value=fake_client
    ):
        transcripts.sync_transcript_account(transcript_account.id)

    assert fake_client.list_transcripts.call_count == 1


# ---------------------------------------------------------------------------
# Task wrappers (sync_transcript_account / rescan_transcript_account)
# ---------------------------------------------------------------------------


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


def test_sync_transcript_account_retries_on_transient_fireflies_error(
    db_session, transcript_account, patch_session, patch_send_task
):
    """Retryable FirefliesError must propagate so Celery's autoretry_for
    catches it; sync_error is recorded in either case."""
    fake_client = MagicMock()
    fake_client.list_transcripts.side_effect = FirefliesError(
        "transient blip", retryable=True
    )
    with patch(
        "memory.workers.tasks.transcripts.FirefliesClient", return_value=fake_client
    ):
        with pytest.raises(FirefliesError):
            transcripts.sync_transcript_account(transcript_account.id)
    db_session.refresh(transcript_account)
    assert "transient blip" in (transcript_account.sync_error or "")


def test_rescan_transcript_account_uses_full_provider(
    db_session, transcript_account, patch_session, patch_send_task
):
    """rescan_transcript_account dispatches via PROVIDERS with max_pages=RESCAN_MAX_PAGES."""
    fake_client = MagicMock()
    fake_client.list_transcripts.return_value = []  # one-call partial
    with patch(
        "memory.workers.tasks.transcripts.FirefliesClient", return_value=fake_client
    ):
        result = transcripts.rescan_transcript_account(transcript_account.id)

    assert result["status"] == "completed"
    fake_client.list_transcripts.assert_called_once()


def test_sync_all_transcript_accounts_dispatches_active(
    db_session, transcript_account, regular_user, patch_session
):
    inactive = TranscriptAccount(
        user_id=regular_user.id,
        name="Inactive",
        provider="fireflies",
        active=False,
    )
    inactive.api_key = "x"
    db_session.add(inactive)
    db_session.commit()

    with patch.object(
        transcripts.sync_transcript_account, "delay", return_value=MagicMock(id="t-1")
    ) as delay:
        result = transcripts.sync_all_transcript_accounts()

    assert len(result) == 1
    assert result[0]["account_id"] == transcript_account.id
    delay.assert_called_once_with(transcript_account.id)


def test_rescan_all_transcript_accounts_dispatches_active(
    db_session, transcript_account, regular_user, patch_session
):
    inactive = TranscriptAccount(
        user_id=regular_user.id,
        name="Inactive rescan",
        provider="fireflies",
        active=False,
    )
    inactive.api_key = "x"
    db_session.add(inactive)
    db_session.commit()

    with patch.object(
        transcripts.rescan_transcript_account,
        "delay",
        return_value=MagicMock(id="r-1"),
    ) as delay:
        result = transcripts.rescan_all_transcript_accounts()

    assert len(result) == 1
    assert result[0]["account_id"] == transcript_account.id
    delay.assert_called_once_with(transcript_account.id)


# ---------------------------------------------------------------------------
# Email-based attendee linking (covers the changes in meetings.py)
# ---------------------------------------------------------------------------


def test_link_attendees_via_email_creates_person(db_session):
    """The email-based linking path creates a Person from a clean email."""
    from memory.workers.tasks.meetings import link_attendees

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
    from memory.workers.tasks.meetings import link_attendees

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
    from memory.workers.tasks.meetings import link_attendees

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


def test_link_attendees_dedupes_repeated_emails(db_session):
    """A noisy upstream that lists the same email twice (different cases)
    should still produce a single Person and a single link."""
    from memory.workers.tasks.meetings import link_attendees

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
