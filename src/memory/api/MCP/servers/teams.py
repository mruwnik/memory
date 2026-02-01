"""MCP subserver for team management."""

import asyncio
import logging
import re
from typing import Any

from fastmcp import FastMCP
from sqlalchemy import Text, cast, or_, select
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
from memory.common.db.models import (
    DiscordUser,
    Person,
    Project,
    Team,
    User,
    can_access_project,
)
from memory.common.db.models.sources import (
    GithubRepo,
    GithubUser,
    team_members,
)
from memory.common import discord as discord_client
from memory.api.MCP.servers.discord import (
    add_user_to_role,
    resolve_bot_id,
    role_remove,
)
from memory.api.MCP.servers.github import add_team_member, remove_team_member
from memory.api.MCP.servers.github_helpers import get_github_client_for_org

logger = logging.getLogger(__name__)

teams_mcp = FastMCP("memory-teams")


def sanitize_error(e: Exception, context: str) -> str:
    """Sanitize error for external API response.

    Logs the full error internally but returns a generic message to avoid
    leaking internal details (stack traces, file paths, connection strings).
    """
    logger.warning(f"{context}: {type(e).__name__}: {e}")
    return f"{context}: operation failed"


def team_to_dict(team: Team, include_members: bool = False, include_projects: bool = False) -> dict[str, Any]:
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
    session: Session,
    slug: str,
    name: str,
    description: str | None,
    tags: list[str] | None,
) -> tuple[Team, str]:
    """Create or update the Team record.

    Returns:
        Tuple of (team, action) where action is "created" or "updated"
    """
    team = session.query(Team).filter(Team.slug == slug).first()

    if team:
        if name:
            team.name = name
        if description is not None:
            team.description = description
        if tags is not None:
            team.tags = tags
        return team, "updated"

    team = Team(
        name=name,
        slug=slug,
        description=description,
        tags=tags or [],
    )
    session.add(team)
    session.flush()
    return team, "created"


def setup_discord_integration(
    session: Session,
    team: Team,
    guild: int | str | None,
    discord_role: int | str | None,
    auto_sync_discord: bool,
) -> tuple[int | None, int | None, bool, list[str], dict[str, Any]]:
    """Configure Discord integration for a team.

    Returns:
        Tuple of (resolved_guild_id, resolved_role_id, role_created, warnings, sync_info)
    """
    warnings: list[str] = []
    sync_info: dict[str, Any] = {}

    if guild is None:
        team.auto_sync_discord = auto_sync_discord
        return None, None, False, warnings, sync_info

    resolved_guild_id = discord_client.resolve_guild(guild, session)
    team.discord_guild_id = resolved_guild_id

    resolved_role_id = None
    role_created = False

    if discord_role is not None and resolved_guild_id is not None:
        try:
            bot_id = resolve_bot_id(None)
            resolved_role_id, role_created = discord_client.resolve_role(
                discord_role,
                resolved_guild_id,
                bot_id,
                create_if_missing=True,
            )
            team.discord_role_id = resolved_role_id
            if role_created:
                sync_info["role_created"] = True
                sync_info["role_name"] = discord_role if isinstance(discord_role, str) else None
        except Exception as e:
            warnings.append(f"Discord role resolution failed: {e}")

    team.auto_sync_discord = auto_sync_discord
    return resolved_guild_id, resolved_role_id, role_created, warnings, sync_info


async def setup_github_integration(
    session: Session,
    team: Team,
    name: str,
    github_org: str | None,
    github_team_slug: str | None,
    auto_sync_github: bool,
) -> tuple[list[str], dict[str, Any]]:
    """Configure GitHub integration for a team.

    Returns:
        Tuple of (warnings, sync_info)
    """
    team.auto_sync_github = auto_sync_github

    if github_org is None:
        return [], {}

    team.github_org = github_org

    if github_team_slug is None:
        return [], {}

    team.github_team_slug = github_team_slug

    try:
        github_team_data = await ensure_github_team(
            session, github_org, github_team_slug, name
        )
    except Exception as e:
        return [f"GitHub team resolution failed: {e}"], {}

    if not github_team_data:
        return [], {}

    sync_info: dict[str, Any] = {}
    if github_team_data.get("created"):
        sync_info["team_created"] = True
    if github_team_data.get("id"):
        team.github_team_id = github_team_data["id"]

    return [], sync_info


