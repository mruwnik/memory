"""MCP subserver for team management.

Note on error response patterns:
Each MCP tool returns a specific response schema. When returning errors,
we include the expected fields with null/empty values to avoid breaking
clients that expect certain keys. For example:
- team_get returns {"error": ..., "team": None}
- team_list returns {"error": ..., "teams": [], "count": 0}
- team_update returns {"error": ...} (no expected fields in success case)
"""

import logging
from typing import Any

from fastmcp import FastMCP
from sqlalchemy import Text, cast, select
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
from sqlalchemy.orm import Session, selectinload

from memory.api.MCP.access import get_mcp_current_user
from memory.api.MCP.visibility import require_scopes, visible_when
from memory.common.access_control import (
    filter_projects_query,
    filter_teams_query,
    get_accessible_project_ids,
    get_accessible_team_ids,
    has_admin_scope,
)
from memory.common.db.connection import make_session
from memory.common.db.models import Person, Project, Team, User, can_access_project
from memory.common.db.models.sources import GithubRepo, team_members

logger = logging.getLogger(__name__)

teams_mcp = FastMCP("memory-teams")


def _team_to_dict(team: Team, include_members: bool = False, include_projects: bool = False) -> dict[str, Any]:
    """Convert a Team model to a dictionary for API responses."""
    result = {
        "id": team.id,
        "name": team.name,
        "slug": team.slug,
        "description": team.description,
        "tags": list(team.tags or []),
        "discord_role_id": team.discord_role_id,
        "discord_guild_id": team.discord_guild_id,
        "auto_sync_discord": team.auto_sync_discord,
        "github_team_id": team.github_team_id,
        "github_team_slug": team.github_team_slug,
        "github_org": team.github_org,
        "auto_sync_github": team.auto_sync_github,
        "is_active": team.is_active,
        "created_at": team.created_at.isoformat() if team.created_at else None,
        "archived_at": team.archived_at.isoformat() if team.archived_at else None,
    }
    if include_members:
        result["members"] = [
            {"id": p.id, "identifier": p.identifier, "display_name": p.display_name}
            for p in team.members
        ]
        result["member_count"] = len(team.members)
    if include_projects:
        result["projects"] = [_project_summary(p) for p in team.projects]
        result["project_count"] = len(team.projects)
    return result


def _person_summary(person: Person) -> dict[str, Any]:
    """Brief person summary for team membership responses."""
    return {
        "id": person.id,
        "identifier": person.identifier,
        "display_name": person.display_name,
        "contributor_status": person.contributor_status,
    }


# ============== Team CRUD ==============


@teams_mcp.tool()
@visible_when(require_scopes("teams"))
async def team_create(
    name: str,
    slug: str,
    description: str | None = None,
    tags: list[str] | None = None,
    discord_role_id: int | None = None,
    discord_guild_id: int | None = None,
    auto_sync_discord: bool = True,
    github_team_id: int | None = None,
    github_team_slug: str | None = None,
    github_org: str | None = None,
    auto_sync_github: bool = True,
) -> dict:
    """
    Create a new team.

    Args:
        name: Display name for the team (e.g., "Engineering Core")
        slug: URL-safe identifier (e.g., "engineering-core")
        description: Optional description of the team's purpose
        tags: Tags for categorization (e.g., ["engineering", "core"])
        discord_role_id: Discord role ID to sync membership to
        discord_guild_id: Discord guild/server ID (required if discord_role_id set)
        auto_sync_discord: Whether to auto-sync membership to Discord (default: true)
        github_team_id: GitHub team ID to sync membership to
        github_team_slug: GitHub team slug (e.g., "engineering-core") - required for sync
        github_org: GitHub organization (required if github_team_id set)
        auto_sync_github: Whether to auto-sync membership to GitHub (default: true)

    Returns:
        Created team data

    Example:
        team_create(
            name="Engineering Core",
            slug="engineering-core",
            tags=["engineering", "core"],
            discord_role_id=123456789,
            discord_guild_id=987654321,
        )
    """
    logger.info(f"MCP: Creating team: {slug}")

    with make_session() as session:
        # Check for existing team with same slug
        existing = session.query(Team).filter(Team.slug == slug).first()
        if existing:
            return {"error": f"Team with slug '{slug}' already exists", "existing_team_id": existing.id}

        team = Team(
            name=name,
            slug=slug,
            description=description,
            tags=tags or [],
            discord_role_id=discord_role_id,
            discord_guild_id=discord_guild_id,
            auto_sync_discord=auto_sync_discord,
            github_team_id=github_team_id,
            github_team_slug=github_team_slug,
            github_org=github_org,
            auto_sync_github=auto_sync_github,
        )
        session.add(team)
        session.commit()

        return {"success": True, "team": _team_to_dict(team)}


