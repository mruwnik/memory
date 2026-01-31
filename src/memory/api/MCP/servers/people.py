"""MCP subserver for tracking people."""

import logging
from typing import Any

from fastmcp import FastMCP
from sqlalchemy import Text, text
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import ARRAY

from memory.api.MCP.visibility import require_scopes, visible_when
from memory.common import settings
from memory.common.celery_app import SYNC_PERSON, UPDATE_PERSON
from memory.common.celery_app import app as celery_app
from memory.common.db.connection import make_session
from memory.common.db.models import Person

logger = logging.getLogger(__name__)

people_mcp = FastMCP("memory-people")


def _person_to_dict(person: Person) -> dict[str, Any]:
    """Convert a Person model to a dictionary for API responses."""
    result = {
        "identifier": person.identifier,
        "display_name": person.display_name,
        "aliases": list(person.aliases or []),
        "contact_info": dict(person.contact_info or {}),
        "tags": list(person.tags or []),
        "notes": person.content,
        "created_at": person.inserted_at.isoformat() if person.inserted_at else None,
    }
    if person.user_id:
        result["user_id"] = person.user_id
        if person.user:
            result["user_email"] = person.user.email
            result["user_name"] = person.user.name
    return result


@people_mcp.tool()
@visible_when(require_scopes("people"))
async def add(
    identifier: str,
    display_name: str,
    aliases: list[str] | None = None,
    contact_info: dict | None = None,
    tags: list[str] | None = None,
    notes: str | None = None,
) -> dict:
    """
    Add a new person to track.

    Args:
        identifier: Unique slug for the person (e.g., "alice_chen")
        display_name: Human-readable name (e.g., "Alice Chen")
        aliases: Alternative names/handles (e.g., ["@alice_c", "alice.chen@work.com"])
        contact_info: Contact information as a dict (e.g., {"email": "...", "phone": "..."})
        tags: Categorization tags (e.g., ["work", "friend", "climbing"])
        notes: Free-form notes about the person

    Returns:
        Task status with task_id

    Example:
        add_person(
            identifier="alice_chen",
            display_name="Alice Chen",
            aliases=["@alice_c"],
            contact_info={"email": "alice@example.com"},
            tags=["work", "engineering"],
            notes="Tech lead on Platform team"
        )
    """
    logger.info(f"MCP: Adding person: {identifier}")

    with make_session() as session:
        existing = session.query(Person).filter(Person.identifier == identifier).first()
        if existing:
            raise ValueError(f"Person with identifier '{identifier}' already exists")

    task = celery_app.send_task(
        SYNC_PERSON,
        queue=f"{settings.CELERY_QUEUE_PREFIX}-people",
        kwargs={
            "identifier": identifier,
            "display_name": display_name,
            "aliases": aliases,
            "contact_info": contact_info,
            "tags": tags,
            "notes": notes,
        },
    )

    return {
        "task_id": task.id,
        "status": "queued",
        "identifier": identifier,
    }


@people_mcp.tool()
@visible_when(require_scopes("people"))
async def update(
    identifier: str,
    display_name: str | None = None,
    aliases: list[str] | None = None,
    contact_info: dict | None = None,
    tags: list[str] | None = None,
    notes: str | None = None,
    replace_notes: bool = False,
    replace_tags: bool = False,
    replace_aliases: bool = False,
) -> dict:
    """
    Update information about a person with configurable merge/replace semantics.

    By default, this tool MERGES new information with existing data:
    - display_name: Always replaces existing value
    - aliases: Adds new aliases (union with existing), or replaces if replace_aliases=True
    - contact_info: Deep merges (adds new keys, updates existing keys, never deletes)
    - tags: Adds new tags (union with existing), or replaces if replace_tags=True
    - notes: Appends to existing notes, or replaces if replace_notes=True

    Args:
        identifier: The person's unique identifier
        display_name: New display name (replaces existing)
        aliases: Aliases to add (or replace existing if replace_aliases=True)
        contact_info: Additional contact info to merge
        tags: Tags to add (or replace existing if replace_tags=True)
        notes: Notes to append (or replace if replace_notes=True)
        replace_notes: If True, replace notes instead of appending
        replace_tags: If True, replace all tags instead of merging
        replace_aliases: If True, replace all aliases instead of merging

    Returns:
        Task status with task_id

    Example:
        # Add new contact info without losing existing data
        update(
            identifier="alice_chen",
            contact_info={"phone": "555-1234"},  # Added to existing
            notes="Enjoys rock climbing"  # Appended to existing notes
        )

        # Replace all tags (useful for removing tags)
        update(
            identifier="alice_chen",
            tags=["work"],  # Replaces all existing tags
            replace_tags=True
        )
    """
    logger.info(f"MCP: Updating person: {identifier}")

    with make_session() as session:
        person = session.query(Person).filter(Person.identifier == identifier).first()
        if not person:
            raise ValueError(f"Person with identifier '{identifier}' not found")

    task = celery_app.send_task(
        UPDATE_PERSON,
        queue=f"{settings.CELERY_QUEUE_PREFIX}-people",
        kwargs={
            "identifier": identifier,
            "display_name": display_name,
            "aliases": aliases,
            "contact_info": contact_info,
            "tags": tags,
            "notes": notes,
            "replace_notes": replace_notes,
            "replace_tags": replace_tags,
            "replace_aliases": replace_aliases,
        },
    )

    return {
        "task_id": task.id,
        "status": "queued",
        "identifier": identifier,
    }


