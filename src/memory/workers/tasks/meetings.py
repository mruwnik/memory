"""Celery tasks for meeting transcript processing."""

import hashlib
import itertools
import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from dateutil import parser as date_parser
from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer, selectinload

from memory.agents.call import LLMError, run_agent_sync
from memory.agents.provider_keys import Credential, credential, ingest_key, ingest_user
from memory.agents.system import MEETING_EXTRACT
from memory.common import embedding
from memory.common import jobs as job_utils
from memory.common.llms import PROVIDER_ERRORS
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import CalendarAccount, JobStatus, JobType, PendingJob, Person, Task, TranscriptAccount, User
from memory.common.db.models.events import item_event
from memory.common.db.models.source_items import CalendarEvent, Meeting
from memory.common.celery_app import (
    app,
    CLEANUP_STUCK_MEETINGS,
    DEDUPE_MEETINGS,
    PROCESS_MEETING,
    REPROCESS_MEETING,
)
from memory.common.content_processing import (
    clear_item_chunks,
    process_content_item,
    safe_task_execution,
)
from memory.common.jobs import tracked_task
from memory.common.transcripts import submitted_external_id
from memory.common.people import (
    contact_values,
    find_or_create_person,
    find_person_by_email,
    find_person_by_name,
    make_identifier,
)
from memory.workers.verification import delete_orphaned_item

logger = logging.getLogger(__name__)

# A meeting whose processing was lost once and is being run again; losing it
# again marks it failed, so a meeting that kills its worker isn't rerun forever.
RETRYING = "retrying"
# The line meeting_content puts before the transcript, listing who attended.
ATTENDEES_PREFIX = "Attendees: "
# A transcript line's "Speaker: " prefix.
SPEAKER_LABEL = re.compile(r"^([^:\n]{1,100}):[ \t]", re.MULTILINE)


def mark_in_flight(meeting: Meeting, status: str) -> None:
    """Restart the clock cleanup_stuck_meetings judges a run lost by."""
    meeting.extraction_status = status
    # extraction_status is on the meeting table, so changing it alone never
    # fires source_item's updated_at onupdate.
    meeting.updated_at = func.now()

MAX_DUE_DATE_HORIZON = timedelta(days=2 * 365)
# A UTC meeting time can be a day ahead of the local date the attendees named.
DUE_DATE_SLACK = timedelta(days=1)


def parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        return date_parser.parse(date_str)
    except (ValueError, TypeError):
        return None


def parse_due_date(date_str: str | None, reference: datetime) -> datetime | None:
    """A due date the meeting held at `reference` could plausibly have set, else None."""
    due = parse_date(date_str)
    if due is None:
        return None
    if not (reference - DUE_DATE_SLACK).date() <= due.date() <= (reference + MAX_DUE_DATE_HORIZON).date():
        return None
    return due


def meeting_time(meeting: Meeting) -> datetime:
    return meeting.meeting_date or meeting.inserted_at or datetime.now(timezone.utc)


def make_task_sha256(meeting_id: int, description: str) -> bytes:
    """Generate a proper SHA256 hash for a task."""
    content = f"meeting:{meeting_id}:{description}"
    return hashlib.sha256(content.encode()).digest()


def call_extraction_llm(transcript: str, held_at: datetime, key: Credential) -> dict:
    """Summary, notes and action items for `transcript`, paid with `key`."""
    result = run_agent_sync(
        MEETING_EXTRACT.slug,
        f"Meeting date: {held_at:%A} {held_at.isoformat()}\n\nMeeting transcript:\n{transcript}",
        key=key,
    )
    return result.data or {}


def normalize_attendee_names(attendee_names: Sequence[str | None]) -> list[str]:
    """Flatten and normalize attendee names, splitting comma-separated values."""
    result = []
    for name in attendee_names:
        name = (name or "").strip()
        if not name:
            continue
        # Split comma-separated values (handles malformed input like "a@x.com,b@y.com")
        if "," in name:
            for part in name.split(","):
                part = part.strip()
                if part:
                    result.append(part)
        else:
            result.append(name)
    return result


def prettify_email_localpart(email: str) -> str:
    """Convert an email local-part into a slightly more humane display name.

    `daniel.oconnell@x.com` -> `Daniel Oconnell`, `dan_o@x.com` -> `Dan O`.
    Still imperfect (no real name signal), but less ugly than the raw
    local-part for entries the user will see in admin UI / search results.
    Returns "Unknown" for inputs that don't look like an email (no `@`) or
    have an empty local-part.
    """
    if "@" not in email:
        return "Unknown"
    local = email.split("@", 1)[0]
    cleaned = local.replace(".", " ").replace("_", " ").replace("-", " ")
    parts = [p for p in cleaned.split() if p]
    return " ".join(p.capitalize() for p in parts) or local or "Unknown"


def find_or_create_paired_person(
    session: DBSession,
    email: str,
    name: str,
    create_missing: bool,
    creator_id: int | None,
) -> tuple[Person | None, bool]:
    """Email-authoritative variant of ``find_or_create_person`` for the paired path.

    The paired path has *both* an email and a name from the same upstream
    attendee record. The email is the stable identifier; if find-by-email
    misses, we should NOT fall back to ``find_person_by_name`` (which can
    cross-link two real people who happen to share a display name like
    "Alex Smith"). Either link to the existing email-matched Person, create
    a brand-new one with this email, or return None.

    The unpaired email path (``leftover_emails``) and unpaired name path
    (``leftover_names``) keep their existing fuzzy behavior — only the
    paired path tightens here.
    """
    person = find_person_by_email(session, email)
    if person is not None:
        return person, False
    if not create_missing:
        return None, False

    identifier = make_identifier(name)
    person = Person(
        identifier=identifier,
        display_name=name,
        aliases=[name],
        contact_info={"email": email},
        creator_id=creator_id,
    )
    session.add(person)
    session.flush()
    logger.info(f"Created person '{identifier}' for paired attendee '{name}' <{email}>")
    return person, True


