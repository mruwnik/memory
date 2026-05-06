"""Celery tasks for meeting transcript processing."""

import hashlib
import json
import logging
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from dateutil import parser as date_parser

from memory.common import llms, settings, jobs as job_utils
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import Task
from memory.common.db.models.source_items import Meeting
from memory.common.celery_app import app, PROCESS_MEETING, REPROCESS_MEETING, CLEANUP_STUCK_MEETINGS
from memory.common.content_processing import (
    clear_item_chunks,
    process_content_item,
    safe_task_execution,
)
from memory.common.db.models import Person
from memory.common.people import (
    find_or_create_person,
    find_person_by_email,
    find_person_by_name,
    make_identifier,
)

logger = logging.getLogger(__name__)

DEFAULT_EXTRACTION_PROMPT = """You are analyzing a meeting transcript. Extract the following information:

1. A concise 2-3 sentence summary of the meeting's main purpose and outcomes
2. Key discussion points, decisions made, and important information (as bullet points)
3. Action items - tasks that were assigned or need to be done, with assignee, due date, and priority if mentioned

Return your analysis as JSON with this exact structure:
{{
    "summary": "Brief summary of the meeting",
    "notes": "- Key point 1\\n- Key point 2\\n- Decision made\\n- etc.",
    "action_items": [
        {{
            "description": "Task description",
            "assignee": "Person name or null if not specified",
            "due_date": "YYYY-MM-DD or null if not specified",
            "priority": "low, medium, high, or urgent based on context, or null if unclear"
        }}
    ]
}}

IMPORTANT: Return ONLY valid JSON, no markdown formatting or extra text.

Meeting transcript:
{transcript}
"""

DEFAULT_SYSTEM_PROMPT = "You are an expert at analyzing meeting transcripts and extracting actionable information. Always return valid JSON."


def parse_extraction_response(response: str) -> dict:
    """Parse the JSON response from the LLM."""
    response = response.strip()
    if not response.startswith("```"):
        try:
            return json.loads(response)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse extraction response: {e}")
            logger.error(f"Response was: {response[:500]}")
            return {"summary": "", "notes": "", "action_items": []}

    # Remove markdown code block
    lines = response.split("\n")
    json_lines = []
    in_block = False
    for line in lines:
        if line.startswith("```") and not in_block:
            in_block = True
            continue
        if line.startswith("```") and in_block:
            break
        if in_block:
            json_lines.append(line)

    try:
        return json.loads("\n".join(json_lines))
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse extraction response: {e}")
        logger.error(f"Response was: {response[:500]}")
        return {"summary": "", "notes": "", "action_items": []}


def parse_due_date(date_str: str | None) -> datetime | None:
    """Parse a due date string into a datetime."""
    if not date_str:
        return None
    try:
        return date_parser.parse(date_str)
    except (ValueError, TypeError):
        return None


def make_task_sha256(meeting_id: int, description: str) -> bytes:
    """Generate a proper SHA256 hash for a task."""
    content = f"meeting:{meeting_id}:{description}"
    return hashlib.sha256(content.encode()).digest()