async def import_external_members(
    session: Session,
    team: Team,
    result: dict[str, Any],
    resolved_guild_id: int | None,
    resolved_role_id: int | None,
    role_created: bool,
) -> None:
    """Import members from linked Discord role and GitHub team.

    Uses union strategy: adds members from external services without removing existing ones.
    Only imports from Discord role if linking to an existing role (not when creating a new one).
    """
    # Sync members from Discord if linking to existing role
    if resolved_guild_id and resolved_role_id and not role_created:
        sync_result = await sync_from_discord(
            session, team, resolved_guild_id, resolved_role_id
        )
        result["discord_sync"]["imported_members"] = sync_result.get("imported", 0)
        result["discord_sync"]["created_people"] = sync_result.get("created_people", [])

    # Sync members from GitHub if team exists (skip if we just created it)
    if not team.github_org or not team.github_team_slug:
        return

    if result.get("github_sync", {}).get("team_created"):
        return

    try:
        sync_result = await sync_from_github(
            session, team, team.github_org, team.github_team_slug
        )
        result["github_sync"]["imported_members"] = sync_result.get("imported", 0)
        result["github_sync"]["created_people"] = sync_result.get("created_people", [])
    except Exception as e:
        result["warnings"].append(f"GitHub member sync failed: {e}")


@teams_mcp.tool()
@visible_when(require_scopes("teams"))
async def upsert(
    name: str,
    slug: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    # Discord integration
    guild: int | str | None = None,
    discord_role: int | str | None = None,
    auto_sync_discord: bool = True,
    # GitHub integration
    github_org: str | None = None,
    github_team_slug: str | None = None,
    auto_sync_github: bool = True,
    # Membership
    members: list[str] | None = None,
) -> dict:
    """
    Create or update a team with optional Discord/GitHub integration.

    Args:
        name: Team display name
        slug: URL-safe identifier (auto-generated from name if not provided)
        description: Optional description of the team's purpose
        tags: Tags for categorization (e.g., ["engineering", "core"])
        guild: Discord guild - can be numeric ID or server name
        discord_role: Discord role - can be numeric ID or role name (creates if doesn't exist)
        auto_sync_discord: Whether to auto-sync membership to Discord (default: true)
        github_org: GitHub organization for team sync
        github_team_slug: GitHub team slug - creates team if doesn't exist
        auto_sync_github: Whether to auto-sync membership to GitHub (default: true)
        members: If provided, set team to exactly these members.
                 Pass [] to remove all members. Pass None to leave unchanged.

    Behavior:
        - If team with slug exists: updates it
        - If discord_role name doesn't exist: creates new role
        - If github_team_slug doesn't exist: creates team using `name`
        - Members from Discord role and GitHub team are auto-added (union)
        - Person records created for external users not yet in system

    Returns:
        Dict with team data and sync results
    """
    if not slug:
        slug = make_slug(name)

    logger.info(f"MCP: Upserting team: {slug}")

    result: dict[str, Any] = {
        "success": True,
        "action": None,
        "team": None,
        "discord_sync": {},
        "github_sync": {},
        "membership_changes": {},
        "warnings": [],
    }

    with make_session() as session:
        team, action = upsert_team_record(session, slug, name, description, tags)
        result["action"] = action

        # Configure Discord integration
        resolved_guild_id, resolved_role_id, role_created, discord_warnings, discord_sync = (
            setup_discord_integration(session, team, guild, discord_role, auto_sync_discord)
        )
        result["warnings"].extend(discord_warnings)
        result["discord_sync"].update(discord_sync)

        # Configure GitHub integration
        github_warnings, github_sync = await setup_github_integration(
            session, team, name, github_org, github_team_slug, auto_sync_github
        )
        result["warnings"].extend(github_warnings)
        result["github_sync"].update(github_sync)

        session.flush()

        # Import members from linked external services
        await import_external_members(
            session, team, result,
            resolved_guild_id, resolved_role_id, role_created,
        )

        # Handle explicit member list
        if members is not None:
            membership_result = await set_team_members(session, team, members)
            result["membership_changes"] = membership_result

        session.commit()
        session.refresh(team)

        result["team"] = team_to_dict(team, include_members=True)

    return result