@teams_mcp.tool()
@visible_when(require_scopes("teams"))
async def team_get(
    team: str | int,
    include_members: bool = True,
    include_projects: bool = False,
) -> dict:
    """
    Get team details by slug or ID.

    Args:
        team: Team slug (e.g., "engineering-core") or numeric ID
        include_members: Whether to include member list (default: true)
        include_projects: Whether to include project list (default: false)

    Returns:
        Team data with optional member and project lists, or error if not found/accessible
    """
    with make_session() as session:
        user = get_mcp_current_user(session, full=True)
        if not user:
            return {"error": "Not authenticated", "team": None}

        query = session.query(Team).options(selectinload(Team.members))

        # Apply access control filtering
        query = filter_teams_query(session, user, query)

        if include_projects:
            query = query.options(selectinload(Team.projects))

        if isinstance(team, int) or (isinstance(team, str) and team.isdigit()):
            team_obj = query.filter(Team.id == int(team)).first()
        else:
            team_obj = query.filter(Team.slug == team).first()

        if not team_obj:
            return {"error": f"Team not found: {team}", "team": None}

        return {"team": _team_to_dict(team_obj, include_members=include_members, include_projects=include_projects)}


@teams_mcp.tool()
@visible_when(require_scopes("teams"))
async def team_list(
    tags: list[str] | None = None,
    include_inactive: bool = False,
    include_projects: bool = False,
) -> dict:
    """
    List all teams, optionally filtered by tags.

    Args:
        tags: Filter to teams that have ALL of these tags
        include_inactive: Include archived/inactive teams (default: false)
        include_projects: Include projects assigned to each team (default: false)

    Returns:
        List of teams (filtered to teams the user has access to)
    """
    with make_session() as session:
        user = get_mcp_current_user(session, full=True)
        if not user:
            return {"error": "Not authenticated", "teams": [], "count": 0}

        query = session.query(Team).options(selectinload(Team.members))

        # Apply access control filtering (non-admins only see their teams)
        query = filter_teams_query(session, user, query)

        if include_projects:
            query = query.options(selectinload(Team.projects))

        if not include_inactive:
            query = query.filter(Team.is_active == True)  # noqa: E712

        if tags:
            # Teams must have ALL specified tags (PostgreSQL array contains)
            query = query.filter(Team.tags.op("@>")(cast(tags, PG_ARRAY(Text))))

        teams = query.order_by(Team.name).all()

        return {
            "teams": [_team_to_dict(t, include_members=False, include_projects=include_projects) for t in teams],
            "count": len(teams),
        }