def attach_attendee(
    meeting: Meeting,
    person: Person,
    label: str,
    was_created: bool,
    seen_person_ids: set[int],
) -> str | None:
    """Attach a Person to the meeting if not already linked.

    Returns the outcome label: "created", "linked", or None if the person
    was already attached. The caller maintains seen_person_ids; this function
    mutates it to track which persons have been attached on this run.
    """
    if person.id in seen_person_ids:
        return None
    seen_person_ids.add(person.id)
    meeting.attendees.append(person)
    if was_created:
        logger.info(f"Created person '{person.identifier}' for attendee '{label}'")
        return "created"
    logger.info(f"Linked attendee '{label}' to person '{person.identifier}'")
    return "linked"


def link_attendees(
    session,
    meeting: Meeting,
    attendee_names: Sequence[str | None] = (),
    attendee_emails: Sequence[str | None] = (),
    create_missing: bool = True,
    attendee_pairs: Sequence[Sequence[str]] = (),
) -> dict:
    """Link attendee names and/or emails to Person records.

    ``attendee_pairs`` are ``(email, name)`` pairs from a source that knows
    which name goes with which email; they skip the index pairing below.

    Emails are processed first since they're a stable identifier — for
    Fireflies, meeting_attendees[].email is consistently populated while
    speaker labels can be garbage ("jgh"). Names are still supported for
    Granola / API uploads where only display names are available.

    When both lists are provided and equal in length they're paired by index
    (assumes both lists come from a single source's attendee list, which is
    the typical case). Otherwise emails and names are processed independently
    and find_or_create_person will fall back to its own email/name fuzzy
    match. Already-linked Persons are de-duplicated by id.

    Pairing happens *before* email dedup: when the raw emails list and the
    raw normalized names list are the same length, we zip them together
    first and then dedupe (email, name) tuples (preserving the first-seen
    pair). This protects against case-folded duplicates collapsing the
    email list out from under the name list — e.g.
    ``["alice@x.com", "ALICE@x.com"]`` + ``["Alice", "Alice"]`` would
    otherwise have lengths 1 and 2 after independent dedup, breaking
    pairing. With dedup-after-zip we get a single ``("alice@x.com", "Alice")``
    pair instead of fallback-to-independent-processing.
    """
    linked, created, skipped = 0, 0, []
    creator_id = meeting.resolve_creator_id()
    seen_person_ids: set[int] = {p.id for p in meeting.attendees}

    normalized_emails = [
        (email or "").strip().lower()
        for email in attendee_emails
        if (email or "").strip()
    ]
    normalized_names = normalize_attendee_names(attendee_names)

    # Pair-and-dedupe when both lists are present and the *raw* lengths
    # match. Dedupe by email (case folded above) but preserve the first-seen
    # name for each email so we don't lose name signal to a noisy upstream
    # that lists the same address twice.
    paired: list[tuple[str, str]] = [
        (email.strip().lower(), name.strip() or prettify_email_localpart(email.strip()))
        for email, name in attendee_pairs
        if email.strip()
    ]
    leftover_emails: list[str] = []
    leftover_names: list[str] = []
    if normalized_emails and normalized_names and len(normalized_emails) == len(
        normalized_names
    ):
        seen_emails: set[str] = set()
        for email, name in zip(normalized_emails, normalized_names):
            if email in seen_emails:
                continue
            seen_emails.add(email)
            paired.append((email, name))
        logger.info(f"Processing {len(paired)} paired attendees (email+name)")
    else:
        # Lengths genuinely differ pre-dedup, or one list is empty —
        # process emails and names independently. Dedupe the email list on
        # its own; cross-list pairing isn't reliable here.
        seen_emails = set()
        for email in normalized_emails:
            if email in seen_emails:
                continue
            seen_emails.add(email)
            leftover_emails.append(email)
        if normalized_emails and normalized_names:
            logger.warning(
                f"Email/name length mismatch ({len(normalized_emails)} emails, "
                f"{len(normalized_names)} names) — falling back to independent "
                "processing; cross-linking by display name is fragile."
            )
        leftover_names = normalized_names

    for email, name in paired:
        person, was_created = find_or_create_paired_person(
            session,
            email=email,
            name=name,
            create_missing=create_missing,
            creator_id=creator_id,
        )
        if person is None:
            logger.warning(
                f"Could not find person for paired attendee '{name}' / '{email}'"
            )
            skipped.append(email)
            continue
        outcome = attach_attendee(
            meeting, person, f"{name} <{email}>", was_created, seen_person_ids
        )
        if outcome == "created":
            created += 1
        elif outcome == "linked":
            linked += 1

    if leftover_emails:
        logger.info(f"Processing {len(leftover_emails)} attendee emails")
    for email in leftover_emails:
        person = find_person_by_email(session, email)
        was_created = False
        if person is None and create_missing:
            display_name = prettify_email_localpart(email)
            person, was_created = find_or_create_person(
                session,
                name=display_name,
                email=email,
                create_if_missing=True,
                creator_id=creator_id,
            )
        if person is None:
            logger.warning(f"Could not find person for email '{email}'")
            skipped.append(email)
            continue
        outcome = attach_attendee(meeting, person, email, was_created, seen_person_ids)
        if outcome == "created":
            created += 1
        elif outcome == "linked":
            linked += 1

    if leftover_names:
        logger.info(
            f"Processing {len(leftover_names)} attendee names: {leftover_names}"
        )
    for name in leftover_names:
        person, was_created = find_or_create_person(
            session, name, create_if_missing=create_missing, creator_id=creator_id
        )
        if person is None:
            logger.warning(f"Could not find person for attendee '{name}'")
            skipped.append(name)
            continue
        outcome = attach_attendee(meeting, person, name, was_created, seen_person_ids)
        if outcome == "created":
            created += 1
        elif outcome == "linked":
            linked += 1

    logger.info(
        f"Attendees: {linked} linked, {created} created, {len(skipped)} skipped"
    )
    return {"linked": linked, "created": created, "skipped": skipped}


