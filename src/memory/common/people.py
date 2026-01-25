"""Utilities for finding and creating Person records.

This module provides shared logic for matching names/emails to existing Person
records and optionally creating new ones. Used by:
- Meeting transcript processing (attendee linking)
- Slack sync (workspace user linking)
- Any other integration that needs to map external users to People
"""

import logging
import re
from typing import TYPE_CHECKING

from sqlalchemy import func as sql_func
from sqlalchemy.orm import Session, scoped_session

from memory.common.content_processing import create_content_hash
from memory.common.db.models.people import Person

if TYPE_CHECKING:
    from memory.common.db.models.source_item import SourceItem

logger = logging.getLogger(__name__)

# Type alias for session types - scoped_session behaves like Session at runtime
DBSession = Session | scoped_session[Session]


def make_identifier(name: str) -> str:
    """Create a person identifier from a name (e.g. 'John Smith' -> 'john_smith')."""
    identifier = re.sub(r"\s+", "_", name.lower().strip())
    return "".join(c for c in identifier if c.isalnum() or c == "_")


def find_person_by_name(session: DBSession, name: str | None) -> Person | None:
    """Try to find a Person record by name, alias, or email.

    Matching order:
    1. Exact match on display_name (case-insensitive)
    2. Match in aliases array (case-insensitive)
    3. If input looks like an email, match in contact_info["email"]

    Args:
        session: Database session
        name: Name or email to search for

    Returns:
        Matching Person or None
    """
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


def find_person_by_email(session: DBSession, email: str | None) -> Person | None:
    """Find a Person by their email address.

    Args:
        session: Database session
        email: Email address to search for

    Returns:
        Matching Person or None
    """
    if not email:
        return None

    email_lower = email.lower().strip()
    return (
        session.query(Person)
        .filter(Person.contact_info["email"].astext.ilike(email_lower))
        .first()
    )


def find_person_by_slack_id(
    session: DBSession, workspace_id: str, slack_user_id: str
) -> Person | None:
    """Find a Person by their Slack user ID in a specific workspace.

    Slack user info is stored in contact_info["slack"][workspace_id]["user_id"].

    Args:
        session: Database session
        workspace_id: Slack workspace/team ID
        slack_user_id: Slack user ID (e.g., "U123ABC")

    Returns:
        Matching Person or None
    """
    # Query for people with Slack info for this workspace
    people = (
        session.query(Person)
        .filter(Person.contact_info["slack"].isnot(None))
        .all()
    )

    for person in people:
        slack_info = person.contact_info.get("slack", {})
        workspace_info = slack_info.get(workspace_id, {})
        if workspace_info.get("user_id") == slack_user_id:
            return person

    return None


def find_or_create_person(
    session: DBSession,
    name: str,
    email: str | None = None,
    create_if_missing: bool = True,
) -> tuple[Person | None, bool]:
    """Find existing person or optionally create new one.

    Matching order:
    1. By email (if provided)
    2. By name/alias
    3. By identifier

    Args:
        session: Database session
        name: Display name for the person
        email: Optional email for matching/creation
        create_if_missing: If True, create a new Person when no match found

    Returns:
        Tuple of (Person or None, was_created)
    """
    # Try email first if provided
    if email:
        person = find_person_by_email(session, email)
        if person:
            return person, False

    # Try name/alias match
    person = find_person_by_name(session, name)
    if person:
        return person, False

    # Check if identifier already exists
    identifier = make_identifier(name)
    existing = session.query(Person).filter(Person.identifier == identifier).first()
    if existing:
        return existing, False

    if not create_if_missing:
        return None, False

    # Create new person
    sha256 = create_content_hash(f"person:{identifier}")
    person = Person(
        identifier=identifier,
        display_name=name,
        aliases=[name],
        contact_info={"email": email} if email else {},
        modality="person",
        mime_type="text/plain",
        sha256=sha256,
        size=0,
    )
    session.add(person)
    session.flush()
    logger.info(f"Created person '{identifier}' for name '{name}'")
    return person, True