async def ensure_github_team(
    session: Session,
    org: str,
    team_slug: str,
    team_name: str,
) -> dict[str, Any] | None:
    """Ensure a GitHub team exists, creating it if necessary.

    Returns:
        Dict with "id", "slug", and "created" (bool) keys, or None on failure
    """
    user = get_mcp_current_user(session=session, full=True)
    if not user or user.id is None:
        return None

    client = get_github_client_for_org(session, org, user.id)
    if not client:
        return None

    # Check if team exists (run blocking API call in thread)
    existing = await asyncio.to_thread(client.fetch_team, org, team_slug)
    if existing:
        return {
            "id": existing.get("github_id"),
            "slug": existing.get("slug"),
            "created": False,
        }

    # Create team (run blocking API call in thread)
    new_team = await asyncio.to_thread(client.create_team, org, team_name)
    if new_team:
        return {
            "id": new_team.get("id"),
            "slug": new_team.get("slug"),
            "created": True,
        }

    return None


def import_external_user_to_team(
    session: Session,
    team: Team,
    external_user: Any,
    identifier: str,
    display_name: str,
    contact_info: dict[str, Any],
    result: dict[str, Any],
) -> None:
    """Import an external user (Discord/GitHub) to a team.

    Common logic for both Discord and GitHub sync:
    - If external_user has a linked person, use that
    - Otherwise, create a new person and link if external_user exists
    - Add person to team if not already a member

    Args:
        session: Database session
        team: Team to add the user to
        external_user: DiscordUser or GithubUser object, or None
        identifier: Username/identifier for creating person
        display_name: Display name for creating person
        contact_info: Contact info dict for creating person
        result: Mutable result dict to update (imported count, created_people list)
    """
    if external_user and external_user.person:
        person = external_user.person
    else:
        person = find_or_create_person(
            session,
            identifier=identifier,
            display_name=display_name,
            contact_info=contact_info,
        )
        result["created_people"].append(identifier)

        # Link external user to Person if it exists but wasn't linked
        if external_user and person:
            external_user.person_id = person.id

    if person and person not in team.members:
        team.members.append(person)
        result["imported"] += 1


async def sync_from_discord(
    session: Session,
    team: Team,
    guild_id: int,
    role_id: int,
) -> dict[str, Any]:
    """Import members from Discord role to team.

    Creates Person records for Discord users not in system.

    Note: The asyncio.to_thread call runs HTTP operations only - session queries
    happen after the thread returns to ensure thread safety.
    """
    result: dict[str, Any] = {"imported": 0, "created_people": []}

    try:
        bot_id = resolve_bot_id(None)
        members_data = await asyncio.to_thread(
            discord_client.list_role_members, bot_id, guild_id, role_id
        )
        if not members_data:
            return result

        for member in members_data.get("members", []):
            discord_id = member.get("id")
            username = member.get("username")
            if not discord_id:
                continue

            discord_user = (
                session.query(DiscordUser)
                .filter(DiscordUser.id == int(discord_id))
                .first()
            )

            import_external_user_to_team(
                session=session,
                team=team,
                external_user=discord_user,
                identifier=username,
                display_name=member.get("display_name") or username,
                contact_info={"discord_id": str(discord_id)},
                result=result,
            )

    except Exception as e:
        logger.warning(f"Discord sync failed for team {team.slug}: {e}")

    return result


async def sync_from_github(
    session: Session,
    team: Team,
    org: str,
    team_slug: str,
) -> dict[str, Any]:
    """Import members from GitHub team to internal team.

    Creates Person records for GitHub users not in system.

    Note: The asyncio.to_thread call runs HTTP operations only - session queries
    happen after the thread returns to ensure thread safety.
    """
    result: dict[str, Any] = {"imported": 0, "created_people": []}

    user = get_mcp_current_user(session=session, full=True)
    if not user or user.id is None:
        return result

    client = get_github_client_for_org(session, org, user.id)
    if not client:
        return result

    try:
        github_members = await asyncio.to_thread(
            client.get_team_members, org, team_slug
        )

        for member in github_members:
            login = member.get("login")
            if not login:
                continue

            github_user = (
                session.query(GithubUser)
                .filter(GithubUser.username == login)
                .first()
            )

            import_external_user_to_team(
                session=session,
                team=team,
                external_user=github_user,
                identifier=login,
                display_name=login,
                contact_info={"github_username": login},
                result=result,
            )

    except Exception as e:
        logger.warning(f"GitHub sync failed for team {team.slug}: {e}")

    return result