# Tasks nobody has started, finished or cancelled.
UNTOUCHED_TASK_STATUSES = ("pending",)


def action_item_key(description: str) -> str:
    """Rewordings that only change case, spacing or a final full stop are the same action item."""
    return " ".join(description.casefold().split()).rstrip(".")


def apply_action_item(session, task: Task, item: dict, held_at: datetime) -> list[str]:
    """Set the task's fields from its action item; returns the change tags of those that changed.

    The assignee is the item's `assignee_id` when the account's filter matched
    one of its chosen people, else the one Person the assignee name resolves to.
    """
    assignee = (
        session.get(Person, item["assignee_id"])
        if "assignee_id" in item
        else find_person_by_name(session, item.get("assignee"))
    )
    priority = item.get("priority")
    fields = {
        "title": ("task_title", item["description"]),
        "due": ("due_date", parse_due_date(item.get("due_date"), held_at)),
        "priority": ("priority", priority if priority in ("low", "medium", "high", "urgent") else "medium"),
        "assignees": ("people", [assignee] if assignee else []),
    }
    changed = [tag for tag, (attr, value) in fields.items() if getattr(task, attr) != value]
    for attr, value in fields.values():
        setattr(task, attr, value)
    task.tags = ["meeting", "action-item"] + ([f"assignee:{assignee.identifier}"] if assignee else [])
    return changed


def sync_action_item_tasks(session, meeting: Meeting, action_items: list[dict]) -> list[Task]:
    """Make the meeting's Tasks match its action items, keeping each known item's task id.

    An item finds its task by hash, then by the title of a task attached to the
    meeting. Pending tasks follow the extraction: updated when kept (edits made
    while pending are overwritten), deleted when their item is gone. Tasks
    someone started, finished or cancelled are kept as they are. Returns the
    tasks created.
    """
    held_at = meeting_time(meeting)
    items: dict[str, dict] = {}
    for item in action_items:
        if item.get("description"):
            items.setdefault(action_item_key(item["description"]), item)
    hashes = {key: make_task_sha256(meeting.id, key) for key in items}
    # Older releases hashed the raw text.
    legacy = {key: make_task_sha256(meeting.id, item["description"]) for key, item in items.items()}
    by_sha = {
        t.sha256: t
        for t in session.query(Task).filter(Task.sha256.in_([*hashes.values(), *legacy.values()]))
    }
    attached = session.query(Task).filter(Task.source_item_id == meeting.id).all()
    by_title: dict[str, Task] = {}
    for task in attached:
        by_title.setdefault(action_item_key(task.task_title), task)

    kept: dict[str, Task] = {}
    for lookup in (lambda k: by_sha.get(hashes[k]), lambda k: by_sha.get(legacy[k]), by_title.get):
        for key in items.keys() - kept.keys():
            task = lookup(key)
            if task is not None and task not in kept.values():
                kept[key] = task

    created = []
    for key, item in items.items():
        task = kept.get(key)
        if task is None:
            task = Task(task_title=item["description"], status="pending", sha256=hashes[key])
            session.add(task)
            created.append(task)
            kept[key] = task
        task.source_item_id = meeting.id
        if by_sha.get(hashes[key]) in (None, task):
            task.sha256 = hashes[key]
        if task.status not in UNTOUCHED_TASK_STATUSES:
            continue
        changes = apply_action_item(session, task, item, held_at)
        if changes and task not in created:
            session.add(item_event(session, task, changes))

    for task in attached:
        if task.status in UNTOUCHED_TASK_STATUSES and task not in kept.values():
            session.delete(task)

    session.flush()
    # Tasks copy the meeting's stored values, so the meeting resolves first.
    meeting.apply_inherited_access_control()
    for task in kept.values():
        task.apply_inherited_access_control()
    return created


def name_key(value: str) -> str:
    """Names that differ only in case or spacing compare equal."""
    return " ".join(value.casefold().split())


def person_names(person: Person) -> set[str]:
    """Everything an action item may call `person` by: display name, aliases and emails."""
    emails = contact_values((person.contact_info or {}).get("email"))
    return {name_key(str(v)) for v in [person.display_name, *(person.aliases or []), *emails] if v}


def with_first_names(names: set[str]) -> set[str]:
    """`names` plus the first word of each name that isn't an email."""
    return names | {name.split(" ")[0] for name in names if "@" not in name}


def transcript_names(content: str) -> set[str]:
    """The attendee list and speaker labels written in a meeting's transcript.

    The list splits on commas, so "Smith, Daniel" reads as two names, and only
    labels at the start of a line count. Both only ever add names, which can
    block a match but never make one.
    """
    listed: list[str] = []
    if content.startswith(ATTENDEES_PREFIX):
        header, _, content = content.partition("\n")
        listed = header.removeprefix(ATTENDEES_PREFIX).split(",")
    return {name_key(name) for name in [*listed, *SPEAKER_LABEL.findall(content)] if name.strip()}


