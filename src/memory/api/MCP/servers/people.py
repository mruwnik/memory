"""MCP subserver for tracking people."""

import logging
from datetime import datetime, timezone
from typing import Any

from fastmcp import FastMCP
from sqlalchemy import Text, or_, text
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import selectinload

from memory.api.MCP.access import (
    get_mcp_current_user,
    get_project_roles_by_user_id,
)
from memory.api.MCP.visibility import require_scopes, visible_when
from memory.common import settings
from memory.common.access_control import (
    get_accessible_project_ids,
    get_accessible_team_ids,
    has_admin_scope,
    user_can_access,
    user_can_edit,
)
from memory.common.celery_app import SYNC_PERSON_TIDBIT
from memory.common.celery_app import app as celery_app
from memory.common.db.connection import make_session
from memory.common.db.models import (
    Person,
    PersonTidbit,
    Project,
    Team,
    User,
    team_members,
)
from memory.common.db.models.discord import DiscordUser
from memory.common.db.models.polls import PollResponse
from memory.common.db.models.source_item import source_item_people
from memory.common.db.models.sources import GithubUser

logger = logging.getLogger(__name__)

people_mcp = FastMCP("memory-people")


def _person_to_dict(person: Person) -> dict[str, Any]:
    """Convert a Person model to a dictionary for API responses.

    Note: This does NOT include tidbits. Use get_person() which handles
    tidbit access filtering separately.
    """
    result = {
        "id": person.id,
        "identifier": person.identifier,
        "display_name": person.display_name,
        "aliases": list(person.aliases or []),
        "contact_info": dict(person.contact_info or {}),
        "created_at": person.created_at.isoformat() if person.created_at else None,
    }
    if person.user_id:
        result["user_id"] = person.user_id
        if person.user:
            result["user_email"] = person.user.email
            result["user_name"] = person.user.name
    return result


def _tidbit_to_dict(tidbit: PersonTidbit) -> dict[str, Any]:
    """Convert a PersonTidbit to a dictionary for API responses."""
    return {
        "id": tidbit.id,
        "person_id": tidbit.person_id,
        "person_identifier": tidbit.person.identifier if tidbit.person else None,
        "tidbit_type": tidbit.tidbit_type,
        "content": tidbit.content,
        "tags": list(tidbit.tags or []),
        "project_id": tidbit.project_id,
        "sensitivity": tidbit.sensitivity,
        "creator_id": tidbit.creator_id,
        "created_at": tidbit.inserted_at.isoformat() if tidbit.inserted_at else None,
    }


def _filter_tidbits_by_access(
    tidbits: list[PersonTidbit], user: Any, project_roles: dict[int, str] | None = None
) -> list[PersonTidbit]:
    """Filter tidbits based on user access.

    Note: This filters in-memory after fetching. If tidbits have lazy-loaded
    relationships accessed by user_can_access (like item.people), this could
    cause N+1 queries. Callers should use selectinload() when fetching tidbits
    if the 'people' relationship is needed.
    """
    if not tidbits:
        return []
    if has_admin_scope(user):
        return tidbits
    return [t for t in tidbits if user_can_access(user, t, project_roles)]


def _deep_merge(base: dict, updates: dict) -> dict:
    """Deep merge two dictionaries, with updates taking precedence."""
    result = dict(base)
    for key, value in updates.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def link_user_from_contact_info(session: Any, person: Person, contact_info: dict | None) -> int | None:
    """Link a User to a Person based on email in contact_info.

    Returns User ID that was linked, or None if no match found.
    """
    if not contact_info:
        return None

    email = contact_info.get("email")
    if not email or not isinstance(email, str):
        return None

    email = email.strip().lower()
    if not email:
        return None

    # Already linked?
    if person.user_id:
        return person.user_id

    # Find user by email
    user = session.query(User).filter(User.email.ilike(email)).first()
    if user:
        person.user_id = user.id
        logger.info(f"Linked Person {person.identifier} to User {user.email} (id={user.id})")
        return user.id

    return None