def call_extraction_llm(
    transcript: str,
    extraction_prompt: str | None = None,
    system_prompt: str | None = None,
    model: str | None = None,
) -> dict:
    """Call LLM to extract structured information from transcript."""
    prompt_template = extraction_prompt or DEFAULT_EXTRACTION_PROMPT
    sys_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
    llm_model = model or getattr(settings, "MEETING_MODEL", settings.SUMMARIZER_MODEL)

    prompt = prompt_template.format(transcript=transcript)
    logger.info(f"Calling LLM for meeting extraction using {llm_model}")
    response = llms.summarize(prompt, model=llm_model, system_prompt=sys_prompt)
    return parse_extraction_response(response)


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
) -> dict:
    """Link attendee names and/or emails to Person records.

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
    paired: list[tuple[str, str]] = []
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
            session, email=email, name=name, create_missing=create_missing
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
                session, name=display_name, email=email, create_if_missing=True
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
            session, name, create_if_missing=create_missing
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


def create_action_item_tasks(
    session, meeting: Meeting, action_items: list[dict]
) -> list[str]:
    """Create Task records for action items. Returns list of created task descriptions."""
    created_tasks = []

    for item in action_items:
        if not item.get("description"):
            continue

        task_tags = ["meeting", "action-item"]

        assignee_name = item.get("assignee")
        assignee_person = (
            find_person_by_name(session, assignee_name) if assignee_name else None
        )
        if assignee_person:
            task_tags.append(f"assignee:{assignee_person.identifier}")

        extracted_priority = item.get("priority")
        priority = (
            extracted_priority
            if extracted_priority in ("low", "medium", "high", "urgent")
            else "medium"
        )

        task = Task(
            task_title=item["description"],
            source_item_id=meeting.id,
            due_date=parse_due_date(item.get("due_date")),
            priority=priority,
            status="pending",
            tags=task_tags,
            sha256=make_task_sha256(meeting.id, item["description"]),
        )

        # Link assignee to task via people relationship
        if assignee_person and assignee_person not in task.people:
            task.people.append(assignee_person)

        session.add(task)
        created_tasks.append(item["description"])

    return created_tasks


def detach_meeting_tasks(session: DBSession, meeting: Meeting) -> int:
    """Detach action item tasks from a meeting (for reingest). Returns count detached."""
    tasks = session.query(Task).filter(Task.source_item_id == meeting.id).all()
    for task in tasks:
        task.source_item_id = None
    session.flush()
    return len(tasks)


def extract_and_update_meeting(
    session: DBSession,
    meeting: Meeting,
    extraction_prompt: str | None = None,
    system_prompt: str | None = None,
    model: str | None = None,
) -> dict:
    """
    Run LLM extraction on a meeting and update it with extracted data.

    Returns dict with extraction results including summary_length, notes_length,
    tasks_created, or error info on failure.
    """
    meeting.extraction_status = "processing"
    session.flush()

    try:
        transcript = meeting.content or ""
        extracted = call_extraction_llm(
            transcript, extraction_prompt, system_prompt, model
        )
        meeting.summary = extracted.get("summary", "")
        meeting.notes = extracted.get("notes", "")
        # Note: status stays "processing" until embedding completes in execute_meeting_processing
        meeting.extraction_status = "extracted"

        created_tasks = create_action_item_tasks(
            session, meeting, extracted.get("action_items", [])
        )
        logger.info(f"Created {len(created_tasks)} tasks from meeting {meeting.id}")

        return {
            "status": "success",
            "summary_length": len(meeting.summary or ""),
            "notes_length": len(meeting.notes or ""),
            "tasks_created": len(created_tasks),
        }
    except Exception as e:
        logger.exception(f"Failed to extract meeting {meeting.id}: {e}")
        meeting.extraction_status = "failed"
        return {"status": "error", "error": str(e)}


def reextract_meeting(
    session: DBSession,
    meeting: Meeting,
    extraction_prompt: str | None = None,
    system_prompt: str | None = None,
    model: str | None = None,
) -> dict:
    """
    Re-run LLM extraction on a meeting transcript.

    Detaches existing action item tasks and re-extracts summary, notes, and action items.
    Does NOT commit - caller controls the transaction.
    """
    logger.info(f"Re-extracting meeting {meeting.id}")

    # Detach existing action item tasks (leave them in DB but unlinked)
    detached_tasks = detach_meeting_tasks(session, meeting)
    logger.info(f"Detached {detached_tasks} existing tasks for meeting {meeting.id}")

    return extract_and_update_meeting(
        session, meeting, extraction_prompt, system_prompt, model
    )


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
) -> Meeting:
    """Create a new Meeting record from the provided data."""
    parsed_date = parse_due_date(meeting_date)

    # Prepend attendee identifiers to transcript so the LLM has a hint about
    # who's in the room. Names take precedence over emails for readability.
    full_transcript = transcript
    attendee_label = ", ".join(
        v for v in ((attendee_names or []) + (attendee_emails or [])) if v
    )
    if attendee_label:
        full_transcript = f"Attendees: {attendee_label}\n\n{transcript}"

    content_hash = hashlib.sha256(full_transcript.encode()).digest()
    meeting = Meeting(
        title=title,
        meeting_date=parsed_date,
        duration_minutes=duration_minutes,
        source_tool=source_tool,
        external_id=external_id,
        content=full_transcript,
        sha256=content_hash,
        tags=["meeting"] + (tags or []),
        extraction_status="processing",
        transcript_account_id=transcript_account_id,
    )
    session.add(meeting)
    session.flush()
    logger.info(f"Created meeting {meeting.id}")
    return meeting


def prepare_meeting_for_reingest(session: DBSession, item_id: int) -> Meeting | None:
    """
    Fetch an existing meeting and clear its chunks/tasks for reprocessing.

    Clears chunks and action item tasks but leaves people relationships intact
    (they're still valuable and will be re-linked during processing).
    """
    meeting = session.get(Meeting, item_id)
    if not meeting:
        return None

    chunks_deleted = clear_item_chunks(meeting, session)
    tasks_detached = detach_meeting_tasks(session, meeting)
    logger.info(
        f"Prepared meeting {item_id} for reingest: "
        f"cleared {chunks_deleted} chunks, detached {tasks_detached} tasks"
    )
    return meeting


def execute_meeting_processing(
    session: DBSession,
    meeting: Meeting,
    attendee_names: list[str] | None,
    extraction_prompt: str | None,
    system_prompt: str | None,
    model: str | None,
    job_id: int | None,
    attendee_emails: list[str] | None = None,
) -> dict:
    """
    Run the full processing pipeline on a meeting.

    This is the shared processing step for both ingest and reingest:
    1. Extract summary, notes, and action items via LLM
    2. Link attendees to Person records
    3. Generate embeddings

    Args:
        session: Database session
        meeting: Meeting record (new or existing with chunks cleared)
        attendee_names: Optional attendee names to link
        extraction_prompt: Custom extraction prompt
        system_prompt: Custom system prompt for LLM
        model: LLM model to use
        job_id: Optional job ID for status tracking

    Returns:
        Dict with processing results
    """
    try:
        extract_result = extract_and_update_meeting(
            session, meeting, extraction_prompt, system_prompt, model
        )

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

        attendee_result = {"linked": 0, "created": 0, "skipped": []}
        if attendee_names or attendee_emails:
            attendee_result = link_attendees(
                session,
                meeting,
                attendee_names=attendee_names or [],
                attendee_emails=attendee_emails or [],
            )

        session.commit()
        session.refresh(meeting)
        embed_result = process_content_item(meeting, session)

        # Only mark complete after both extraction AND embedding succeed
        meeting.extraction_status = "complete"
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
        logger.exception(f"Failed to process meeting {meeting.id}: {e}")
        # Only mark as "failed" if extraction itself failed (status still "processing")
        # If status is "extracted", extraction succeeded but embedding failed - preserve that
        if meeting.extraction_status == "processing":
            meeting.extraction_status = "failed"
        # If "extracted", leave it so we know extraction worked but embedding didn't
        if job_id:
            job_utils.fail_job(session, job_id, str(e))
        session.commit()
        return {"status": "error", "error": str(e), "meeting_id": meeting.id}


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
    extraction_prompt: str | None = None,
    system_prompt: str | None = None,
    model: str | None = None,
    job_id: int | None = None,
    attendee_emails: list[str] | None = None,
    transcript_account_id: int | None = None,
):
    """
    Process a new meeting transcript.

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
        extraction_prompt: Custom extraction prompt
        system_prompt: Custom system prompt
        model: LLM model to use
        job_id: Optional job ID for status tracking
    """
    logger.info(f"Processing new meeting (external_id={external_id}, job_id={job_id})")

    with make_session() as session:
        if job_id:
            job_utils.start_job(session, job_id)
            session.commit()

        # Idempotency check via external_id
        if external_id:
            existing = (
                session.query(Meeting)
                .filter(Meeting.external_id == external_id)
                .first()
            )
            if existing:
                logger.info(f"Meeting with external_id={external_id} already exists")
                if job_id:
                    job_utils.complete_job(
                        session, job_id, result_id=existing.id, result_type="Meeting"
                    )
                    session.commit()
                return {
                    "status": "exists",
                    "meeting_id": existing.id,
                    "external_id": external_id,
                }

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
        )

        return execute_meeting_processing(
            session,
            meeting,
            attendee_names,
            extraction_prompt,
            system_prompt,
            model,
            job_id,
            attendee_emails=attendee_emails,
        )


@app.task(name=REPROCESS_MEETING)
@safe_task_execution
def reprocess_meeting(
    item_id: int,
    extraction_prompt: str | None = None,
    system_prompt: str | None = None,
    model: str | None = None,
    job_id: int | None = None,
):
    """
    Reprocess an existing meeting.

    Fetches the meeting, clears chunks and detaches tasks, then re-runs
    the full processing pipeline (LLM extraction + embeddings).

    Args:
        item_id: ID of the meeting to reprocess
        extraction_prompt: Custom extraction prompt
        system_prompt: Custom system prompt
        model: LLM model to use
        job_id: Optional job ID for status tracking
    """
    logger.info(f"Reprocessing meeting {item_id} (job_id={job_id})")

    with make_session() as session:
        if job_id:
            job_utils.start_job(session, job_id)
            session.commit()

        meeting = prepare_meeting_for_reingest(session, item_id)
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

        return execute_meeting_processing(
            session,
            meeting,
            attendee_names,
            extraction_prompt,
            system_prompt,
            model,
            job_id,
        )


# Timeout for stuck meetings (1 hour - if processing takes longer, something is wrong)
STUCK_MEETING_TIMEOUT_HOURS = 1


@app.task(name=CLEANUP_STUCK_MEETINGS)
@safe_task_execution
def cleanup_stuck_meetings() -> dict:
    """
    Reset meetings stuck in 'processing' status due to worker crashes.

    This task should be run periodically (e.g., every hour) to clean up
    meetings that got stuck when a worker crashed mid-processing.

    Returns:
        dict with count of reset meetings
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=STUCK_MEETING_TIMEOUT_HOURS)

    with make_session() as session:
        # Find meetings that have been 'processing' for too long
        stuck_meetings = (
            session.query(Meeting)
            .filter(
                Meeting.extraction_status == "processing",
                Meeting.updated_at < cutoff,
            )
            .all()
        )

        count = 0
        for meeting in stuck_meetings:
            logger.warning(
                f"Resetting stuck meeting {meeting.id} "
                f"(stuck since {meeting.updated_at})"
            )
            meeting.extraction_status = "pending"
            count += 1

        session.commit()

        if count > 0:
            logger.info(f"Reset {count} stuck meetings")

        return {"reset_count": count}
