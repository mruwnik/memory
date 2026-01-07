"""Celery tasks for meeting transcript processing."""

import hashlib
import json
import logging
from datetime import datetime

from dateutil import parser as date_parser
from sqlalchemy import func as sql_func

from memory.common import llms, settings
from memory.common.db.connection import make_session
from memory.common.db.models import Task
from memory.common.db.models.source_items import Meeting
from memory.common.db.models.people import Person
from memory.common.celery_app import app, PROCESS_MEETING
from memory.workers.tasks.content_processing import (
    process_content_item,
    safe_task_execution,
)

logger = logging.getLogger(__name__)

DEFAULT_EXTRACTION_PROMPT = """You are analyzing a meeting transcript. Extract the following information:

1. A concise 2-3 sentence summary of the meeting's main purpose and outcomes
2. Key discussion points, decisions made, and important information (as bullet points)
3. Action items - tasks that were assigned or need to be done, with assignee, due date, and priority if mentioned

Return your analysis as JSON with this exact structure:
{
    "summary": "Brief summary of the meeting",
    "notes": "- Key point 1\\n- Key point 2\\n- Decision made\\n- etc.",
    "action_items": [
        {
            "description": "Task description",
            "assignee": "Person name or null if not specified",
            "due_date": "YYYY-MM-DD or null if not specified",
            "priority": "low, medium, high, or urgent based on context, or null if unclear"
        }
    ]
}

IMPORTANT: Return ONLY valid JSON, no markdown formatting or extra text.

Meeting transcript:
{transcript}
"""

DEFAULT_SYSTEM_PROMPT = "You are an expert at analyzing meeting transcripts and extracting actionable information. Always return valid JSON."


def parse_extraction_response(response: str) -> dict:
    """Parse the JSON response from the LLM."""
    # Try to extract JSON if wrapped in markdown code blocks
    response = response.strip()
    if response.startswith("```"):
        # Remove markdown code block
        lines = response.split("\n")
        # Skip first line (```json or ```) and last line (```)
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
        response = "\n".join(json_lines)

    try:
        return json.loads(response)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse extraction response: {e}")
        logger.error(f"Response was: {response[:500]}")
        return {
            "summary": "",
            "notes": "",
            "action_items": [],
        }


def find_person_by_name(session, name: str) -> Person | None:
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

    # Try matching aliases - load people with aliases and check in Python
    # PostgreSQL array case-insensitive matching is complex, so we do a simple contains check
    people_with_aliases = (
        session.query(Person)
        .filter(Person.aliases.isnot(None))
        .filter(sql_func.array_length(Person.aliases, 1) > 0)
        .all()
    )
    for person in people_with_aliases:
        if any(alias.lower() == name_lower for alias in (person.aliases or [])):
            return person

    # Check if input looks like an email and try to match in contact_info
    if "@" in name_lower:
        person = (
            session.query(Person)
            .filter(
                Person.contact_info["email"].astext.ilike(f"%{name_lower}%")
            )
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
    response = llms.summarize(
        prompt,
        model=llm_model,
        system_prompt=sys_prompt,
    )

    return parse_extraction_response(response)


def link_attendees(session, meeting: Meeting, attendee_names: list[str]) -> int:
    """Link attendee names to Person records. Returns count of linked attendees."""
    linked = 0
    for name in attendee_names:
        person = find_person_by_name(session, name)
        if person and person not in meeting.attendees:
            meeting.attendees.append(person)
            logger.info(f"Linked attendee {name} to person {person.identifier}")
            linked += 1
    return linked


def create_action_item_tasks(
    session, meeting: Meeting, action_items: list[dict]
) -> list[str]:
    """Create Task records for action items. Returns list of created task descriptions."""
    created_tasks = []

    for item in action_items:
        if not item.get("description"):
            continue

        task_tags = ["meeting", "action-item"]

        assignee_person = find_person_by_name(session, item.get("assignee"))
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
):
    """
    Create and process a meeting transcript.

    This task:
    1. Creates the Meeting record (idempotent via external_id)
    2. Calls LLM to extract structured information
    3. Updates the Meeting record with summary and notes
    4. Creates Task records for each action item
    5. Links attendees to Person records
    6. Triggers embedding generation for the meeting

    Args:
        transcript: The meeting transcript text
        title: Optional meeting title
        meeting_date: Optional meeting date (ISO format string)
        duration_minutes: Optional duration in minutes
        attendee_names: Optional list of attendee names to match to Person records
        source_tool: Source of transcript (fireflies, granola, etc.)
        external_id: External ID for idempotency
        tags: Optional tags for the meeting
        extraction_prompt: Custom extraction prompt
        system_prompt: Custom system prompt
        model: LLM model to use
    """
    logger.info(f"Processing meeting (external_id={external_id})")

    with make_session() as session:
        # Idempotency check via external_id
        if external_id:
            existing = (
                session.query(Meeting)
                .filter(Meeting.external_id == external_id)
                .first()
            )
            if existing:
                logger.info(f"Meeting with external_id={external_id} already exists (id={existing.id})")
                return {
                    "status": "exists",
                    "meeting_id": existing.id,
                    "external_id": external_id,
                }

        # Parse meeting_date if provided
        parsed_date = None
        if meeting_date:
            parsed_date = parse_due_date(meeting_date)

        # Create the Meeting record
        content_hash = hashlib.sha256(transcript.encode()).digest()
        meeting = Meeting(
            title=title,
            meeting_date=parsed_date,
            duration_minutes=duration_minutes,
            source_tool=source_tool,
            external_id=external_id,
            content=transcript,
            sha256=content_hash,
            tags=["meeting"] + (tags or []),
            extraction_status="processing",
        )
        session.add(meeting)
        session.flush()  # Get the ID without committing

        logger.info(f"Created meeting {meeting.id}")

        try:
            extracted = call_extraction_llm(
                transcript, extraction_prompt, system_prompt, model
            )

            meeting.summary = extracted.get("summary", "")
            meeting.notes = extracted.get("notes", "")
            meeting.extraction_status = "complete"

            if attendee_names:
                link_attendees(session, meeting, attendee_names)

            created_tasks = create_action_item_tasks(
                session, meeting, extracted.get("action_items", [])
            )

            session.commit()
            logger.info(f"Created {len(created_tasks)} tasks from meeting {meeting.id}")

            session.refresh(meeting)
            result = process_content_item(meeting, session)

            return {
                "status": "success",
                "meeting_id": meeting.id,
                "external_id": external_id,
                "summary_length": len(meeting.summary or ""),
                "notes_length": len(meeting.notes or ""),
                "tasks_created": len(created_tasks),
                "attendees_linked": len(meeting.attendees),
                "embedding_result": result,
            }

        except Exception as e:
            logger.exception(f"Failed to process meeting: {e}")
            meeting.extraction_status = "failed"
            session.commit()
            return {"status": "error", "message": str(e)}