def link_discord_from_contact_info(session: Any, person: Person, contact_info: dict | None) -> list[int]:
    """Link Discord users to a Person based on contact_info.

    Returns list of Discord user IDs that were linked.
    """
    if not contact_info:
        return []

    discord_info = contact_info.get("discord")
    if not discord_info:
        return []

    # Normalize to list
    if isinstance(discord_info, str):
        discord_identifiers = [discord_info]
    elif isinstance(discord_info, list):
        discord_identifiers = discord_info
    else:
        logger.warning(f"Unexpected discord contact_info type: {type(discord_info)}")
        return []

    linked_ids = []

    for identifier in discord_identifiers:
        identifier = str(identifier).strip()
        if not identifier:
            continue

        # Try to find existing Discord user
        discord_user = None

        # Check if it's a numeric ID
        if identifier.isdigit():
            discord_user = session.get(DiscordUser, int(identifier))

        # If not found by ID, search by username/display_name
        if not discord_user:
            discord_user = (
                session.query(DiscordUser)
                .filter(
                    or_(
                        DiscordUser.username == identifier,
                        DiscordUser.display_name == identifier,
                    )
                )
                .first()
            )

        if discord_user:
            # Link to person if not already linked
            if discord_user.person_id != person.id:
                discord_user.person_id = person.id
                logger.info(
                    f"Linked Discord user {discord_user.username} ({discord_user.id}) "
                    f"to person {person.identifier}"
                )
            linked_ids.append(discord_user.id)
        else:
            logger.debug(f"Discord user not found for identifier: {identifier}")

    return linked_ids


@people_mcp.tool()
@visible_when(require_scopes("people"))
async def upsert(
    identifier: str,
    display_name: str | None = None,
    aliases: list[str] | None = None,
    contact_info: dict | None = None,
    replace_aliases: bool = False,
    content: str | None = None,
    tidbit_type: str = "note",
    tags: list[str] | None = None,
    project_id: int | None = None,
    sensitivity: str = "basic",
) -> dict:
    """
    Create or update a person.

    If the person exists, updates their identity fields. If not, creates them.
    Optionally queues a tidbit creation task if content is provided.

    Create mode (person doesn't exist):
    - display_name is required
    - Creates thin identity record

    Update mode (person exists):
    - display_name: Replaces if provided
    - aliases: Union with existing (or replace if replace_aliases=True)
    - contact_info: Deep merge with existing

    To add information/notes about a person, use tidbit_add() instead.

    Args:
        identifier: Unique slug for the person (e.g., "alice_chen")
        display_name: Human-readable name - required for create, optional for update
        aliases: Alternative names/handles (e.g., ["@alice_c", "alice.chen@work.com"])
        contact_info: Contact information as a dict (e.g., {"email": "...", "phone": "..."})
        replace_aliases: If True, replace all aliases instead of merging (update only)
        content: Optional initial note about the person
        tidbit_type: Type of initial tidbit if content provided (default: "note")
        tags: Tags for the initial tidbit (e.g., ["work", "engineering"])
        project_id: Project ID for the initial tidbit (affects visibility)
        sensitivity: Sensitivity level for the initial tidbit (default: "basic")

    Returns:
        Person data with status "created" or "updated"

    Example:
        upsert(
            identifier="alice_chen",
            display_name="Alice Chen",
            aliases=["@alice_c"],
            contact_info={"email": "alice@example.com"},
            content="Tech lead on Platform team. Prefers async communication.",
            tags=["work", "engineering"],
        )
    """
    # Get current user for creator_id
    user = get_mcp_current_user()
    creator_id = user.id if user else None

    with make_session() as session:
        existing = session.query(Person).filter(Person.identifier == identifier).first()

        if existing:
            # Update mode
            logger.info(f"MCP: Updating person: {identifier}")
            person = existing

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

            person.updated_at = datetime.now(timezone.utc)
            status = "updated"
        else:
            # Create mode - display_name is required
            logger.info(f"MCP: Creating person: {identifier}")
            if not display_name:
                raise ValueError("display_name is required when creating a new person")

            person = Person(
                identifier=identifier,
                display_name=display_name,
                aliases=aliases or [],
                contact_info=contact_info or {},
            )
            session.add(person)
            status = "created"

        session.flush()

        # Auto-link User from contact_info email
        linked_user = link_user_from_contact_info(session, person, person.contact_info)

        # Auto-link Discord users from contact_info
        linked_discord = link_discord_from_contact_info(session, person, person.contact_info)

        session.commit()

        result: dict[str, Any] = {
            "status": status,
            "person_id": person.id,
            "identifier": identifier,
        }

        if linked_user:
            result["linked_user_id"] = linked_user
        if linked_discord:
            result["linked_discord_users"] = linked_discord

    # If content provided, queue a tidbit creation task
    if content:
        task = celery_app.send_task(
            SYNC_PERSON_TIDBIT,
            queue=f"{settings.CELERY_QUEUE_PREFIX}-people",
            kwargs={
                "person_id": result["person_id"],
                "content": content,
                "tidbit_type": tidbit_type,
                "tags": tags,
                "project_id": project_id,
                "sensitivity": sensitivity,
                "creator_id": creator_id,
            },
        )
        result["tidbit_task_id"] = task.id

    return result