@teams_mcp.tool()
@visible_when(require_scopes("teams"))
async def team_update(
    team: str | int,
    name: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    discord_role_id: int | None = None,
    discord_guild_id: int | None = None,
    auto_sync_discord: bool | None = None,
    github_team_id: int | None = None,
    github_team_slug: str | None = None,
    github_org: str | None = None,
    auto_sync_github: bool | None = None,
    is_active: bool | None = None,
) -> dict:
    """
    Update team settings.

    Args:
        team: Team slug or ID
        name: New display name
        description: New description
        tags: New tags (replaces existing)
        discord_role_id: Discord role ID
        discord_guild_id: Discord guild ID
        auto_sync_discord: Whether to sync to Discord
        github_team_id: GitHub team ID
        github_team_slug: GitHub team slug (e.g., "engineering-core") - required for sync
        github_org: GitHub organization
        auto_sync_github: Whether to sync to GitHub
        is_active: Active status (set to false to archive)

    Returns:
        Updated team data, or error if not found/accessible
    """
    with make_session() as session:
        user = get_mcp_current_user(session, full=True)
        if not user:
            return {"error": "Not authenticated"}

        # Apply access control filtering
        query = filter_teams_query(session, user, session.query(Team))

        if isinstance(team, int) or (isinstance(team, str) and team.isdigit()):
            team_obj = query.filter(Team.id == int(team)).first()
        else:
            team_obj = query.filter(Team.slug == team).first()

        if not team_obj:
            return {"error": f"Team not found: {team}"}

        # Update fields if provided
        if name is not None:
            team_obj.name = name
        if description is not None:
            team_obj.description = description
        if tags is not None:
            team_obj.tags = tags
        if discord_role_id is not None:
            team_obj.discord_role_id = discord_role_id
        if discord_guild_id is not None:
            team_obj.discord_guild_id = discord_guild_id
        if auto_sync_discord is not None:
            team_obj.auto_sync_discord = auto_sync_discord
        if github_team_id is not None:
            team_obj.github_team_id = github_team_id
        if github_team_slug is not None:
            team_obj.github_team_slug = github_team_slug
        if github_org is not None:
            team_obj.github_org = github_org
        if auto_sync_github is not None:
            team_obj.auto_sync_github = auto_sync_github
        if is_active is not None:
            team_obj.is_active = is_active
            # Only set archived_at when transitioning to inactive for the first time
            if not is_active and team_obj.archived_at is None:
                from datetime import datetime, timezone
                team_obj.archived_at = datetime.now(timezone.utc)

        session.commit()
        session.refresh(team_obj)

        return {"success": True, "team": _team_to_dict(team_obj)}


# ============== Team Membership ==============


@teams_mcp.tool()
@visible_when(require_scopes("teams"))
async def team_add_member(
    team: str | int,
    person: str | int,
    role: str = "member",
    sync_external: bool = True,
) -> dict:
    """
    Add a person to a team.

    If the team has Discord/GitHub integration enabled and sync_external is true,
    this will also add the person to the corresponding Discord role and/or GitHub team.

    Args:
        team: Team slug or ID
        person: Person identifier or ID
        role: Team role - "member", "lead", or "admin" (default: "member")
        sync_external: Whether to sync to Discord/GitHub (default: true)

    Returns:
        Result with sync status, or error if team not found/accessible
    """
    logger.info(f"MCP: Adding {person} to team {team}")

    if role not in ("member", "lead", "admin"):
        return {"error": f"Invalid role: {role}. Must be 'member', 'lead', or 'admin'"}

    with make_session() as session:
        user = get_mcp_current_user(session, full=True)
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
        if isinstance(person, int) or (isinstance(person, str) and person.isdigit()):
            person_obj = session.query(Person).options(
                selectinload(Person.discord_accounts),
                selectinload(Person.github_accounts),
            ).filter(Person.id == int(person)).first()
        else:
            person_obj = session.query(Person).options(
                selectinload(Person.discord_accounts),
                selectinload(Person.github_accounts),
            ).filter(Person.identifier == person).first()

        if not person_obj:
            return {"error": f"Person not found: {person}"}

        # Check if already a member
        if person_obj in team_obj.members:
            return {
                "success": True,
                "message": f"{person_obj.identifier} is already a member of {team_obj.slug}",
                "already_member": True,
            }

        # Add to team with explicit role via junction table insert
        # (Using relationship.append() would only set the server default "member" role)
        from memory.common.db.models.sources import team_members

        session.execute(
            team_members.insert().values(
                team_id=team_obj.id,
                person_id=person_obj.id,
                role=role,
            )
        )
        session.commit()

        # Refresh to get updated members list
        session.refresh(team_obj)

        result = {
            "success": True,
            "team": team_obj.slug,
            "person": person_obj.identifier,
            "role": role,
            "sync": {},
        }

        # Sync to external services
        if sync_external:
            result["sync"] = await _sync_membership_add(team_obj, person_obj)

        return result


