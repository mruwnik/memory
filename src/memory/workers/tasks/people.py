"""
Celery tasks for tracking people.
"""

import logging

from memory.common.db.connection import make_session
from memory.common.db.models import Person
from memory.common.celery_app import app, SYNC_PERSON, UPDATE_PERSON
from memory.workers.tasks.content_processing import (
    check_content_exists,
    create_content_hash,
    create_task_result,
    process_content_item,
    safe_task_execution,
)

logger = logging.getLogger(__name__)


def _deep_merge(base: dict, updates: dict) -> dict:
    """Deep merge two dictionaries, with updates taking precedence."""
    result = dict(base)
    for key, value in updates.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


@app.task(name=SYNC_PERSON)
@safe_task_execution
def sync_person(
    identifier: str,
    display_name: str,
    aliases: list[str] | None = None,
    contact_info: dict | None = None,
    tags: list[str] | None = None,
    notes: str | None = None,
):
    """
    Create or update a person in the knowledge base.

    Args:
        identifier: Unique slug for the person
        display_name: Human-readable name
        aliases: Alternative names/handles
        contact_info: Contact information dict
        tags: Categorization tags
        notes: Free-form notes about the person
    """
    logger.info(f"Syncing person: {identifier}")

    # Create hash from identifier for deduplication
    sha256 = create_content_hash(f"person:{identifier}")

    with make_session() as session:
        # Check if person already exists by identifier
        existing = session.query(Person).filter(Person.identifier == identifier).first()

        if existing:
            logger.info(f"Person already exists: {identifier}")
            return create_task_result(existing, "already_exists")

        # Also check by sha256 (defensive)
        existing_by_hash = check_content_exists(session, Person, sha256=sha256)
        if existing_by_hash:
            logger.info(f"Person already exists (by hash): {identifier}")
            return create_task_result(existing_by_hash, "already_exists")

        person = Person(
            identifier=identifier,
            display_name=display_name,
            aliases=aliases or [],
            contact_info=contact_info or {},
            tags=tags or [],
            content=notes,
            modality="person",
            mime_type="text/plain",
            sha256=sha256,
            size=len(notes or ""),
        )

        return process_content_item(person, session)


@app.task(name=UPDATE_PERSON)
@safe_task_execution
def update_person(
    identifier: str,
    display_name: str | None = None,
    aliases: list[str] | None = None,
    contact_info: dict | None = None,
    tags: list[str] | None = None,
    notes: str | None = None,
    replace_notes: bool = False,
):
    """
    Update a person with merge semantics.

    Merge behavior:
    - display_name: Replaces if provided
    - aliases: Union with existing
    - contact_info: Deep merge with existing
    - tags: Union with existing
    - notes: Append to existing (or replace if replace_notes=True)
    """
    logger.info(f"Updating person: {identifier}")

    with make_session() as session:
        person = session.query(Person).filter(Person.identifier == identifier).first()
        if not person:
            logger.warning(f"Person not found: {identifier}")
            return {"status": "not_found", "identifier": identifier}

        if display_name is not None:
            person.display_name = display_name

        if aliases is not None:
            existing_aliases = set(person.aliases or [])
            new_aliases = existing_aliases | set(aliases)
            person.aliases = list(new_aliases)

        if contact_info is not None:
            existing_contact = dict(person.contact_info or {})
            person.contact_info = _deep_merge(existing_contact, contact_info)

        if tags is not None:
            existing_tags = set(person.tags or [])
            new_tags = existing_tags | set(tags)
            person.tags = list(new_tags)

        if notes is not None:
            if replace_notes or not person.content:
                person.content = notes
            else:
                person.content = f"{person.content}\n\n---\n\n{notes}"

        # Update hash based on new content
        person.sha256 = create_content_hash(f"person:{identifier}")
        person.size = len(person.content or "")
        person.embed_status = "RAW"  # Re-embed with updated content

        return process_content_item(person, session)