@people_mcp.tool()
@visible_when(require_scopes("people"))
async def fetch(
    identifier: str,
    include_tidbits: bool = True,
    include_teams: bool = False,
    include_projects: bool = False,
) -> dict | None:
    """
    Fetch a person by their identifier.

    Returns the person's identity info and optionally their tidbits, teams,
    and projects (filtered by the caller's access permissions).

    Args:
        identifier: The person's unique identifier
        include_tidbits: Whether to include tidbits (default: True)
        include_teams: Whether to include teams the person belongs to (default: False)
        include_projects: Whether to include projects accessible via team membership (default: False)

    Returns:
        The person record with filtered tidbits/teams/projects, or None if not found
    """
    logger.info(f"MCP: Fetching person: {identifier}")

    # For tidbits we can use UserProxy, but for teams/projects we need the full User
    # Fetch project_roles before opening main session to avoid nested session issues
    user_proxy = get_mcp_current_user()
    project_roles: dict[int, str] | None = None
    if user_proxy and user_proxy.id is not None:
        project_roles = get_project_roles_by_user_id(user_proxy.id)

    with make_session() as session:
        # Get full user if we need it for team/project access filtering
        full_user = None
        if include_teams or include_projects:
            full_user = get_mcp_current_user(session, full=True)

        query = session.query(Person)
        if include_tidbits:
            query = query.options(selectinload(Person.tidbits))
        if include_projects:
            # Projects require teams, so load both with chained selectinload
            query = query.options(selectinload(Person.teams).selectinload(Team.projects))
        elif include_teams:
            query = query.options(selectinload(Person.teams))

        # First try exact identifier match
        person = query.filter(Person.identifier == identifier).first()
        if not person:
            # Fall back to searching aliases
            person = query.filter(Person.aliases.contains([identifier])).first()  # type: ignore[union-attr]
        if not person:
            return None

        result = _person_to_dict(person)
        if include_tidbits and person.tidbits:
            # Filter tidbits by access - if no user, return empty list
            if not user_proxy or user_proxy.id is None:
                result["tidbits"] = []
            else:
                filtered_tidbits = _filter_tidbits_by_access(person.tidbits, user_proxy, project_roles)
                result["tidbits"] = [_tidbit_to_dict(t) for t in filtered_tidbits]

        # Include teams (filtered by access)
        if include_teams:
            if not full_user or full_user.id is None:
                result["teams"] = []
            elif has_admin_scope(full_user):
                result["teams"] = [
                    {"id": t.id, "slug": t.slug, "name": t.name}
                    for t in person.teams
                ]
            else:
                accessible_ids = get_accessible_team_ids(session, full_user) or set()
                result["teams"] = [
                    {"id": t.id, "slug": t.slug, "name": t.name}
                    for t in person.teams
                    if t.id in accessible_ids
                ]
            result["team_count"] = len(result["teams"])

        # Include projects (filtered by access)
        if include_projects:
            # Collect all projects from all teams
            projects: dict[int, Project] = {}
            for team in person.teams:
                for project in team.projects:
                    projects[project.id] = project

            if not full_user or full_user.id is None:
                result["projects"] = []
            elif has_admin_scope(full_user):
                result["projects"] = [
                    {"id": p.id, "title": p.title, "slug": p.slug, "state": p.state}
                    for p in projects.values()
                ]
            else:
                accessible_ids = get_accessible_project_ids(session, full_user) or set()
                result["projects"] = [
                    {"id": p.id, "title": p.title, "slug": p.slug, "state": p.state}
                    for p in projects.values()
                    if p.id in accessible_ids
                ]
            result["project_count"] = len(result["projects"])

        return result