@teams_mcp.tool()
@visible_when(require_scopes("teams"))
async def team_remove_member(
    team: str | int,
    person: str | int,
    sync_external: bool = True,
) -> dict:
    """
    Remove a person from a team.

    If the team has Discord/GitHub integration enabled and sync_external is true,
    this will also remove the person from the corresponding Discord role and/or GitHub team.

    Args:
        team: Team slug or ID
        person: Person identifier or ID
        sync_external: Whether to sync to Discord/GitHub (default: true)

    Returns:
        Result with sync status, or error if team not found/accessible
    """
    logger.info(f"MCP: Removing {person} from team {team}")

    with make_session() as session:
        user = get_mcp_current_user(session, full=True)
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
        if isinstance(person, int) or (isinstance(person, str) and person.isdigit()):
            person_obj = session.query(Person).options(
                selectinload(Person.discord_accounts),
                selectinload(Person.github_accounts),
            ).filter(Person.id == int(person)).first()
        else:
            person_obj = session.query(Person).options(
                selectinload(Person.discord_accounts),
                selectinload(Person.github_accounts),
            ).filter(Person.identifier == person).first()

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

        result = {
            "success": True,
            "team": team_obj.slug,
            "person": person_obj.identifier,
            "sync": {},
        }

        # Sync to external services
        if sync_external:
            result["sync"] = await _sync_membership_remove(team_obj, person_obj)

        return result


@teams_mcp.tool()
@visible_when(require_scopes("teams"))
async def team_list_members(
    team: str | int,
) -> dict:
    """
    List all members of a team.

    Args:
        team: Team slug or ID

    Returns:
        List of team members with their roles, or error if team not found/accessible
    """
    with make_session() as session:
        user = get_mcp_current_user(session, full=True)
        if not user:
            return {"error": "Not authenticated"}

        # Apply access control filtering
        query = filter_teams_query(session, user, session.query(Team).options(selectinload(Team.members)))

        if isinstance(team, int) or (isinstance(team, str) and team.isdigit()):
            team_obj = query.filter(Team.id == int(team)).first()
        else:
            team_obj = query.filter(Team.slug == team).first()

        if not team_obj:
            return {"error": f"Team not found: {team}"}

        # Fetch member roles from junction table
        role_query = session.execute(
            select(team_members.c.person_id, team_members.c.role)
            .where(team_members.c.team_id == team_obj.id)
        )
        roles = {row.person_id: row.role for row in role_query}

        return {
            "team": team_obj.slug,
            "team_name": team_obj.name,
            "members": [
                {**_person_summary(p), "role": roles.get(p.id, "member")}
                for p in team_obj.members
            ],
            "count": len(team_obj.members),
        }


# ============== External Service Sync ==============


async def _sync_membership_add(team: Team, person: Person) -> dict[str, Any]:
    """Sync membership addition to Discord and GitHub."""
    result: dict[str, Any] = {"discord": None, "github": None}

    # Discord sync
    if team.auto_sync_discord and team.discord_role_id and team.discord_guild_id:
        discord_result = await _discord_add_role(team, person)
        result["discord"] = discord_result

    # GitHub sync
    if team.auto_sync_github and team.github_team_id and team.github_org:
        github_result = await _github_add_team_member(team, person)
        result["github"] = github_result

    return result


async def _sync_membership_remove(team: Team, person: Person) -> dict[str, Any]:
    """Sync membership removal to Discord and GitHub."""
    result: dict[str, Any] = {"discord": None, "github": None}

    # Discord sync
    if team.auto_sync_discord and team.discord_role_id and team.discord_guild_id:
        discord_result = await _discord_remove_role(team, person)
        result["discord"] = discord_result

    # GitHub sync
    if team.auto_sync_github and team.github_team_id and team.github_org:
        github_result = await _github_remove_team_member(team, person)
        result["github"] = github_result

    return result


async def _discord_add_role(team: Team, person: Person) -> dict[str, Any]:
    """Add Discord role to person's Discord accounts."""
    from memory.api.MCP.servers.discord import add_user_to_role

    results = []
    errors = []

    for discord_account in person.discord_accounts:
        try:
            result = await add_user_to_role(
                guild_id=str(team.discord_guild_id),
                role_id=team.discord_role_id,
                user_id=discord_account.id,
            )
            if "error" in result:
                errors.append(f"{discord_account.username}: {result['error']}")
            else:
                results.append(discord_account.username)
        except Exception as e:
            errors.append(f"{discord_account.username}: {str(e)}")

    return {
        "success": len(errors) == 0,
        "users_added": results,
        "errors": errors,
    }