def other_attendee_names(meeting: Meeting, chosen: Sequence[Person]) -> set[str]:
    """Every name an action item may call an attendee who isn't one of the `chosen` people.

    Attendees are the meeting's linked Persons, its listed attendees and its
    speaker labels; a listed name or label that names a chosen person is theirs.
    Each attendee's full names, aliases and emails count, and their first names.
    """
    chosen_ids = {p.id for p in chosen}
    chosen_names = set().union(*(person_names(p) for p in chosen))
    linked = [person_names(p) for p in meeting.people if p.id not in chosen_ids]
    unlinked = transcript_names(meeting.content or "") - chosen_names
    return set().union(*(with_first_names(names) for names in [*linked, unlinked]))


def chosen_assignee(
    assignee: str | None, chosen: Sequence[Person], others: set[str]
) -> Person | None:
    """The one `chosen` person the action item `assignee` names, if any.

    Matches a chosen person's display name, alias or email case-insensitively,
    but never a name in `others`, the names of the meeting's other attendees,
    nor a name two chosen people share: a task for the wrong person is worse
    than a missed one.
    """
    key = name_key(assignee or "")
    if not key or key in others:
        return None
    matches = [p for p in chosen if key in person_names(p)]
    return matches[0] if len(matches) == 1 else None


def task_action_items(meeting: Meeting, action_items: list[dict]) -> list[dict]:
    """The action items the meeting's transcript account makes Tasks for.

    An account can turn tasks off, or limit them to action items assigned to
    its chosen people; those items carry the matched person's `assignee_id`.
    Meetings without an account make Tasks for every item.
    """
    account = meeting.transcript_account
    if account is None:
        return action_items
    if not account.create_tasks:
        return []
    if not account.task_people:
        return action_items
    others = other_attendee_names(meeting, account.task_people)
    matched = [
        (item, chosen_assignee(item.get("assignee"), account.task_people, others))
        for item in action_items
    ]
    return [{**item, "assignee_id": person.id} for item, person in matched if person]


def detach_meeting_tasks(session: DBSession, meeting: Meeting) -> None:
    """Detach action item tasks from a meeting about to be deleted.

    Detached tasks keep the access control they inherited from the meeting:
    marking it explicit stops it from re-resolving to no source at all.
    """
    tasks = session.query(Task).filter(Task.source_item_id == meeting.id).all()
    for task in tasks:
        task.source_item_id = None
        task.project_id_inherited = False
        task.sensitivity_inherited = False
    session.flush()


def extract_and_update_meeting(session: DBSession, meeting: Meeting) -> dict:
    """
    Run LLM extraction on a meeting and update it with extracted data.

    Leaves the meeting's in-flight status for the caller to finish, so a worker
    lost before the meeting is stored leaves it for cleanup_stuck_meetings. A
    meeting no key pays for is not extracted. Returns dict
    with extraction results including summary_length, notes_length,
    tasks_created, or error info on failure.
    """
    found = ingest_key(session, meeting, MEETING_EXTRACT)
    if found is None:
        return {"status": "skipped"}

    try:
        extracted = call_extraction_llm(
            meeting.content or "", meeting_time(meeting), credential(found, ingest_user(meeting))
        )
    except (LLMError, *PROVIDER_ERRORS) as e:
        logger.warning(f"Failed to extract meeting {meeting.id}: {type(e).__name__}")
        meeting.extraction_status = "failed"
        return {"status": "error", "error": str(e)}

    meeting.summary = extracted.get("summary", "")
    meeting.notes = extracted.get("notes", "")
    action_items = task_action_items(meeting, extracted.get("action_items", []))
    created = sync_action_item_tasks(session, meeting, action_items)
    logger.info(f"Created {len(created)} tasks from meeting {meeting.id}")

    return {
        "status": "success",
        "summary_length": len(meeting.summary or ""),
        "notes_length": len(meeting.notes or ""),
        "tasks_created": len(created),
        "created_task_ids": [t.id for t in created],
    }


def meeting_content(
    transcript: str,
    attendee_names: list[str] | None,
    attendee_emails: list[str] | None,
    attendee_pairs: list[tuple[str, str]] | None,
) -> str:
    # Prepend attendee identifiers to transcript so the LLM has a hint about
    # who's in the room. Names take precedence over emails for readability.
    pair_labels = [name or email for email, name in attendee_pairs or []]
    attendee_label = ", ".join(
        v for v in (pair_labels + (attendee_names or []) + (attendee_emails or [])) if v
    )
    if attendee_label:
        return f"{ATTENDEES_PREFIX}{attendee_label}\n\n{transcript}"
    return transcript


def create_meeting_record(
    session: DBSession,
    transcript: str,
    title: str | None,
    meeting_date: str | None,
    duration_minutes: int | None,
    attendee_names: list[str] | None,
    source_tool: str | None,
    external_id: str | None,
    tags: list[str] | None,
    attendee_emails: list[str] | None = None,
    transcript_account_id: int | None = None,
    creator_id: int | None = None,
    attendee_pairs: list[tuple[str, str]] | None = None,
    calendar_event_uid: str | None = None,
) -> Meeting:
    """Create a new Meeting record from the provided data."""
    full_transcript = meeting_content(transcript, attendee_names, attendee_emails, attendee_pairs)
    meeting = Meeting(
        size=len(full_transcript.encode()),
        title=title,
        meeting_date=parse_date(meeting_date),
        duration_minutes=duration_minutes,
        source_tool=source_tool,
        external_id=external_id,
        calendar_event_uid=calendar_event_uid,
        content=full_transcript,
        sha256=hashlib.sha256(full_transcript.encode()).digest(),
        tags=["meeting"] + (tags or []),
        extraction_status="processing",
        transcript_account_id=transcript_account_id,
        creator_id=creator_id,
    )
    session.add(meeting)
    session.flush()
    logger.info(f"Created meeting {meeting.id}")
    return meeting