def find_or_create_person(
    session: Session,
    identifier: str,
    display_name: str,
    contact_info: dict[str, Any] | None = None,
) -> Person:
    """Find existing person by identifier or create new one."""
    # Normalize identifier
    normalized = re.sub(r"\s+", "_", identifier.lower().strip())
    normalized = "".join(c for c in normalized if c.isalnum() or c == "_")

    # Check for existing
    existing = session.query(Person).filter(Person.identifier == normalized).first()
    if existing:
        return existing

    # Create new
    person = Person(
        identifier=normalized,
        display_name=display_name,
        contact_info=contact_info or {},
    )
    session.add(person)
    session.flush()
    logger.info(f"Created person '{normalized}' for external user")
    return person


async def set_team_members(
    session: Session,
    team: Team,
    member_identifiers: list[str],
) -> dict[str, Any]:
    """Set team to exactly these members, syncing to external services.

    Returns dict with added/removed counts and any warnings.
    """
    current_members = {p.identifier for p in team.members}
    target_members = set(member_identifiers)

    to_add = target_members - current_members
    to_remove = current_members - target_members

    result: dict[str, Any] = {
        "added": [],
        "removed": [],
        "created_people": [],
        "warnings": [],
    }

    # Remove members no longer in list
    for identifier in to_remove:
        person = session.query(Person).filter(Person.identifier == identifier).first()
        if person and person in team.members:
            team.members.remove(person)
            await sync_membership_remove(team, person)
            result["removed"].append(identifier)

    # Add new members
    for identifier in to_add:
        person = session.query(Person).filter(Person.identifier == identifier).first()
        if not person:
            # Try to find by display_name or create
            # Note: ilike() is case-insensitive and may not use index without a
            # functional index on LOWER(display_name). This is acceptable for member
            # sync operations which typically involve small result sets.
            person = session.query(Person).filter(
                Person.display_name.ilike(identifier)
            ).first()

        if not person:
            # Create Person if not found
            person = find_or_create_person(
                session,
                identifier=identifier,
                display_name=identifier,
            )
            result["created_people"].append(identifier)

        if person not in team.members:
            team.members.append(person)
            await sync_membership_add(team, person)
            result["added"].append(identifier)

    # Warning for clearing all members
    if not target_members and current_members:
        result["warnings"].append(f"Removed all {len(to_remove)} members from team")

    return result


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
            return {"error": "Not authenticated"}

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
            return {"error": f"Team not found: {team}"}

        return {"team": team_to_dict(team_obj, include_members=include_members, include_projects=include_projects)}


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
            return {"error": "Not authenticated"}

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
            "teams": [team_to_dict(t, include_members=False, include_projects=include_projects) for t in teams],
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

        return {"success": True, "team": team_to_dict(team_obj)}


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
            result["sync"] = await sync_membership_add(team_obj, person_obj)

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
            result["sync"] = await sync_membership_remove(team_obj, person_obj)

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
                {**person_summary(p), "role": roles.get(p.id, "member")}
                for p in team_obj.members
            ],
            "count": len(team_obj.members),
        }


# ============== External Service Sync ==============


async def sync_membership_add(team: Team, person: Person) -> dict[str, Any]:
    """Sync membership addition to Discord and GitHub."""
    result: dict[str, Any] = {"discord": None, "github": None}

    # Discord sync
    if team.auto_sync_discord and team.discord_role_id and team.discord_guild_id:
        discord_result = await discord_add_role(team, person)
        result["discord"] = discord_result

    # GitHub sync
    if team.auto_sync_github and team.github_team_id and team.github_org:
        github_result = await github_add_team_member(team, person)
        result["github"] = github_result

    return result


