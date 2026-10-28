"""MCP subserver for team management."""

import logging
import re
from typing import Any

from fastmcp import FastMCP
from sqlalchemy import Text, cast, or_, select
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
from sqlalchemy.orm import Session, scoped_session, selectinload

from memory.api.MCP.access import get_mcp_current_user
from memory.api.MCP.audit import audit_call
from memory.api.MCP.visibility import require_scopes, visible_when
from memory.common.access_control import (
    UserLike,
    filter_projects_query,
    filter_teams_query,
    has_admin_scope,
)
from memory.common.scopes import SCOPE_TEAMS, SCOPE_TEAMS_WRITE
from memory.common.db.connection import make_session
from memory.common.db.models import (
    Person,
    Project,
    Team,
)
from memory.common.people import find_or_create_person, find_person
from memory.common.db.models.sources import (
    GithubRepo,
    team_members,
)

logger = logging.getLogger(__name__)

teams_mcp = FastMCP("memory-teams")

_UNSET = "__UNSET__"


def resolve_person(
    session: Session | scoped_session[Session],
    identifier: str | int,
) -> Person | None:
    """Resolve a person by int ID or fuzzy name/email/alias/identifier."""
    if isinstance(identifier, int) or (isinstance(identifier, str) and identifier.isdigit()):
        return session.query(Person).filter(Person.id == int(identifier)).first()
    return find_person(session, str(identifier))


def team_to_dict(
    team: Team,
    include_members: bool = False,
    include_projects: bool = False,
    member_roles: dict[int, str] | None = None,
) -> dict[str, Any]:
    """Convert a Team model to a dictionary for API responses.

    Args:
        team: The Team model instance
        include_members: Whether to include member list
        include_projects: Whether to include project list
        member_roles: Optional dict mapping person_id -> role (from junction table)
    """
    result = {
        "id": team.id,
        "name": team.name,
        "slug": team.slug,
        "description": team.description,
        "owner_id": team.owner_id,
        "owner": person_summary(team.owner) if team.owner else None,
        "tags": list(team.tags or []),
        "is_active": team.is_active,
        "created_at": team.created_at.isoformat() if team.created_at else None,
        "archived_at": team.archived_at.isoformat() if team.archived_at else None,
    }
    if include_members:
        if member_roles:
            result["members"] = [
                {
                    "id": p.id,
                    "identifier": p.identifier,
                    "display_name": p.display_name,
                    "contributor_status": p.contributor_status,
                    "role": member_roles.get(p.id, "member"),
                }
                for p in team.members
            ]
        else:
            result["members"] = [
                {"id": p.id, "identifier": p.identifier, "display_name": p.display_name}
                for p in team.members
            ]
        result["member_count"] = len(team.members)
    if include_projects:
        result["projects"] = [project_summary(p) for p in team.projects]
        result["project_count"] = len(team.projects)
    return result


def person_summary(person: Person) -> dict[str, Any]:
    """Brief person summary for team membership responses."""
    return {
        "id": person.id,
        "identifier": person.identifier,
        "display_name": person.display_name,
        "contributor_status": person.contributor_status,
    }


# ============== Team CRUD ==============


def make_slug(name: str) -> str:
    """Create a URL-safe slug from a team name."""
    slug = re.sub(r"\s+", "-", name.lower().strip())
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


def upsert_team_record(
    session: Session | scoped_session[Session],
    slug: str,
    name: str,
    description: str | None,
    tags: list[str] | None,
    is_active: bool | None,
    owner_id: int | None = 0,
) -> tuple[Team, str]:
    """Create or update the Team record.

    Args:
        owner_id: 0 = skip (don't change), None = clear, positive int = set.

    Returns:
        Tuple of (team, action) where action is "created" or "updated"
    """
    from datetime import datetime, timezone

    team = session.query(Team).filter(Team.slug == slug).first()

    if team:
        if name:
            team.name = name
        if description is not None:
            team.description = description
        if tags is not None:
            team.tags = tags
        if is_active is not None:
            team.is_active = is_active
            if not is_active and team.archived_at is None:
                team.archived_at = datetime.now(timezone.utc)
        if owner_id != 0:
            team.owner_id = owner_id  # None clears, int sets
        return team, "updated"

    team = Team(
        name=name,
        slug=slug,
        description=description,
        tags=tags or [],
        is_active=is_active if is_active is not None else True,
        owner_id=owner_id or None,
    )
    session.add(team)
    session.flush()
    return team, "created"