def prepare_meeting_for_reingest(session: DBSession, item_id: int, retry: bool = False) -> Meeting | None:
    """
    Fetch an existing meeting and clear its chunks for reprocessing.

    Its tasks stay attached, for extraction to match to its action items.
    People relationships are left intact (they're re-linked during
    processing). A `retry` is cleanup_stuck_meetings rerunning a lost run,
    and isn't rerun again.
    """
    meeting = session.get(Meeting, item_id)
    if not meeting:
        return None

    chunks_deleted = clear_item_chunks(meeting, session)
    # Committed with the cleared chunks before the LLM call, so a worker lost
    # mid-call leaves it for cleanup_stuck_meetings rather than looking done.
    mark_in_flight(meeting, RETRYING if retry else "processing")
    logger.info(
        f"Prepared meeting {item_id} for reingest: "
        f"cleared {chunks_deleted} chunks"
    )
    return meeting


def calendar_event_for(session: DBSession, meeting: Meeting) -> CalendarEvent | None:
    """The meeting owner's calendar event it recorded. A recurring event's
    occurrences can share one uid, so the one starting nearest the meeting."""
    owner_id = meeting.resolve_creator_id()
    if not meeting.calendar_event_uid or owner_id is None:
        return None
    events = (
        session.query(CalendarEvent)
        .join(CalendarAccount, CalendarEvent.calendar_account_id == CalendarAccount.id)
        .filter(CalendarEvent.external_id == meeting.calendar_event_uid, CalendarAccount.user_id == owner_id)
        .all()
    )
    held_at = meeting.meeting_date
    if held_at is None:
        return next(iter(events), None)
    return min(events, key=lambda event: abs(event.start_time - held_at), default=None)


def execute_meeting_processing(
    session: DBSession,
    meeting: Meeting,
    attendee_names: list[str] | None,
    job_id: int | None,
    attendee_emails: list[str] | None = None,
    attendee_pairs: list[tuple[str, str]] | None = None,
) -> dict:
    """
    Run the full processing pipeline on a meeting.

    This is the shared processing step for both ingest and reingest:
    1. Link attendees to Person records
    2. Extract summary, notes, and action items via LLM
    3. Generate embeddings

    Args:
        session: Database session
        meeting: Meeting record (new or existing with chunks cleared)
        attendee_names: Optional attendee names to link
        job_id: Optional job ID for status tracking

    Returns:
        Dict with processing results
    """
    extract_result = None
    try:
        attendee_result = {"linked": 0, "created": 0, "skipped": []}
        if attendee_names or attendee_emails or attendee_pairs:
            attendee_result = link_attendees(
                session,
                meeting,
                attendee_names=attendee_names or [],
                attendee_emails=attendee_emails or [],
                attendee_pairs=attendee_pairs or [],
            )
        meeting.calendar_event = meeting.calendar_event or calendar_event_for(session, meeting)
        # Before the slow LLM call: releases the ingest's call locks, and keeps
        # the attendees a second recording is matched by even if extraction fails.
        session.commit()

        extract_result = extract_and_update_meeting(session, meeting)

        # Bail out early if extraction itself failed — don't proceed to
        # embeddings or overwrite extraction_status="failed" with "complete".
        if extract_result.get("status") == "error":
            error_msg = extract_result.get("error", "extraction failed")
            if job_id:
                job_utils.fail_job(session, job_id, error_msg)
            session.commit()
            return {
                "status": "error",
                "error": error_msg,
                "meeting_id": meeting.id,
                "external_id": meeting.external_id,
            }

        session.commit()
        session.refresh(meeting)
        embed_result = process_content_item(meeting, session)
        # Without an embeddings key nothing gets embedded; that is setup, not this meeting failing.
        if embed_result["status"] == "failed" and embedding.configured():
            raise RuntimeError(f"Meeting {meeting.id} was not stored")

        # Only mark complete after both extraction AND embedding succeed
        meeting.extraction_status = "skipped" if extract_result["status"] == "skipped" else "complete"
        session.commit()

        if job_id:
            job_utils.complete_job(
                session, job_id, result_id=meeting.id, result_type="Meeting"
            )
            session.commit()

        return {
            "status": "success",
            "meeting_id": meeting.id,
            "external_id": meeting.external_id,
            "summary_length": extract_result.get("summary_length", 0),
            "notes_length": extract_result.get("notes_length", 0),
            "tasks_created": extract_result.get("tasks_created", 0),
            "attendees_linked": attendee_result["linked"],
            "attendees_created": attendee_result["created"],
            "attendees_skipped": attendee_result["skipped"],
            "embedding_result": embed_result,
        }

    except Exception as e:
        logger.exception(f"Failed to process meeting {meeting.id}")
        session.rollback()
        # "extracted": extraction worked, but the meeting wasn't stored.
        outcome = (extract_result or {}).get("status", "")
        meeting.extraction_status = {"success": "extracted", "skipped": "skipped"}.get(outcome, "failed")
        # New tasks would point at a meeting search can't return; reprocessing
        # recreates them. Tasks this run reused keep their ids.
        created_ids = (extract_result or {}).get("created_task_ids", [])
        new = session.query(Task).filter(Task.id.in_(created_ids), Task.status.in_(UNTOUCHED_TASK_STATUSES))
        for task in new:
            session.delete(task)
        if job_id:
            job_utils.fail_job(session, job_id, str(e))
        session.commit()
        return {"status": "error", "error": str(e), "meeting_id": meeting.id}


# Two notetakers in one call record nearly the same stretch of it; a recording
# restarted after a drop, or the next meeting under the same title, barely
# overlaps the first.
MIN_SHARED_FRACTION = 0.5
# Without a duration, only recordings starting this close are one call.
SAME_START_WINDOW = timedelta(minutes=5)
# How far back to look for a stored recording that may overlap a new one.
LONGEST_CALL = timedelta(days=1)