@people_mcp.tool()
@visible_when(require_scopes("people"))
async def get_person(identifier: str) -> dict | None:
    """
    Get a person by their identifier.

    Args:
        identifier: The person's unique identifier

    Returns:
        The person record, or None if not found
    """
    logger.info(f"MCP: Getting person: {identifier}")

    with make_session() as session:
        # First try exact identifier match
        person = session.query(Person).filter(Person.identifier == identifier).first()
        if not person:
            # Fall back to searching aliases
            person = session.query(Person).filter(Person.aliases.contains([identifier])).first()  # type: ignore[union-attr]
        if not person:
            return None
        return _person_to_dict(person)


@people_mcp.tool()
@visible_when(require_scopes("people"))
async def list_people(
    tags: list[str] | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """
    List all tracked people, optionally filtered by tags or search term.

    Args:
        tags: Filter to people with at least one of these tags
        search: Search term to match against name, aliases, or notes
        limit: Maximum number of results (default 50, max 200)
        offset: Number of results to skip for pagination (default 0, max 10000)

    Returns:
        List of person records matching the filters
    """
    logger.info(f"MCP: Listing people (tags={tags}, search={search})")

    limit = min(max(limit, 1), 200)
    offset = min(max(offset, 0), 10000)

    with make_session() as session:
        query = session.query(Person)

        if tags:
            query = query.filter(Person.tags.op("&&")(sql_cast(tags, ARRAY(Text))))  # type: ignore[union-attr]

        if search:
            search_term = f"%{search.lower()}%"
            # Search in aliases array using EXISTS with unnest for ILIKE matching
            # We use a raw SQL fragment for the array search since SQLAlchemy
            # doesn't have great support for unnest + ILIKE
            alias_match = text(
                "EXISTS (SELECT 1 FROM unnest(aliases) AS alias WHERE alias ILIKE :search_pattern)"
            ).bindparams(search_pattern=search_term)
            query = query.filter(
                (Person.display_name.ilike(search_term))  # type: ignore[union-attr]
                | (Person.content.ilike(search_term))  # type: ignore[union-attr]
                | (Person.identifier.ilike(search_term))  # type: ignore[union-attr]
                | alias_match
            )

        query = query.order_by(Person.display_name).offset(offset).limit(limit)
        people = query.all()

        return [_person_to_dict(p) for p in people]


@people_mcp.tool()
@visible_when(require_scopes("people"))
async def delete(identifier: str) -> dict:
    """
    Delete a person by their identifier.

    This permanently removes the person and all associated data.
    Observations about this person (with subject "person:<identifier>") will remain.

    Args:
        identifier: The person's unique identifier

    Returns:
        Confirmation of deletion
    """
    logger.info(f"MCP: Deleting person: {identifier}")

    with make_session() as session:
        person = session.query(Person).filter(Person.identifier == identifier).first()
        if not person:
            raise ValueError(f"Person with identifier '{identifier}' not found")

        display_name = person.display_name
        session.delete(person)
        session.commit()

        return {
            "deleted": True,
            "identifier": identifier,
            "display_name": display_name,
        }