@teams_mcp.tool()
@visible_when(require_scopes(SCOPE_TEAMS_WRITE))
@audit_call(level=logging.INFO)
async def upsert(
    name: str,
    slug: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    # Ownership
    owner: str | int | None = _UNSET,
    members: list[str] | None = None,
    is_active: bool | None = None,
) -> dict:
    """
    Create or update a team.

    Args:
        name: Team display name
        slug: URL-safe identifier (auto-generated from name if not provided)
        description: Optional description of the team's purpose
        tags: Tags for categorization (e.g., ["engineering", "core"])
        owner: Person responsible for this team - can be person identifier or ID.
               Set to null to clear the owner.
        members: If provided, set team to exactly these members.
                 Pass [] to remove all members. Pass None to leave unchanged.
        is_active: Active status (set to false to archive team)

    If a team with the slug exists it is updated, otherwise created.

    Returns:
        Dict with team data and membership changes
    """
    if not slug:
        slug = make_slug(name)

    logger.info(f"MCP: Upserting team: {slug}")

    result: dict[str, Any] = {
        "success": True,
        "action": None,
        "team": None,
        "membership_changes": {},
        "warnings": [],
    }

    with make_session() as session:
        user = get_mcp_current_user(session)
        if not user:
            return {"error": "Not authenticated"}

        # For existing teams, require lead/admin to prevent arbitrary modification.
        # (Creating a new team is allowed for any teams:write user.)
        existing_team = session.query(Team).filter(Team.slug == slug).first()
        if existing_team:
            caller_role = get_caller_team_role(session, user, existing_team.id)
            if caller_role not in ("lead", "admin"):
                return {"error": "Only team leads and admins can modify an existing team"}

        # Resolve owner: _UNSET = skip, None = clear, str/int = resolve person
        owner_id: int | None = 0  # 0 = skip
        if owner is None:
            owner_id = None  # Explicitly clear
        elif owner is not _UNSET:
            owner_person = resolve_person(session, owner)
            if owner_person:
                owner_id = owner_person.id
            else:
                result["warnings"].append(f"Owner not found: {owner}")

        team, action = upsert_team_record(session, slug, name, description, tags, is_active, owner_id)
        result["action"] = action

        # Capture team.id immediately - before any queries that might trigger autoflush
        # and expire the object (DetachedInstanceError with scoped_session)
        team_id = team.id

        if members is not None:
            team = (
                session.query(Team)
                .options(selectinload(Team.members))
                .filter(Team.id == team_id)
                .first()
            )
            if team:
                result["membership_changes"] = set_team_members(session, team, members, user.id)

        session.commit()

        # Re-query with relationships to avoid lazy-load issues
        team = (
            session.query(Team)
            .options(selectinload(Team.members), selectinload(Team.owner))
            .filter(Team.id == team_id)
            .first()
        )
        if team:
            result["team"] = team_to_dict(team, include_members=True)

    return result