@dataclass(frozen=True)
class Call:
    """What tells two recordings of one call from two meetings."""

    start: datetime | None
    duration: timedelta | None
    calendar_event_uid: str | None
    title: str
    emails: frozenset[str]

    @property
    def reach(self) -> datetime | None:
        """The latest start a recording of this call can have."""
        if self.start is None:
            return None
        return self.start + max(self.duration or timedelta(0), SAME_START_WINDOW)


def normalised_emails(emails: Iterable[str | None]) -> frozenset[str]:
    # Fireflies sometimes joins several addresses with commas in one entry.
    return frozenset(
        part.strip().lower() for email in emails for part in (email or "").split(",") if part.strip()
    )


def make_call(
    start: datetime | None,
    duration_minutes: float | None,
    calendar_event_uid: str | None,
    title: str | None,
    emails: Iterable[str | None],
) -> Call:
    if start is not None and start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return Call(
        start=start,
        duration=timedelta(minutes=duration_minutes) if duration_minutes else None,
        calendar_event_uid=calendar_event_uid or None,
        title=" ".join((title or "").casefold().split()),
        emails=normalised_emails(emails),
    )


def overlapping(a: Call, b: Call) -> bool:
    if a.start is None or b.start is None:
        return False
    if not (a.duration and b.duration):
        return abs(a.start - b.start) <= SAME_START_WINDOW
    shared = min(a.start + a.duration, b.start + b.duration) - max(a.start, b.start)
    return shared >= min(a.duration, b.duration) * MIN_SHARED_FRACTION


def same_call(a: Call, b: Call, owner_emails: frozenset[str]) -> bool:
    """Calendar ids aren't shared across calendar providers, so differing ids
    don't rule out one call; title and a guest still match it. The owner is in
    every call they record, so their addresses don't count as a guest."""
    if not overlapping(a, b):
        return False
    if a.calendar_event_uid and a.calendar_event_uid == b.calendar_event_uid:
        return True
    return bool(a.title) and a.title == b.title and bool((a.emails & b.emails) - owner_emails)


def owner_emails(session: DBSession, user_id: int) -> frozenset[str]:
    user = session.get(User, user_id)
    if user is None:
        return frozenset()
    person_emails = contact_values((user.person.contact_info or {}).get("email")) if user.person else []
    return normalised_emails([user.email, *person_emails])


def stored_call(meeting: Meeting) -> Call:
    return make_call(
        meeting.meeting_date,
        meeting.duration_minutes,
        meeting.calendar_event_uid,
        meeting.title,
        (email for person in meeting.people for email in contact_values((person.contact_info or {}).get("email"))),
    )


def meeting_owner():
    """A synced meeting's creator is filled from its account's owner only once processed."""
    return func.coalesce(Meeting.creator_id, TranscriptAccount.user_id)


def owned_meetings(session: DBSession):
    return session.query(Meeting).outerjoin(
        TranscriptAccount, Meeting.transcript_account_id == TranscriptAccount.id
    )


def find_recorded_call(session: DBSession, owner_id: int, source_tool: str, call: Call) -> Meeting | None:
    """The owner's stored meeting from `source_tool` that recorded `call`, if any."""
    if call.start is None or call.reach is None:
        return None
    candidates = (
        owned_meetings(session)
        .filter(
            meeting_owner() == owner_id,
            Meeting.source_tool == source_tool,
            Meeting.meeting_date.between(call.start - LONGEST_CALL, call.reach),
        )
        .options(defer(Meeting.content), selectinload(Meeting.people))
        .order_by(Meeting.id)
    )
    ignored = owner_emails(session, owner_id)
    return next((m for m in candidates if same_call(call, stored_call(m), ignored)), None)


def earlier_recording(
    session: DBSession,
    source_tool: str | None,
    creator_id: int | None,
    transcript_account_id: int | None,
    call: Call,
) -> Meeting | None:
    """The stored meeting `call` is a second recording of, for the same owner only.

    Takes locks held until the session commits: two recordings of a call that
    arrive together would otherwise both find nothing stored and both be kept.
    """
    account = transcript_account_id and session.get(TranscriptAccount, transcript_account_id)
    owner_id = creator_id if creator_id is not None else account and account.user_id
    if not owner_id or not source_tool or call.start is None:
        return None
    # Sorted, so two ingests needing the same pair of locks take them in one order.
    keys = sorted(
        key
        for key in (
            call.calendar_event_uid and f"cal:{call.calendar_event_uid}",
            call.title and f"title:{call.title}",
        )
        if key
    )
    for key in keys:
        lock_key = func.hashtextextended(f"meeting_call:{owner_id}:{source_tool}:{key}", 0)
        session.execute(select(func.pg_advisory_xact_lock(lock_key)))
    return find_recorded_call(session, owner_id, source_tool, call)


def recording_exists(
    session: DBSession, recorded: Meeting, external_id: str | None, job_id: int | None
) -> dict:
    logger.info(f"Meeting {external_id} is another recording of meeting {recorded.id}")
    if job_id:
        job_utils.complete_job(session, job_id, result_id=recorded.id, result_type="Meeting")
        session.commit()
    return {"status": "duplicate", "meeting_id": recorded.id, "external_id": external_id}


def find_meeting(session: DBSession, external_id: str | None) -> Meeting | None:
    if not external_id:
        return None
    return session.query(Meeting).filter(Meeting.external_id == external_id).first()


def meeting_exists(session: DBSession, existing: Meeting, job_id: int | None) -> dict:
    logger.info(f"Meeting with external_id={existing.external_id} already exists")
    if job_id:
        job_utils.complete_job(session, job_id, result_id=existing.id, result_type="Meeting")
        session.commit()
    return {"status": "exists", "meeting_id": existing.id, "external_id": existing.external_id}