async def _discord_remove_role(team: Team, person: Person) -> dict[str, Any]:
    """Remove Discord role from person's Discord accounts."""
    from memory.api.MCP.servers.discord import role_remove

    results = []
    errors = []

    for discord_account in person.discord_accounts:
        try:
            result = await role_remove(
                guild_id=str(team.discord_guild_id),
                role_id=team.discord_role_id,
                user_id=discord_account.id,
            )
            if "error" in result:
                errors.append(f"{discord_account.username}: {result['error']}")
            else:
                results.append(discord_account.username)
        except Exception as e:
            errors.append(f"{discord_account.username}: {str(e)}")

    return {
        "success": len(errors) == 0,
        "users_removed": results,
        "errors": errors,
    }


async def _github_add_team_member(team: Team, person: Person) -> dict[str, Any]:
    """Add person's GitHub accounts to GitHub team."""
    from memory.api.MCP.servers.github import add_team_member

    # Require github_team_slug for API calls
    if not team.github_team_slug:
        return {
            "success": False,
            "users_added": [],
            "errors": [f"Team {team.slug} has github_team_id but no github_team_slug - cannot sync"],
        }

    results = []
    errors = []

    for github_account in person.github_accounts:
        try:
            result = await add_team_member(
                org=team.github_org,
                team_slug=team.github_team_slug,
                username=github_account.username,
            )
            if "error" in result:
                errors.append(f"{github_account.username}: {result['error']}")
            else:
                results.append(github_account.username)
        except Exception as e:
            errors.append(f"{github_account.username}: {str(e)}")

    return {
        "success": len(errors) == 0,
        "users_added": results,
        "errors": errors,
    }


async def _github_remove_team_member(team: Team, person: Person) -> dict[str, Any]:
    """Remove person's GitHub accounts from GitHub team."""
    from memory.api.MCP.servers.github import remove_team_member

    # Require github_team_slug for API calls
    if not team.github_team_slug:
        return {
            "success": False,
            "users_removed": [],
            "errors": [f"Team {team.slug} has github_team_id but no github_team_slug - cannot sync"],
        }

    results = []
    errors = []

    for github_account in person.github_accounts:
        try:
            result = await remove_team_member(
                org=team.github_org,
                team_slug=team.github_team_slug,
                username=github_account.username,
            )
            if "error" in result:
                errors.append(f"{github_account.username}: {result['error']}")
            else:
                results.append(github_account.username)
        except Exception as e:
            errors.append(f"{github_account.username}: {str(e)}")

    return {
        "success": len(errors) == 0,
        "users_removed": results,
        "errors": errors,
    }


# ============== Query Helpers ==============


@teams_mcp.tool()
@visible_when(require_scopes("teams"))
async def teams_by_tag(
    tags: list[str],
    match_all: bool = True,
) -> dict:
    """
    Find teams by tags.

    Args:
        tags: Tags to search for
        match_all: If true, teams must have ALL tags. If false, ANY tag matches.

    Returns:
        List of matching teams (filtered to teams the user has access to)
    """
    with make_session() as session:
        user = get_mcp_current_user(session, full=True)
        if not user:
            return {"error": "Not authenticated", "teams": [], "count": 0}

        query = session.query(Team).filter(Team.is_active == True)  # noqa: E712

        # Apply access control filtering
        query = filter_teams_query(session, user, query)

        if match_all:
            # Teams must have ALL specified tags (PostgreSQL array contains)
            query = query.filter(Team.tags.op("@>")(cast(tags, PG_ARRAY(Text))))
        else:
            # Teams must have ANY of the specified tags
            from sqlalchemy import or_
            conditions = [Team.tags.op("@>")(cast([tag], PG_ARRAY(Text))) for tag in tags]
            query = query.filter(or_(*conditions))

        teams = query.order_by(Team.name).all()

        return {
            "teams": [_team_to_dict(t, include_members=False) for t in teams],
            "count": len(teams),
        }


@teams_mcp.tool()
@visible_when(require_scopes("teams"))
async def person_teams(
    person: str | int,
) -> dict:
    """
    List all teams a person belongs to.

    Args:
        person: Person identifier or ID

    Returns:
        List of teams the person is a member of (filtered to teams the user has access to)
    """
    with make_session() as session:
        user = get_mcp_current_user(session, full=True)
        if not user:
            return {"error": "Not authenticated"}

        if isinstance(person, int) or (isinstance(person, str) and person.isdigit()):
            person_obj = session.query(Person).options(selectinload(Person.teams)).filter(Person.id == int(person)).first()
        else:
            person_obj = session.query(Person).options(selectinload(Person.teams)).filter(Person.identifier == person).first()

        if not person_obj:
            return {"error": f"Person not found: {person}"}

        # Filter teams to only those the user can access
        if has_admin_scope(user):
            accessible_teams = person_obj.teams
        else:
            accessible_ids = get_accessible_team_ids(session, user)
            accessible_teams = [t for t in person_obj.teams if t.id in accessible_ids]

        return {
            "person": person_obj.identifier,
            "teams": [_team_to_dict(t, include_members=False) for t in accessible_teams],
            "count": len(accessible_teams),
        }