def set_team_members(
    session: Session | scoped_session[Session],
    team: Team,
    member_identifiers: list[str],
    creator_id: int | None,
) -> dict[str, Any]:
    """Set team to exactly these members.

    Returns dict with added/removed counts and any warnings.
    """
    current_member_list = list(team.members)
    current_member_ids = {p.id for p in current_member_list}

    # Resolve target identifiers to Person records (using fuzzy matching)
    target_people: dict[int, Person] = {}  # person_id -> Person
    result: dict[str, Any] = {
        "added": [],
        "removed": [],
        "created_people": [],
        "warnings": [],
    }
    for identifier in member_identifiers:
        person, was_created = find_or_create_person(
            session, name=identifier, create_if_missing=True, creator_id=creator_id
        )
        if not person:
            result["warnings"].append(f"Could not find or create person for '{identifier}'")
            continue
        if was_created:
            result["created_people"].append(identifier)
        else:
            logger.info(
                "Fuzzy matched '%s' -> person '%s' (id=%s)",
                identifier, person.identifier, person.id,
            )
        target_people[person.id] = person

    to_add_ids = set(target_people) - current_member_ids
    to_remove_ids = current_member_ids - set(target_people)

    for person in current_member_list:
        if person.id in to_remove_ids:
            team.members.remove(person)
            result["removed"].append(person.identifier)

    for person_id in to_add_ids:
        person = target_people[person_id]
        team.members.append(person)
        result["added"].append(person.identifier)

    session.flush()

    # Warning for clearing all members
    if not target_people and current_member_list:
        result["warnings"].append(f"Removed all {len(to_remove_ids)} members from team")

    return result


@teams_mcp.tool()
@visible_when(require_scopes(SCOPE_TEAMS))
async def fetch(
    team: str | int,
    include_members: bool = True,
    include_projects: bool = False,
) -> dict:
    """
    Get team details by slug or ID.

    Args:
        team: Team slug (e.g., "engineering-core") or numeric ID
        include_members: Whether to include member list with roles (default: true)
        include_projects: Whether to include project list (default: false)

    Returns:
        Team data with optional member and project lists, or error if not found/accessible
    """
    with make_session() as session:
        user = get_mcp_current_user(session)
        if not user:
            return {"error": "Not authenticated"}

        query = session.query(Team).options(
            selectinload(Team.members),
            selectinload(Team.owner),
        )

        # Apply access control filtering
        query = filter_teams_query(session, user, query)

        if include_projects:
            query = query.options(selectinload(Team.projects))

        if isinstance(team, int) or (isinstance(team, str) and team.isdigit()):
            team_obj = query.filter(Team.id == int(team)).first()
        else:
            team_obj = query.filter(Team.slug == team).first()

        if not team_obj:
            return {"error": f"Team not found: {team}"}

        # Fetch member roles from junction table if including members
        member_roles: dict[int, str] | None = None
        if include_members:
            role_query = session.execute(
                select(team_members.c.person_id, team_members.c.role)
                .where(team_members.c.team_id == team_obj.id)
            )
            member_roles = {row.person_id: row.role for row in role_query}

        return {
            "team": team_to_dict(
                team_obj,
                include_members=include_members,
                include_projects=include_projects,
                member_roles=member_roles,
            )
        }


@teams_mcp.tool()
@visible_when(require_scopes(SCOPE_TEAMS))
async def list_all(
    tags: list[str] | None = None,
    match_any_tag: bool = False,
    include_inactive: bool = False,
    include_projects: bool = False,
) -> dict:
    """
    List all teams the user can access.

    Args:
        tags: Filter to teams by tags
        match_any_tag: If true, match teams with ANY of the tags. If false (default),
                       match only teams with ALL tags.
        include_inactive: Include archived/inactive teams (default: false)
        include_projects: Include projects assigned to each team (default: false)

    Returns:
        List of teams (filtered to teams the user has access to)
    """
    with make_session() as session:
        user = get_mcp_current_user(session)
        if not user:
            return {"error": "Not authenticated"}

        query = session.query(Team).options(
            selectinload(Team.members),
            selectinload(Team.owner),
        )

        # Apply access control filtering (non-admins only see their teams)
        query = filter_teams_query(session, user, query)

        if include_projects:
            query = query.options(selectinload(Team.projects))

        if not include_inactive:
            query = query.filter(Team.is_active == True)  # noqa: E712

        if tags:
            if match_any_tag:
                # Teams must have ANY of the specified tags
                conditions = [Team.tags.op("@>")(cast([tag], PG_ARRAY(Text))) for tag in tags]
                query = query.filter(or_(*conditions))
            else:
                # Teams must have ALL specified tags (PostgreSQL array contains)
                query = query.filter(Team.tags.op("@>")(cast(tags, PG_ARRAY(Text))))

        teams = query.order_by(Team.name).all()

        return {
            "teams": [team_to_dict(t, include_members=False, include_projects=include_projects) for t in teams],
            "count": len(teams),
        }