@app.task(name=PROCESS_MEETING)
@safe_task_execution
def process_meeting(
    transcript: str,
    title: str | None = None,
    meeting_date: str | None = None,
    duration_minutes: int | None = None,
    attendee_names: list[str] | None = None,
    source_tool: str | None = None,
    external_id: str | None = None,
    tags: list[str] | None = None,
    job_id: int | None = None,
    attendee_emails: list[str] | None = None,
    transcript_account_id: int | None = None,
    creator_id: int | None = None,
    attendee_pairs: list[tuple[str, str]] | None = None,
    replace: bool = False,
    calendar_event_uid: str | None = None,
):
    """
    Process a new meeting transcript.

    A second recording of a call its owner already has from the same tool
    (see ``same_call``) is not stored.

    Creates meeting record, extracts summary/notes/action items via LLM,
    links attendees to Person records, and generates embeddings.

    Args:
        transcript: Meeting transcript text (required)
        title: Optional meeting title
        meeting_date: Optional meeting date (ISO format string)
        duration_minutes: Optional duration in minutes
        attendee_names: Optional list of attendee names
        source_tool: Source of transcript (fireflies, granola, etc.)
        external_id: External ID for idempotency
        tags: Optional tags for the meeting
        job_id: Optional job ID for status tracking
        creator_id: Uploader; synced meetings inherit their account's owner
        attendee_pairs: (email, name) pairs known to belong to one attendee
        replace: Overwrite and reprocess a meeting already stored under external_id
        calendar_event_uid: The source's id for the calendar event occurrence
    """
    logger.info(f"Processing new meeting (external_id={external_id}, job_id={job_id})")

    with make_session() as session:
        if job_id:
            job_utils.start_job(session, job_id)
            session.commit()

        existing = find_meeting(session, external_id)
        content = meeting_content(transcript, attendee_names, attendee_emails, attendee_pairs)
        sha256 = hashlib.sha256(content.encode()).digest()
        replacing = bool(
            existing
            and replace
            and existing.sha256 != sha256
            and existing.transcript_account_id == transcript_account_id
        )
        # "processing": a worker lost mid-run had its task redelivered.
        if existing and (replacing or existing.extraction_status == "processing"):
            prepare_meeting_for_reingest(session, existing.id, retry=not replacing)
            meeting = existing
            if replacing:
                meeting.content = content
                meeting.sha256 = sha256
                meeting.size = len(content.encode())
                meeting.title = title
                meeting.meeting_date = parse_date(meeting_date)
                meeting.duration_minutes = duration_minutes
            session.flush()
        elif existing:
            return meeting_exists(session, existing, job_id)
        elif recorded := earlier_recording(
            session,
            source_tool,
            creator_id,
            transcript_account_id,
            make_call(
                parse_date(meeting_date),
                duration_minutes,
                calendar_event_uid,
                title,
                [*(attendee_emails or []), *(email for email, _ in attendee_pairs or [])],
            ),
        ):
            return recording_exists(session, recorded, external_id, job_id)
        else:
            try:
                meeting = create_meeting_record(
                    session,
                    transcript,
                    title,
                    meeting_date,
                    duration_minutes,
                    attendee_names,
                    source_tool,
                    external_id,
                    tags,
                    attendee_emails=attendee_emails,
                    transcript_account_id=transcript_account_id,
                    creator_id=creator_id,
                    attendee_pairs=attendee_pairs,
                    calendar_event_uid=calendar_event_uid,
                )
            except IntegrityError:
                # Lost a race with another ingest of the same meeting (webhook
                # vs. poll) between the check above and the insert.
                session.rollback()
                if not (winner := find_meeting(session, external_id)):
                    raise
                return meeting_exists(session, winner, job_id)

        return execute_meeting_processing(
            session,
            meeting,
            attendee_names,
            job_id,
            attendee_emails=attendee_emails,
            attendee_pairs=attendee_pairs,
        )


@app.task(name=REPROCESS_MEETING)
@safe_task_execution
def reprocess_meeting(
    item_id: int,
    job_id: int | None = None,
    retry: bool = False,
):
    """
    Reprocess an existing meeting.

    Fetches the meeting, clears chunks and untouched tasks, then re-runs
    the full processing pipeline (LLM extraction + embeddings).

    Args:
        item_id: ID of the meeting to reprocess
        job_id: Optional job ID for status tracking
        retry: Rerunning a run cleanup_stuck_meetings found lost
    """
    logger.info(f"Reprocessing meeting {item_id} (job_id={job_id})")

    with make_session() as session:
        if job_id:
            job_utils.start_job(session, job_id)
            session.commit()

        meeting = prepare_meeting_for_reingest(session, item_id, retry=retry)
        if not meeting:
            error = f"Meeting {item_id} not found"
            if job_id:
                job_utils.fail_job(session, job_id, error)
                session.commit()
            return {"status": "error", "error": error}

        # Get attendee names from existing meeting for re-linking
        attendee_names = (
            [p.display_name for p in meeting.attendees] if meeting.attendees else None
        )

        return execute_meeting_processing(session, meeting, attendee_names, job_id)


def job_key(meeting: Meeting) -> ColumnElement[bool]:
    """Matches the meeting's owner's jobs whose idempotency key brought it in.

    The owner is the transcript account's user, else the meeting's creator. A
    meeting submitted to POST /meetings stores a namespaced id, while its job
    keeps the submitter's own id.
    """
    account = meeting.transcript_account
    owner = account.user_id if account else meeting.creator_id
    stored = meeting.external_id or ""
    key = (owner and submitted_external_id(owner, stored)) or stored
    return and_(PendingJob.external_id == key, PendingJob.user_id == owner)


