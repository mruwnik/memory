"""
Celery tasks for tracking people.
"""

import logging

from memory.common import settings
from memory.common.db.connection import make_session
from memory.common.db.models import Person
from memory.common.celery_app import app, SYNC_PERSON, UPDATE_PERSON, SYNC_PROFILE_FROM_FILE
from memory.common.content_processing import (
    check_content_exists,
    create_content_hash,
    create_task_result,
    process_content_item,
    safe_task_execution,
)
from memory.workers.tasks.notes import git_tracking

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


def _save_profile_note(person_id: int, save_to_file: bool = True) -> None:
    """Save person data to profile note file with git tracking."""
    if not save_to_file:
        return

    with make_session() as session:
        person = session.get(Person, person_id)
        if not person:
            logger.warning(f"Person not found for profile save: {person_id}")
            return

        profile_path = person.get_profile_path()
        with git_tracking(
            settings.NOTES_STORAGE_DIR,
            f"Sync profile {profile_path}: {person.display_name}",
        ):
            person.save_profile_note()


@app.task(name=SYNC_PERSON)
@safe_task_execution
def sync_person(
    identifier: str,
    display_name: str,
    aliases: list[str] | None = None,
    contact_info: dict | None = None,
    tags: list[str] | None = None,
    notes: str | None = None,
    save_to_file: bool = True,
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
        save_to_file: Whether to save to profile note file (default True)
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

        result = process_content_item(person, session)

    # Save profile note outside transaction (git operations are slow)
    person_id = result.get("person_id")
    if result.get("status") == "processed" and isinstance(person_id, int):
        _save_profile_note(person_id, save_to_file)

    return result


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
    replace_tags: bool = False,
    replace_aliases: bool = False,
    save_to_file: bool = True,
):
    """
    Update a person with configurable merge/replace semantics.

    Merge behavior (default):
    - display_name: Replaces if provided
    - aliases: Union with existing (or replace if replace_aliases=True)
    - contact_info: Deep merge with existing
    - tags: Union with existing (or replace if replace_tags=True)
    - notes: Append to existing (or replace if replace_notes=True)

    Args:
        replace_notes: If True, replace notes instead of appending
        replace_tags: If True, replace all tags instead of merging
        replace_aliases: If True, replace all aliases instead of merging
        save_to_file: Whether to save to profile note file (default True)
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
            if replace_aliases:
                person.aliases = list(aliases)
            else:
                existing_aliases = set(person.aliases or [])
                new_aliases = existing_aliases | set(aliases)
                person.aliases = list(new_aliases)

        if contact_info is not None:
            existing_contact = dict(person.contact_info or {})
            person.contact_info = _deep_merge(existing_contact, contact_info)

        if tags is not None:
            if replace_tags:
                person.tags = list(tags)
            else:
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

        result = process_content_item(person, session)

    # Save profile note outside transaction (git operations are slow)
    person_id = result.get("person_id")
    if result.get("status") == "processed" and isinstance(person_id, int):
        _save_profile_note(person_id, save_to_file)

    return result


@app.task(name=SYNC_PROFILE_FROM_FILE)
@safe_task_execution
def sync_profile_from_file(filename: str):
    """
    Sync a profile note file to a Person record.

    Reads a markdown file with YAML frontmatter and creates/updates
    the corresponding Person record. Does NOT save back to file
    to avoid infinite loops.

    Args:
        filename: Relative path to the profile file (e.g., "profiles/john_doe.md")
    """
    file_path = settings.NOTES_STORAGE_DIR / filename
    if not file_path.exists():
        logger.warning(f"Profile file not found: {filename}")
        return {"status": "not_found", "filename": filename}

    content = file_path.read_text()
    data = Person.from_profile_markdown(content)

    if "identifier" not in data:
        # Try to infer identifier from filename
        stem = file_path.stem  # e.g., "john_doe" from "profiles/john_doe.md"
        data["identifier"] = stem

    if "display_name" not in data:
        # Use identifier as display name if not provided
        data["display_name"] = data["identifier"].replace("_", " ").title()

    identifier = data["identifier"]
    logger.info(f"Syncing profile from file: {filename} -> {identifier}")

    with make_session() as session:
        person = session.query(Person).filter(Person.identifier == identifier).first()

        if person:
            # Update existing person with merge semantics
            if "display_name" in data:
                person.display_name = data["display_name"]
            if "aliases" in data:
                existing_aliases = set(person.aliases or [])
                new_aliases = existing_aliases | set(data["aliases"])
                person.aliases = list(new_aliases)
            if "contact_info" in data:
                existing_contact = dict(person.contact_info or {})
                person.contact_info = _deep_merge(existing_contact, data["contact_info"])
            if "tags" in data:
                existing_tags = set(person.tags or [])
                new_tags = existing_tags | set(data["tags"])
                person.tags = list(new_tags)
            if "notes" in data:
                # Replace notes from file (file is source of truth)
                person.content = data["notes"]

            person.sha256 = create_content_hash(f"person:{identifier}")
            person.size = len(person.content or "")
            person.embed_status = "RAW"

            return process_content_item(person, session)
        else:
            # Create new person
            sha256 = create_content_hash(f"person:{identifier}")
            person = Person(
                identifier=identifier,
                display_name=data.get("display_name", identifier),
                aliases=data.get("aliases", []),
                contact_info=data.get("contact_info", {}),
                tags=data.get("tags", []),
                content=data.get("notes"),
                modality="person",
                mime_type="text/plain",
                sha256=sha256,
                size=len(data.get("notes") or ""),
            )

            return process_content_item(person, session)