# ============== Team Membership ==============


@teams_mcp.tool()
@visible_when(require_scopes(SCOPE_TEAMS_WRITE))
@audit_call(level=logging.INFO)
async def team_add_member(
    team: str | int,
    person: str | int,
    role: str = "member",
) -> dict:
    """
    Add a person to a team.

    Args:
        team: Team slug or ID
        person: Person identifier or ID
        role: Team role - "member", "lead", or "admin" (default: "member")

    Returns:
        Result, or error if team not found/accessible
    """
    logger.info(f"MCP: Adding {person} to team {team}")

    if role not in ("member", "lead", "admin"):
        return {"error": f"Invalid role: {role}. Must be 'member', 'lead', or 'admin'"}

    with make_session() as session:
        user = get_mcp_current_user(session)
        if not user:
            return {"error": "Not authenticated"}

        # Find team with access control
        query = filter_teams_query(session, user, session.query(Team).options(selectinload(Team.members)))

        if isinstance(team, int) or (isinstance(team, str) and team.isdigit()):
            team_obj = query.filter(Team.id == int(team)).first()
        else:
            team_obj = query.filter(Team.slug == team).first()

        if not team_obj:
            return {"error": f"Team not found: {team}"}

        # Find person
        person_obj = resolve_person(session, person)

        if not person_obj:
            return {"error": f"Person not found: {person}"}

        # Verify caller has lead/admin role in the team — members cannot add others.
        # Admins (has_admin_scope) are implicitly "admin" and always pass.
        caller_role = get_caller_team_role(session, user, team_obj.id)
        if caller_role not in ("lead", "admin"):
            return {"error": "Only team leads and admins can add members"}

        # Verify caller cannot assign a role higher than their own.
        caller_rank = _ROLE_RANK.get(caller_role, -1)
        target_rank = _ROLE_RANK.get(role, -1)
        if target_rank > caller_rank:
            return {
                "error": (
                    f"Cannot assign role '{role}' — "
                    f"your team role is '{caller_role}'"
                )
            }

        # Check if already a member
        if person_obj in team_obj.members:
            return {
                "success": True,
                "message": f"{person_obj.identifier} is already a member of {team_obj.slug}",
                "already_member": True,
            }

        # Add to team with explicit role via junction table insert
        # (Using relationship.append() would only set the server default "member" role)
        session.execute(
            team_members.insert().values(
                team_id=team_obj.id,
                person_id=person_obj.id,
                role=role,
            )
        )
        session.commit()

        return {
            "success": True,
            "team": team_obj.slug,
            "person": person_obj.identifier,
            "role": role,
        }


@teams_mcp.tool()
@visible_when(require_scopes(SCOPE_TEAMS_WRITE))
@audit_call(level=logging.INFO)
async def team_remove_member(
    team: str | int,
    person: str | int,
) -> dict:
    """
    Remove a person from a team.

    Args:
        team: Team slug or ID
        person: Person identifier or ID

    Returns:
        Result, or error if team not found/accessible
    """
    logger.info(f"MCP: Removing {person} from team {team}")

    with make_session() as session:
        user = get_mcp_current_user(session)
        if not user:
            return {"error": "Not authenticated"}

        # Find team with access control
        query = filter_teams_query(session, user, session.query(Team).options(selectinload(Team.members)))

        if isinstance(team, int) or (isinstance(team, str) and team.isdigit()):
            team_obj = query.filter(Team.id == int(team)).first()
        else:
            team_obj = query.filter(Team.slug == team).first()

        if not team_obj:
            return {"error": f"Team not found: {team}"}

        # Verify caller has lead/admin role before removing members.
        caller_role = get_caller_team_role(session, user, team_obj.id)
        if caller_role not in ("lead", "admin"):
            return {"error": "Only team leads and admins can remove members"}

        # Find person
        person_obj = resolve_person(session, person)

        if not person_obj:
            return {"error": f"Person not found: {person}"}

        # Check if member
        if person_obj not in team_obj.members:
            return {
                "success": True,
                "message": f"{person_obj.identifier} is not a member of {team_obj.slug}",
                "was_not_member": True,
            }

        # Remove from team
        team_obj.members.remove(person_obj)
        session.commit()

        return {
            "success": True,
            "team": team_obj.slug,
            "person": person_obj.identifier,
        }