# ============== Project-Team Assignment ==============


def _project_summary(project: Project) -> dict[str, Any]:
    """Brief project summary for responses."""
    return {
        "id": project.id,
        "title": project.title,
        "slug": project.slug,
        "state": project.state,
    }


@teams_mcp.tool()
@visible_when(require_scopes("teams"))
async def project_assign_team(
    project: int | str,
    team: str | int,
) -> dict:
    """
    Assign a team to a project, granting all team members access.

    Args:
        project: Project ID or slug (e.g., "owner/repo:number")
        team: Team slug or ID

    Returns:
        Assignment result, or error if project/team not found/accessible
    """
    logger.info(f"MCP: Assigning team {team} to project {project}")

    with make_session() as session:
        user = get_mcp_current_user(session, full=True)
        if not user:
            return {"error": "Not authenticated"}

        # Find project with access control
        project_obj = _find_project_with_access(session, user, project)
        if not project_obj:
            return {"error": f"Project not found: {project}"}

        # Find team with access control
        team_obj = _find_team_with_access(session, user, team)
        if not team_obj:
            return {"error": f"Team not found: {team}"}

        # Check if already assigned
        if team_obj in project_obj.teams:
            return {
                "success": True,
                "message": f"Team {team_obj.slug} is already assigned to project {project_obj.title}",
                "already_assigned": True,
            }

        # Assign team
        project_obj.teams.append(team_obj)
        session.commit()

        return {
            "success": True,
            "project": _project_summary(project_obj),
            "team": {"slug": team_obj.slug, "name": team_obj.name},
        }


@teams_mcp.tool()
@visible_when(require_scopes("teams"))
async def project_unassign_team(
    project: int | str,
    team: str | int,
) -> dict:
    """
    Remove a team's assignment from a project.

    Args:
        project: Project ID or slug
        team: Team slug or ID

    Returns:
        Result, or error if project/team not found/accessible
    """
    logger.info(f"MCP: Unassigning team {team} from project {project}")

    with make_session() as session:
        user = get_mcp_current_user(session, full=True)
        if not user:
            return {"error": "Not authenticated"}

        # Find project with access control
        project_obj = _find_project_with_access(session, user, project)
        if not project_obj:
            return {"error": f"Project not found: {project}"}

        # Find team with access control
        team_obj = _find_team_with_access(session, user, team)
        if not team_obj:
            return {"error": f"Team not found: {team}"}

        # Check if assigned
        if team_obj not in project_obj.teams:
            return {
                "success": True,
                "message": f"Team {team_obj.slug} is not assigned to project {project_obj.title}",
                "was_not_assigned": True,
            }

        # Remove assignment
        project_obj.teams.remove(team_obj)
        session.commit()

        return {
            "success": True,
            "project": _project_summary(project_obj),
            "team": {"slug": team_obj.slug, "name": team_obj.name},
        }


@teams_mcp.tool()
@visible_when(require_scopes("teams"))
async def project_list_teams(
    project: int | str,
) -> dict:
    """
    List all teams assigned to a project.

    Args:
        project: Project ID or slug

    Returns:
        List of assigned teams, or error if project not found/accessible
    """
    with make_session() as session:
        user = get_mcp_current_user(session, full=True)
        if not user:
            return {"error": "Not authenticated"}

        project_obj = _find_project_with_access(session, user, project)
        if not project_obj:
            return {"error": f"Project not found: {project}"}

        return {
            "project": _project_summary(project_obj),
            "teams": [_team_to_dict(t, include_members=False) for t in project_obj.teams],
            "count": len(project_obj.teams),
        }