async def sync_membership_remove(team: Team, person: Person) -> dict[str, Any]:
    """Sync membership removal to Discord and GitHub."""
    result: dict[str, Any] = {"discord": None, "github": None}

    # Discord sync
    if team.auto_sync_discord and team.discord_role_id and team.discord_guild_id:
        discord_result = await discord_remove_role(team, person)
        result["discord"] = discord_result

    # GitHub sync
    if team.auto_sync_github and team.github_team_id and team.github_org:
        github_result = await github_remove_team_member(team, person)
        result["github"] = github_result

    return result


async def discord_add_role(team: Team, person: Person) -> dict[str, Any]:
    """Add Discord role to person's Discord accounts."""
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
            errors.append(sanitize_error(e, f"Discord add role for {discord_account.username}"))

    return {
        "success": len(errors) == 0,
        "users_added": results,
        "errors": errors,
    }


async def discord_remove_role(team: Team, person: Person) -> dict[str, Any]:
    """Remove Discord role from person's Discord accounts."""
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
            errors.append(sanitize_error(e, f"Discord remove role for {discord_account.username}"))

    return {
        "success": len(errors) == 0,
        "users_removed": results,
        "errors": errors,
    }


async def github_add_team_member(team: Team, person: Person) -> dict[str, Any]:
    """Add person's GitHub accounts to GitHub team."""
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
            errors.append(sanitize_error(e, f"GitHub add team member {github_account.username}"))

    return {
        "success": len(errors) == 0,
        "users_added": results,
        "errors": errors,
    }


async def github_remove_team_member(team: Team, person: Person) -> dict[str, Any]:
    """Remove person's GitHub accounts from GitHub team."""
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
            errors.append(sanitize_error(e, f"GitHub remove team member {github_account.username}"))

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
            return {"error": "Not authenticated"}

        query = session.query(Team).filter(Team.is_active == True)  # noqa: E712

        # Apply access control filtering
        query = filter_teams_query(session, user, query)

        if match_all:
            # Teams must have ALL specified tags (PostgreSQL array contains)
            query = query.filter(Team.tags.op("@>")(cast(tags, PG_ARRAY(Text))))
        else:
            # Teams must have ANY of the specified tags
            conditions = [Team.tags.op("@>")(cast([tag], PG_ARRAY(Text))) for tag in tags]
            query = query.filter(or_(*conditions))

        teams = query.order_by(Team.name).all()

        return {
            "teams": [team_to_dict(t, include_members=False) for t in teams],
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
            "teams": [team_to_dict(t, include_members=False) for t in accessible_teams],
            "count": len(accessible_teams),
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
        project_obj = find_project_with_access(session, user, project)
        if not project_obj:
            return {"error": f"Project not found: {project}"}

        # Find team with access control
        team_obj = find_team_with_access(session, user, team)
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
            "project": project_summary(project_obj),
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
        project_obj = find_project_with_access(session, user, project)
        if not project_obj:
            return {"error": f"Project not found: {project}"}

        # Find team with access control
        team_obj = find_team_with_access(session, user, team)
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
            "project": project_summary(project_obj),
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

        project_obj = find_project_with_access(session, user, project)
        if not project_obj:
            return {"error": f"Project not found: {project}"}

        return {
            "project": project_summary(project_obj),
            "teams": [team_to_dict(t, include_members=False) for t in project_obj.teams],
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
            "projects": [project_summary(p) for p in visible_projects],
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
        project_obj = find_project_with_access(session, user, project)
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
            "project": project_summary(project_obj),
            "has_access": has_access,
            "granting_teams": granting_teams,
        }


# ============== Helper Functions ==============


def find_project_with_access(session: Session, user: User, project: int | str) -> Project | None:
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


def find_team_with_access(session: Session, user: User, team: str | int) -> Team | None:
    """Find a team by ID or slug, with access control filtering."""
    query = session.query(Team).options(selectinload(Team.members))

    # Apply access control filtering
    query = filter_teams_query(session, user, query)

    if isinstance(team, int):
        return query.filter(Team.id == team).first()

    if team.isdigit():
        return query.filter(Team.id == int(team)).first()

    return query.filter(Team.slug == team).first()