# ============== Project-Team Assignment ==============


def project_summary(project: Project) -> dict[str, Any]:
    """Brief project summary for responses."""
    return {
        "id": project.id,
        "title": project.title,
        "slug": project.slug,
        "state": project.state,
    }


@teams_mcp.tool()
@visible_when(require_scopes(SCOPE_TEAMS))
async def project_list_access(
    project: int | str,
) -> dict:
    """
    List all people who can access a project (through team membership).

    Args:
        project: Project ID or slug

    Returns:
        Teams with their members, and aggregate list of all people with access,
        or error if project not found/accessible
    """
    with make_session() as session:
        user = get_mcp_current_user(session)
        if not user:
            return {"error": "Not authenticated"}

        project_obj = find_project_with_access(session, user, project)
        if not project_obj:
            return {"error": f"Project not found: {project}"}

        # Collect all people with access
        people_with_access: dict[int, Person] = {}
        teams_data = []

        for team in project_obj.teams:
            team_members = []
            for person in team.members:
                people_with_access[person.id] = person
                team_members.append(person_summary(person))
            teams_data.append({
                "team": team.slug,
                "team_name": team.name,
                "members": team_members,
            })

        return {
            "project": project_summary(project_obj),
            "teams": teams_data,
            "all_people": [person_summary(p) for p in people_with_access.values()],
            "total_people_count": len(people_with_access),
        }


# ============== Helper Functions ==============

# Role ordering for privilege checks — higher index = higher privilege.
_ROLE_RANK = {"member": 0, "lead": 1, "admin": 2}


def get_caller_team_role(
    session: Session | scoped_session[Session], user: UserLike, team_id: int
) -> str | None:
    """Return the caller's role ('member'/'lead'/'admin') in the given team.

    Returns None if the caller has no person record or is not a team member.
    Admins (has_admin_scope) bypass this and are treated as implicit 'admin'.
    """
    if has_admin_scope(user):
        return "admin"

    person = (
        session.query(Person)
        .filter(Person.user_id == user.id)
        .first()
    )
    if person is None:
        return None

    row = session.execute(
        select(team_members.c.role)
        .where(team_members.c.team_id == team_id)
        .where(team_members.c.person_id == person.id)
    ).first()
    return row.role if row else None


def find_project_with_access(session: Session | scoped_session[Session], user: UserLike, project: int | str) -> Project | None:
    """Find a project by ID or slug, with access control filtering."""
    query = session.query(Project).options(selectinload(Project.teams))

    # Apply access control filtering
    query = filter_projects_query(session, user, query)

    if isinstance(project, int):
        return query.filter(Project.id == project).first()

    if project.isdigit():
        return query.filter(Project.id == int(project)).first()

    # Try to parse as slug (owner/repo:number)
    if ":" in project:
        parts = project.rsplit(":", 1)
        if len(parts) == 2 and parts[1].isdigit():
            # This is a slug like "owner/repo:123"
            repo_path = parts[0]  # "owner/repo"
            number = int(parts[1])

            if "/" in repo_path:
                owner, name = repo_path.split("/", 1)
                return (
                    query
                    .join(GithubRepo)
                    .filter(GithubRepo.owner == owner, GithubRepo.name == name, Project.number == number)
                    .first()
                )

    # Try exact title match as fallback
    return query.filter(Project.title == project).first()