def open_job(session: DBSession, meeting: Meeting) -> PendingJob | None:
    """The unfinished job that brought the meeting in, so its rerun closes it."""
    if not meeting.external_id:
        return None
    return (
        session.query(PendingJob)
        .filter(
            PendingJob.job_type == JobType.MEETING.value,
            job_key(meeting),
            PendingJob.status.in_((JobStatus.PENDING.value, JobStatus.PROCESSING.value)),
        )
        .order_by(PendingJob.id.desc())
        .first()
    )


# Timeout for stuck meetings (1 hour - if processing takes longer, something is wrong)
STUCK_MEETING_TIMEOUT_HOURS = 1


@app.task(name=CLEANUP_STUCK_MEETINGS)
@safe_task_execution
def cleanup_stuck_meetings() -> dict:
    """Rerun once the meetings whose extraction a lost worker left unfinished,
    and mark failed those already rerun once. A `pending` meeting is one an
    older release's cleanup reset and never reran."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=STUCK_MEETING_TIMEOUT_HOURS)

    with make_session() as session:
        stuck = (
            session.query(Meeting)
            .filter(
                Meeting.extraction_status.in_(("pending", "processing", RETRYING)),
                Meeting.updated_at < cutoff,
            )
            .all()
        )
        retried = [m.id for m in stuck if m.extraction_status != RETRYING]
        failed = [m.id for m in stuck if m.extraction_status == RETRYING]
        jobs = {m.id: open_job(session, m) for m in stuck}
        for meeting in stuck:
            if meeting.id in failed:
                meeting.extraction_status = "failed"
                if job := jobs[meeting.id]:
                    job.mark_failed("Worker lost twice while processing the meeting")
                continue
            mark_in_flight(meeting, RETRYING)
        session.commit()
        job_ids = {item_id: job.id for item_id, job in jobs.items() if job}

    for item_id in retried:
        kwargs = {"item_id": item_id, "retry": True}
        if item_id in job_ids:
            kwargs["job_id"] = job_ids[item_id]
        app.send_task(REPROCESS_MEETING, kwargs=kwargs)
    if stuck:
        logger.warning(f"Stuck meetings: retrying {retried}, gave up on {failed}")
    return {"retried": retried, "failed": failed}


def recording_groups(meetings: Iterable[Meeting], owner_emails: frozenset[str]) -> list[list[Meeting]]:
    """Group one owner's meetings from one tool, sorted by date, into
    recordings of one call. Each member is the same call as every other, so a
    run of overlapping meetings doesn't chain into one group."""
    groups: list[list[tuple[Meeting, Call]]] = []
    open_groups: list[list[tuple[Meeting, Call]]] = []
    for meeting in meetings:
        call = stored_call(meeting)
        open_groups = [g for g in open_groups if all(c.reach and call.start and c.reach >= call.start for _, c in g)]
        group = next((g for g in open_groups if all(same_call(call, c, owner_emails) for _, c in g)), None)
        if group is None:
            group = []
            open_groups.append(group)
            groups.append(group)
        group.append((meeting, call))
    return [[meeting for meeting, _ in group] for group in groups if len(group) > 1]


def drop_recording(session: DBSession, meeting: Meeting) -> None:
    """Delete a meeting, its vectors and its untouched tasks; tasks someone
    started or finished are detached and kept."""
    untouched = (
        session.query(Task)
        .filter(Task.source_item_id == meeting.id, Task.status.in_(UNTOUCHED_TASK_STATUSES))
        .all()
    )
    for task in untouched:
        delete_orphaned_item(task, session)
    session.flush()
    detach_meeting_tasks(session, meeting)
    delete_orphaned_item(meeting, session)


@app.task(name=DEDUPE_MEETINGS)
@tracked_task
def dedupe_meetings(dry_run: bool = False) -> dict:
    """Delete second recordings of a call (see ``same_call``). Keeps the copy
    whose tasks someone worked on, so those don't sit beside pending copies of
    themselves, else one whose processing finished, else the longest
    transcript, else the earliest stored. Safe to re-run."""
    with make_session() as session:
        rows = (
            owned_meetings(session)
            .add_columns(meeting_owner(), func.length(Meeting.content))
            .filter(
                meeting_owner().isnot(None),
                Meeting.source_tool.isnot(None),
                Meeting.meeting_date.isnot(None),
            )
            .options(defer(Meeting.content), selectinload(Meeting.people))
            .order_by(meeting_owner(), Meeting.source_tool, Meeting.meeting_date, Meeting.id)
            .all()
        )
        lengths = {meeting.id: length or 0 for meeting, _, length in rows}
        worked_on = set(
            session.scalars(
                select(Task.source_item_id)
                .join(Meeting, Meeting.id == Task.source_item_id)
                .where(Task.status.not_in(UNTOUCHED_TASK_STATUSES))
                .distinct()
            )
        )
        groups = [
            group
            for (owner_id, _), owned in itertools.groupby(rows, key=lambda row: (row[1], row[0].source_tool))
            for group in recording_groups((meeting for meeting, *_ in owned), owner_emails(session, owner_id))
        ]
        plan = [
            (keeper, [m for m in group if m is not keeper])
            for group in groups
            for keeper in [
                max(
                    group,
                    key=lambda m: (
                        m.id in worked_on,
                        m.extraction_status in ("complete", "skipped"),
                        lengths[m.id],
                        -m.id,
                    ),
                )
            ]
        ]
        result = {
            "dry_run": dry_run,
            "groups": [{"keep": keeper.id, "delete": [m.id for m in dropped]} for keeper, dropped in plan],
        }
        if dry_run:
            return result
        for _, dropped in plan:
            for meeting in dropped:
                drop_recording(session, meeting)
            session.commit()
    logger.info("Deleted %d second recordings of calls", sum(len(d) for _, d in plan))
    return result