@teams_mcp.tool()
@visible_when(require_scopes("teams"))
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
        user = get_mcp_current_user(session, full=True)
        if not user:
            return {"error": "Not authenticated"}

        project_obj = _find_project_with_access(session, user, project)
        if not project_obj:
            return {"error": f"Project not found: {project}"}

        # Collect all people with access
        people_with_access: dict[int, Person] = {}
        teams_data = []

        for team in project_obj.teams:
            team_members = []
            for person in team.members:
                people_with_access[person.id] = person
                team_members.append(_person_summary(person))
            teams_data.append({
                "team": team.slug,
                "team_name": team.name,
                "members": team_members,
            })

        return {
            "project": _project_summary(project_obj),
            "teams": teams_data,
            "all_people": [_person_summary(p) for p in people_with_access.values()],
            "total_people_count": len(people_with_access),
        }


@teams_mcp.tool()
@visible_when(require_scopes("teams"))
async def projects_for_person(
    person: str | int,
) -> dict:
    """
    List all projects a person can access (through team membership).

    Args:
        person: Person identifier or ID

    Returns:
        List of accessible projects (filtered to projects the user can see)
    """
    with make_session() as session:
        user = get_mcp_current_user(session, full=True)
        if not user:
            return {"error": "Not authenticated"}

        if isinstance(person, int) or (isinstance(person, str) and person.isdigit()):
            person_obj = session.query(Person).options(
                selectinload(Person.teams).selectinload(Team.projects)
            ).filter(Person.id == int(person)).first()
        else:
            person_obj = session.query(Person).options(
                selectinload(Person.teams).selectinload(Team.projects)
            ).filter(Person.identifier == person).first()

        if not person_obj:
            return {"error": f"Person not found: {person}"}

        # Collect all accessible projects (for the queried person)
        projects: dict[int, Project] = {}
        for team in person_obj.teams:
            for project in team.projects:
                projects[project.id] = project

        # Filter to only projects the current user can also see
        if has_admin_scope(user):
            visible_projects = list(projects.values())
        else:
            accessible_ids = get_accessible_project_ids(session, user)
            visible_projects = [p for p in projects.values() if p.id in accessible_ids]

        return {
            "person": person_obj.identifier,
            "projects": [_project_summary(p) for p in visible_projects],
            "count": len(visible_projects),
        }


@teams_mcp.tool()
@visible_when(require_scopes("teams"))
async def check_project_access(
    person: str | int,
    project: int | str,
) -> dict:
    """
    Check if a person can access a specific project.

    Args:
        person: Person identifier or ID
        project: Project ID or slug

    Returns:
        Access status and which teams grant access,
        or error if project not found/accessible to current user
    """
    with make_session() as session:
        user = get_mcp_current_user(session, full=True)
        if not user:
            return {"error": "Not authenticated"}

        # Find person
        if isinstance(person, int) or (isinstance(person, str) and person.isdigit()):
            person_obj = session.query(Person).options(
                selectinload(Person.teams)
            ).filter(Person.id == int(person)).first()
        else:
            person_obj = session.query(Person).options(
                selectinload(Person.teams)
            ).filter(Person.identifier == person).first()

        if not person_obj:
            return {"error": f"Person not found: {person}"}

        # Find project with access control
        project_obj = _find_project_with_access(session, user, project)
        if not project_obj:
            return {"error": f"Project not found: {project}"}

        # Check access
        has_access = can_access_project(person_obj, project_obj)

        # Find which teams grant access
        granting_teams = []
        if has_access:
            person_team_ids = {t.id for t in person_obj.teams}
            for team in project_obj.teams:
                if team.id in person_team_ids:
                    granting_teams.append({"slug": team.slug, "name": team.name})

        return {
            "person": person_obj.identifier,
            "project": _project_summary(project_obj),
            "has_access": has_access,
            "granting_teams": granting_teams,
        }


# ============== Helper Functions ==============


def _find_project_with_access(session: Session, user: User, project: int | str) -> Project | None:
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


def _find_team_with_access(session: Session, user: User, team: str | int) -> Team | None:
    """Find a team by ID or slug, with access control filtering."""
    query = session.query(Team).options(selectinload(Team.members))

    # Apply access control filtering
    query = filter_teams_query(session, user, query)

    if isinstance(team, int):
        return query.filter(Team.id == team).first()

    if team.isdigit():
        return query.filter(Team.id == int(team)).first()

    return query.filter(Team.slug == team).first()