def find_person(session: DBSession, identifier: str | None) -> Person | None:
    """Find a Person by trying multiple lookup strategies.

    Tries in order:
    1. By email (if identifier contains @)
    2. By name/alias
    3. By identifier slug

    Args:
        session: Database session
        identifier: Email, name, or identifier to search for

    Returns:
        Matching Person or None
    """
    if not identifier:
        return None

    # Try email lookup first if it looks like an email
    if "@" in identifier:
        if person := find_person_by_email(session, identifier):
            return person

    # Try name/alias lookup
    if person := find_person_by_name(session, identifier):
        return person

    # Try identifier slug lookup
    slug = make_identifier(identifier)
    return session.query(Person).filter(Person.identifier == slug).first()


def link_people(
    session: DBSession,
    source_item: "SourceItem",
    identifiers: set[str] | list[str],
    create_if_missing: bool = False,
) -> int:
    """Link Person records to a SourceItem based on identifiers.

    For each identifier, tries to find a matching Person by email, name,
    or identifier slug. Skips duplicates and None values.

    Args:
        session: Database session for person lookup
        source_item: The SourceItem to link people to
        identifiers: Collection of emails, names, or identifiers to look up
        create_if_missing: If True, create Person records for unmatched identifiers

    Returns:
        Number of people linked
    """
    linked = 0
    for identifier in identifiers:
        if not identifier:
            continue

        person = find_person(session, identifier)

        # Optionally create if not found and we have enough info
        if not person and create_if_missing:
            email = identifier if "@" in identifier else None
            name = identifier if "@" not in identifier else identifier.split("@")[0]
            person, _ = find_or_create_person(session, name=name, email=email)

        if person and person not in source_item.people:
            source_item.people.append(person)
            linked += 1

    return linked


def link_slack_user_to_person(
    session: DBSession,
    person: Person,
    workspace_id: str,
    slack_user_id: str,
    slack_username: str | None = None,
    slack_display_name: str | None = None,
) -> None:
    """Link a Slack user to an existing Person record.

    Stores Slack user info in contact_info["slack"][workspace_id].

    Args:
        session: Database session
        person: Person to link
        workspace_id: Slack workspace/team ID
        slack_user_id: Slack user ID
        slack_username: Optional Slack username
        slack_display_name: Optional Slack display name
    """
    if not person.contact_info:
        person.contact_info = {}

    slack_info = person.contact_info.get("slack", {})
    slack_info[workspace_id] = {
        "user_id": slack_user_id,
        "username": slack_username,
        "display_name": slack_display_name,
    }
    person.contact_info = {**person.contact_info, "slack": slack_info}
    session.flush()
    logger.info(
        f"Linked Slack user {slack_user_id} to person '{person.identifier}' "
        f"in workspace {workspace_id}"
    )


def sync_slack_users_to_people(
    session: DBSession,
    workspace_id: str,
    users: list[dict],
    create_missing: bool = False,
) -> dict:
    """Sync Slack workspace users to Person records.

    For each Slack user:
    1. Try to match by existing Slack ID link
    2. Try to match by email
    3. Try to match by name
    4. Optionally create new Person if no match

    Args:
        session: Database session
        workspace_id: Slack workspace/team ID
        users: List of Slack user dicts from users.list API
        create_missing: If True, create Person records for unmatched users

    Returns:
        Dict with counts: {"matched": N, "created": N, "skipped": N}
    """
    matched = 0
    created = 0
    skipped = 0

    for user in users:
        user_id = user.get("id")
        if not user_id:
            continue

        # Skip bots and deleted users
        if user.get("is_bot") or user.get("deleted"):
            continue

        profile = user.get("profile", {})
        email = profile.get("email")
        display_name = (
            profile.get("display_name")
            or profile.get("real_name")
            or user.get("name")
        )

        if not display_name:
            skipped += 1
            continue

        # Check if already linked
        existing = find_person_by_slack_id(session, workspace_id, user_id)
        if existing:
            matched += 1
            continue

        # Try to find by email or name
        person, was_created = find_or_create_person(
            session,
            name=display_name,
            email=email,
            create_if_missing=create_missing,
        )

        if person:
            # Link Slack user to person
            link_slack_user_to_person(
                session,
                person,
                workspace_id,
                user_id,
                slack_username=user.get("name"),
                slack_display_name=display_name,
            )
            if was_created:
                created += 1
            else:
                matched += 1
        else:
            skipped += 1

    session.commit()
    logger.info(
        f"Slack user sync for workspace {workspace_id}: "
        f"{matched} matched, {created} created, {skipped} skipped"
    )
    return {"matched": matched, "created": created, "skipped": skipped}