@people_mcp.tool()
@visible_when(require_scopes("people"))
async def list_all(
    tags: list[str] | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """
    List all tracked people, optionally filtered by tags or search term.

    Returns thin Person records (identity only, no tidbits).
    Use fetch() to get tidbits for a specific person.

    Args:
        tags: Filter to people with at least one of these tags (in their tidbits)
        search: Search term to match against name, aliases, identifier, or tidbit content
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

        # Note: tags are now on tidbits, not Person
        # For tag filtering, we'd need to join through tidbits
        if tags:
            # Find people who have tidbits with matching tags
            subquery = (
                session.query(PersonTidbit.person_id)
                .filter(PersonTidbit.tags.op("&&")(sql_cast(tags, ARRAY(Text))))
                .distinct()
            )
            query = query.filter(Person.id.in_(subquery))

        if search:
            search_term = f"%{search.lower()}%"
            # Search in aliases array using EXISTS with unnest for ILIKE matching
            alias_match = text(
                "EXISTS (SELECT 1 FROM unnest(aliases) AS alias WHERE alias ILIKE :search_pattern)"
            ).bindparams(search_pattern=search_term)
            # Also search in tidbit content
            tidbit_content_match = (
                session.query(PersonTidbit.person_id)
                .filter(PersonTidbit.content.ilike(search_term))  # type: ignore[union-attr]
                .distinct()
            )
            query = query.filter(
                (Person.display_name.ilike(search_term))  # type: ignore[union-attr]
                | (Person.identifier.ilike(search_term))  # type: ignore[union-attr]
                | alias_match
                | Person.id.in_(tidbit_content_match)
            )

        query = query.order_by(Person.display_name).offset(offset).limit(limit)
        people = query.all()

        return [_person_to_dict(p) for p in people]


@people_mcp.tool()
@visible_when(require_scopes("people"))
async def delete(identifier: str) -> dict:
    """
    Delete a person by their identifier.

    This permanently removes the person and all associated tidbits.
    Observations about this person (with subject "person:<identifier>") will remain.

    Only admins can delete people. Regular users cannot delete people,
    even ones they created, since Person records may be referenced by
    other users' tidbits.

    Args:
        identifier: The person's unique identifier

    Returns:
        Confirmation of deletion
    """
    logger.info(f"MCP: Deleting person: {identifier}")

    # Only admins can delete people
    user = get_mcp_current_user()
    if not user or not has_admin_scope(user):
        raise PermissionError("Only admins can delete people")

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


@people_mcp.tool()
@visible_when(require_scopes("admin"))
async def merge(
    identifiers: list[str],
    primary_identifier: str | None = None,
) -> dict:
    """
    Merge multiple people into one.

    When duplicate Person records exist (same real person with different entries),
    this tool combines them into a single canonical record.

    The merge operation:
    1. Combines aliases from all people (deduped)
    2. Merges contact_info (primary takes precedence for conflicts)
    3. Updates all relationships to point to the primary person:
       - Person tidbits
       - Team memberships (preserving highest role per team)
       - Source item associations
       - Discord/GitHub user links
       - Poll responses
    4. Deletes the secondary person records

    Only admins can merge people.

    Args:
        identifiers: List of person identifiers to merge (minimum 2)
        primary_identifier: Identifier of the person to keep as primary.
                          If not specified, uses the first identifier in the list.

    Returns:
        Summary of the merge operation including counts of updated records.

    Example:
        merge(
            identifiers=["john_doe", "jdoe", "john.doe"],
            primary_identifier="john_doe"
        )
    """
    logger.info(f"MCP: Merging people: {identifiers} -> {primary_identifier or identifiers[0]}")

    # Validate input
    if len(identifiers) < 2:
        raise ValueError("At least 2 identifiers are required for merging")

    if primary_identifier and primary_identifier not in identifiers:
        raise ValueError(f"Primary identifier '{primary_identifier}' must be in the identifiers list")

    primary_id = primary_identifier or identifiers[0]
    secondary_ids = [i for i in identifiers if i != primary_id]

    # Only admins can merge people
    user = get_mcp_current_user()
    if not user or not has_admin_scope(user):
        raise PermissionError("Only admins can merge people")

    with make_session() as session:
        # Fetch all people
        people = session.query(Person).filter(Person.identifier.in_(identifiers)).all()
        people_by_id = {p.identifier: p for p in people}

        # Validate all people exist
        missing = [i for i in identifiers if i not in people_by_id]
        if missing:
            raise ValueError(f"People not found: {missing}")

        primary = people_by_id[primary_id]
        secondaries = [people_by_id[i] for i in secondary_ids]

        # Track stats
        stats = {
            "tidbits_moved": 0,
            "team_memberships_moved": 0,
            "source_items_moved": 0,
            "discord_users_moved": 0,
            "github_users_moved": 0,
            "poll_responses_moved": 0,
            "users_updated": 0,
        }

        # 1. Merge aliases
        all_aliases = set(primary.aliases or [])
        for sec in secondaries:
            all_aliases.update(sec.aliases or [])
            # Add secondary identifiers and display names as aliases
            all_aliases.add(sec.identifier)
            if sec.display_name and sec.display_name != primary.display_name:
                all_aliases.add(sec.display_name)
        # Remove primary's own identifier from aliases
        all_aliases.discard(primary.identifier)
        all_aliases.discard(primary.display_name)
        primary.aliases = list(all_aliases)

        # 2. Merge contact_info (secondary values fill gaps, primary takes precedence)
        merged_contact = {}
        for sec in reversed(secondaries):  # Process in reverse so earlier ones take precedence
            merged_contact = _deep_merge(merged_contact, dict(sec.contact_info or {}))
        merged_contact = _deep_merge(merged_contact, dict(primary.contact_info or {}))
        primary.contact_info = merged_contact

        # 3. Move tidbits
        for sec in secondaries:
            count = (
                session.query(PersonTidbit)
                .filter(PersonTidbit.person_id == sec.id)
                .update({PersonTidbit.person_id: primary.id})
            )
            stats["tidbits_moved"] += count

        # 4. Move team memberships (handle duplicates - keep highest role)
        role_priority = {"admin": 3, "lead": 2, "member": 1}

        for sec in secondaries:
            # Get secondary's team memberships
            sec_memberships = session.execute(
                team_members.select().where(team_members.c.person_id == sec.id)
            ).fetchall()

            for membership in sec_memberships:
                team_id = membership.team_id
                sec_role = membership.role

                # Check if primary already has membership in this team
                existing = session.execute(
                    team_members.select().where(
                        team_members.c.team_id == team_id,
                        team_members.c.person_id == primary.id,
                    )
                ).fetchone()

                if existing:
                    # Keep higher role
                    if role_priority.get(sec_role, 0) > role_priority.get(existing.role, 0):
                        session.execute(
                            team_members.update()
                            .where(
                                team_members.c.team_id == team_id,
                                team_members.c.person_id == primary.id,
                            )
                            .values(role=sec_role)
                        )
                    # Delete secondary's membership (will be handled by cascade, but be explicit)
                    session.execute(
                        team_members.delete().where(
                            team_members.c.team_id == team_id,
                            team_members.c.person_id == sec.id,
                        )
                    )
                else:
                    # Move membership to primary
                    session.execute(
                        team_members.update()
                        .where(
                            team_members.c.team_id == team_id,
                            team_members.c.person_id == sec.id,
                        )
                        .values(person_id=primary.id)
                    )
                stats["team_memberships_moved"] += 1

        # 5. Move source_item associations (handle duplicates)
        for sec in secondaries:
            # Get secondary's source item associations
            sec_items = session.execute(
                source_item_people.select().where(source_item_people.c.person_id == sec.id)
            ).fetchall()

            for item in sec_items:
                source_item_id = item.source_item_id

                # Check if primary already associated with this item
                existing = session.execute(
                    source_item_people.select().where(
                        source_item_people.c.source_item_id == source_item_id,
                        source_item_people.c.person_id == primary.id,
                    )
                ).fetchone()

                if existing:
                    # Delete duplicate
                    session.execute(
                        source_item_people.delete().where(
                            source_item_people.c.source_item_id == source_item_id,
                            source_item_people.c.person_id == sec.id,
                        )
                    )
                else:
                    # Move to primary
                    session.execute(
                        source_item_people.update()
                        .where(
                            source_item_people.c.source_item_id == source_item_id,
                            source_item_people.c.person_id == sec.id,
                        )
                        .values(person_id=primary.id)
                    )
                stats["source_items_moved"] += 1

        # 6. Move Discord users
        for sec in secondaries:
            count = (
                session.query(DiscordUser)
                .filter(DiscordUser.person_id == sec.id)
                .update({DiscordUser.person_id: primary.id})
            )
            stats["discord_users_moved"] += count

        # 7. Move GitHub users
        for sec in secondaries:
            count = (
                session.query(GithubUser)
                .filter(GithubUser.person_id == sec.id)
                .update({GithubUser.person_id: primary.id})
            )
            stats["github_users_moved"] += count

        # 8. Move poll responses
        for sec in secondaries:
            count = (
                session.query(PollResponse)
                .filter(PollResponse.person_id == sec.id)
                .update({PollResponse.person_id: primary.id})
            )
            stats["poll_responses_moved"] += count

        # 9. Handle User links (users.person_id -> people.id)
        # If secondary has a linked user but primary doesn't, move the link
        for sec in secondaries:
            if sec.user_id:
                if not primary.user_id:
                    # Move user link to primary
                    primary.user_id = sec.user_id
                    stats["users_updated"] += 1
                else:
                    # Both have users - just clear secondary's link
                    # (user will need to be handled manually if needed)
                    logger.warning(
                        f"Both {primary.identifier} and {sec.identifier} have linked users. "
                        f"Keeping primary's user link ({primary.user_id}), clearing secondary's ({sec.user_id})."
                    )
                sec.user_id = None

        # 10. Delete secondary people (relationships should be moved by now)
        deleted_names = []
        for sec in secondaries:
            deleted_names.append(sec.display_name)
            session.delete(sec)

        session.commit()

        return {
            "success": True,
            "primary": {
                "identifier": primary.identifier,
                "display_name": primary.display_name,
                "aliases": primary.aliases,
            },
            "merged_from": deleted_names,
            "stats": stats,
        }


# ============== Tidbit Tools ==============


@people_mcp.tool()
@visible_when(require_scopes("people"))
async def tidbit_add(
    identifier: str,
    content: str,
    tidbit_type: str = "note",
    tags: list[str] | None = None,
    project_id: int | None = None,
    sensitivity: str = "basic",
) -> dict:
    """
    Add a tidbit of information about a person.

    Tidbits are searchable pieces of information with access control.
    - If project_id is None: only you (creator) and admins can see it
    - If project_id is set: project members can see it based on sensitivity

    Args:
        identifier: The person's unique identifier
        content: The information to record about the person
        tidbit_type: Type of tidbit (note, preference, fact, etc.) default: "note"
        tags: Categorization tags
        project_id: Project ID for access control (None = creator-only)
        sensitivity: Sensitivity level (basic, internal, confidential)

    Returns:
        Task status with task_id

    Example:
        tidbit_add(
            identifier="alice_chen",
            content="Prefers morning meetings, allergic to peanuts",
            tidbit_type="preference",
            tags=["scheduling", "dietary"],
        )
    """
    logger.info(f"MCP: Adding tidbit for person: {identifier}")

    with make_session() as session:
        person = session.query(Person).filter(Person.identifier == identifier).first()
        if not person:
            raise ValueError(f"Person with identifier '{identifier}' not found")
        person_id = person.id

    # Get current user for creator_id
    user = get_mcp_current_user()
    creator_id = user.id if user else None

    task = celery_app.send_task(
        SYNC_PERSON_TIDBIT,
        queue=f"{settings.CELERY_QUEUE_PREFIX}-people",
        kwargs={
            "person_id": person_id,
            "content": content,
            "tidbit_type": tidbit_type,
            "tags": tags,
            "project_id": project_id,
            "sensitivity": sensitivity,
            "creator_id": creator_id,
        },
    )

    return {
        "task_id": task.id,
        "status": "queued",
        "person_identifier": identifier,
    }


@people_mcp.tool()
@visible_when(require_scopes("people"))
async def tidbit_update(
    tidbit_id: int,
    content: str | None = None,
    tidbit_type: str | None = None,
    tags: list[str] | None = None,
    project_id: int | None = None,
    sensitivity: str | None = None,
) -> dict:
    """
    Update a tidbit. Only the creator or admin can update.

    Args:
        tidbit_id: ID of the tidbit to update
        content: New content (replaces existing)
        tidbit_type: New type
        tags: New tags (replaces existing)
        project_id: New project ID
        sensitivity: New sensitivity level

    Returns:
        Updated tidbit data
    """
    logger.info(f"MCP: Updating tidbit: {tidbit_id}")

    user = get_mcp_current_user()

    with make_session() as session:
        tidbit = session.get(PersonTidbit, tidbit_id)
        if not tidbit:
            raise ValueError(f"Tidbit with ID {tidbit_id} not found")

        if not user or not user_can_edit(user, tidbit):
            raise PermissionError("You can only edit tidbits you created")

        if content is not None:
            tidbit.content = content
            tidbit.embed_status = "RAW"  # Re-embed with new content
        if tidbit_type is not None:
            tidbit.tidbit_type = tidbit_type
        if tags is not None:
            tidbit.tags = list(tags)
        if project_id is not None:
            tidbit.project_id = project_id
        if sensitivity is not None:
            tidbit.sensitivity = sensitivity

        session.commit()

        return _tidbit_to_dict(tidbit)


@people_mcp.tool()
@visible_when(require_scopes("people"))
async def tidbit_delete(tidbit_id: int) -> dict:
    """
    Delete a tidbit. Only the creator or admin can delete.

    Args:
        tidbit_id: ID of the tidbit to delete

    Returns:
        Confirmation of deletion
    """
    logger.info(f"MCP: Deleting tidbit: {tidbit_id}")

    user = get_mcp_current_user()

    with make_session() as session:
        tidbit = session.get(PersonTidbit, tidbit_id)
        if not tidbit:
            raise ValueError(f"Tidbit with ID {tidbit_id} not found")

        if not user or not user_can_edit(user, tidbit):
            raise PermissionError("You can only delete tidbits you created")

        person_identifier = tidbit.person.identifier if tidbit.person else None
        session.delete(tidbit)
        session.commit()

        return {
            "deleted": True,
            "tidbit_id": tidbit_id,
            "person_identifier": person_identifier,
        }


@people_mcp.tool()
@visible_when(require_scopes("people"))
async def tidbit_list(
    identifier: str,
    tidbit_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """
    List tidbits for a person (filtered by your access permissions).

    Args:
        identifier: The person's unique identifier
        tidbit_type: Filter by tidbit type (note, preference, etc.)
        limit: Maximum number of results (default 50, max 200)
        offset: Number of results to skip for pagination

    Returns:
        List of tidbits you have access to
    """
    logger.info(f"MCP: Listing tidbits for person: {identifier}")

    limit = min(max(limit, 1), 200)
    offset = min(max(offset, 0), 10000)

    user = get_mcp_current_user()

    # If no user, return empty list (unauthenticated access gets no tidbits)
    if not user or user.id is None:
        return []

    # Fetch project_roles BEFORE opening session to avoid nested session issues
    # (get_project_roles_by_user_id opens its own session which would conflict)
    project_roles = get_project_roles_by_user_id(user.id)

    with make_session() as session:
        person = session.query(Person).filter(Person.identifier == identifier).first()
        if not person:
            raise ValueError(f"Person with identifier '{identifier}' not found")

        query = (
            session.query(PersonTidbit)
            .options(selectinload(PersonTidbit.person))
            .filter(PersonTidbit.person_id == person.id)
        )

        if tidbit_type:
            query = query.filter(PersonTidbit.tidbit_type == tidbit_type)

        query = query.order_by(PersonTidbit.id.desc()).offset(offset).limit(limit)
        tidbits = query.all()

        tidbits = _filter_tidbits_by_access(tidbits, user, project_roles)

        # Convert to dicts inside session while relationships are accessible
        return [_tidbit_to_dict(t) for t in tidbits]
