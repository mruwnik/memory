"""Celery tasks for meeting transcript processing."""

import hashlib
import json
import logging
from collections.abc import Sequence
from datetime import datetime

from dateutil import parser as date_parser
from sqlalchemy import func as sql_func

from memory.common import llms, settings, jobs as job_utils
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import Task
from memory.common.db.models.source_items import Meeting
from memory.common.db.models.people import Person
from memory.common.celery_app import app, PROCESS_MEETING, REPROCESS_MEETING
from memory.common.content_processing import (
    clear_item_chunks,
    create_content_hash,
    process_content_item,
    safe_task_execution,
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


def find_person_by_name(session, name: str | None) -> Person | None:
    """Try to find a Person record by name, alias, or email."""
    if not name:
        return None

    name_lower = name.lower().strip()

    # Try exact match on display_name first
    person = (
        session.query(Person)
        .filter(sql_func.lower(Person.display_name) == name_lower)
        .first()
    )
    if person:
        return person

    # Try matching aliases
    people_with_aliases = (
        session.query(Person)
        .filter(Person.aliases.isnot(None))
        .filter(sql_func.array_length(Person.aliases, 1) > 0)
        .all()
    )
    for person in people_with_aliases:
        if any(alias.lower() == name_lower for alias in (person.aliases or [])):
            return person

    # Check if input looks like an email
    if "@" in name_lower:
        person = (
            session.query(Person)
            .filter(Person.contact_info["email"].astext.ilike(f"%{name_lower}%"))
            .first()
        )
        if person:
            return person

    return None


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


def _make_identifier(name: str) -> str:
    """Create a person identifier from a name (e.g. 'John Smith' -> 'john_smith')."""
    import re

    identifier = re.sub(r"\s+", "_", name.lower().strip())
    return "".join(c for c in identifier if c.isalnum() or c == "_")


def _find_or_create_person(session, name: str) -> tuple[Person, bool]:
    """Find existing person or create new one. Returns (person, was_created)."""
    person = find_person_by_name(session, name)
    if person:
        return person, False

    identifier = _make_identifier(name)
    existing = session.query(Person).filter(Person.identifier == identifier).first()
    if existing:
        return existing, False

    sha256 = create_content_hash(f"person:{identifier}")
    person = Person(
        identifier=identifier,
        display_name=name,
        aliases=[name],
        modality="person",
        mime_type="text/plain",
        sha256=sha256,
        size=0,
    )
    session.add(person)
    session.flush()
    return person, True


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


def link_attendees(
    session, meeting: Meeting, attendee_names: Sequence[str | None], create_missing: bool = True
) -> dict:
    """Link attendee names to Person records, optionally creating new ones."""
    normalized_names = normalize_attendee_names(attendee_names)
    logger.info(f"Processing {len(normalized_names)} attendees: {normalized_names}")

    linked, created, skipped = 0, 0, []

    for name in normalized_names:
        if not name:
            continue

        if not create_missing:
            person = find_person_by_name(session, name)
            if not person:
                logger.warning(f"Could not find person for attendee '{name}'")
                skipped.append(name)
                continue
            was_created = False
        else:
            person, was_created = _find_or_create_person(session, name)

        if person in meeting.attendees:
            continue

        meeting.attendees.append(person)
        if was_created:
            logger.info(f"Created person '{person.identifier}' for attendee '{name}'")
            created += 1
        else:
            logger.info(f"Linked attendee '{name}' to person '{person.identifier}'")
            linked += 1

    logger.info(f"Attendees: {linked} linked, {created} created, {len(skipped)} skipped")
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
        assignee_person = find_person_by_name(session, assignee_name) if assignee_name else None
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
        meeting.extraction_status = "complete"

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
) -> Meeting:
    """Create a new Meeting record from the provided data."""
    parsed_date = parse_due_date(meeting_date)

    # Prepend attendees to transcript if provided
    full_transcript = transcript
    if attendee_names:
        attendee_list = ", ".join(name for name in attendee_names if name)
        if attendee_list:
            full_transcript = f"Attendees: {attendee_list}\n\n{transcript}"

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

        attendee_result = {"linked": 0, "created": 0, "skipped": []}
        if attendee_names:
            attendee_result = link_attendees(session, meeting, attendee_names)

        session.commit()
        session.refresh(meeting)
        embed_result = process_content_item(meeting, session)

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
        meeting.extraction_status = "failed"
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
        attendee_names = [p.display_name for p in meeting.attendees] if meeting.attendees else None

        return execute_meeting_processing(
            session,
            meeting,
            attendee_names,
            extraction_prompt,
            system_prompt,
            model,
            job_id,
        )
