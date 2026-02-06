"""MCP subserver for team management."""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

from fastmcp import FastMCP
from sqlalchemy import Text, cast, or_, select
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
from sqlalchemy.orm import Session, scoped_session, selectinload

from memory.api.MCP.access import get_mcp_current_user
from memory.api.MCP.visibility import require_scopes, visible_when
from memory.common.access_control import (
    filter_projects_query,
    filter_teams_query,
)
from memory.common.scopes import SCOPE_TEAMS, SCOPE_TEAMS_WRITE
from memory.common.db.connection import make_session
from memory.common.db.models import (
    Person,
    Project,
    Team,
    User,
)
from memory.common.db.models.sources import (
    GithubRepo,
    team_members,
)
from memory.common import discord as discord_client
from memory.common.github import GithubClient
from memory.api.MCP.servers.discord import resolve_bot_id, resolve_guild_id
from memory.api.MCP.servers.github_helpers import get_github_client_for_org

logger = logging.getLogger(__name__)

teams_mcp = FastMCP("memory-teams")

_UNSET = "__UNSET__"


# ============== Async-Safe Data Capture ==============
#
# SQLAlchemy objects can become detached from their session after async/await
# operations, causing DetachedInstanceError when accessing attributes.
# These dataclasses capture the needed values upfront for safe use in async code.


@dataclass(frozen=True)
class TeamSyncInfo:
    """Team attributes needed for external service sync operations."""

    slug: str
    discord_guild_id: int | None
    discord_role_id: int | None
    auto_sync_discord: bool
    github_org: str | None
    github_team_slug: str | None
    github_team_id: int | None
    auto_sync_github: bool

    @classmethod
    def from_team(cls, team: Team) -> "TeamSyncInfo":
        return cls(
            slug=team.slug,
            discord_guild_id=team.discord_guild_id,
            discord_role_id=team.discord_role_id,
            auto_sync_discord=team.auto_sync_discord,
            github_org=team.github_org,
            github_team_slug=team.github_team_slug,
            github_team_id=team.github_team_id,
            auto_sync_github=team.auto_sync_github,
        )

    @property
    def should_sync_discord(self) -> bool:
        return bool(self.auto_sync_discord and self.discord_role_id and self.discord_guild_id)

    @property
    def should_sync_github(self) -> bool:
        return bool(self.auto_sync_github and self.github_team_id and self.github_org)


@dataclass(frozen=True)
class PersonSyncInfo:
    """Person attributes needed for external service sync operations."""

    identifier: str
    discord_accounts: tuple[tuple[int, str], ...]  # (id, username) pairs
    github_usernames: tuple[str, ...]

    @classmethod
    def from_person(cls, person: Person) -> "PersonSyncInfo":
        # Get GitHub usernames from linked accounts
        github_usernames = [acc.username for acc in person.github_accounts]

        # Also check contact_info for github username if no linked accounts
        if not github_usernames and person.contact_info:
            github_contact = person.contact_info.get("github")
            if isinstance(github_contact, str) and github_contact.strip():
                github_usernames = [github_contact.strip()]
            elif isinstance(github_contact, list):
                github_usernames = [u.strip() for u in github_contact if isinstance(u, str) and u.strip()]

        return cls(
            identifier=person.identifier,
            discord_accounts=tuple(
                (acc.id, acc.username) for acc in person.discord_accounts
            ),
            github_usernames=tuple(github_usernames),
        )


def sanitize_error(e: Exception, context: str) -> str:
    """Sanitize error for external API response.

    Logs the full error internally but returns a generic message to avoid
    leaking internal details (stack traces, file paths, connection strings).
    """
    logger.warning(f"{context}: {type(e).__name__}: {e}")
    return f"{context}: operation failed"


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


async def ensure_discord_role(
    session: Session | scoped_session[Session],
    team_id: int,
    guild: int | str | None,
    discord_role: int | str | None,
    auto_sync_discord: bool,
) -> tuple[dict[str, Any], list[str]]:
    """Ensure Discord role exists and membership matches internal team.

    1. Resolve/create the Discord role
    2. If auto_sync_discord: diff current role members vs internal team members,
       add missing, remove extras

    Returns (sync_info, warnings)
    """
    warnings: list[str] = []
    sync_info: dict[str, Any] = {}

    team = session.query(Team).filter(Team.id == team_id).first()
    if not team:
        return sync_info, [f"Team {team_id} not found"]

    if guild is None:
        team.auto_sync_discord = auto_sync_discord
        return sync_info, warnings

    try:
        resolved_guild_id = resolve_guild_id(session, guild)
    except ValueError as e:
        warnings.append(f"Discord guild resolution failed: {e}")
        team.auto_sync_discord = auto_sync_discord
        return sync_info, warnings

    team.discord_guild_id = resolved_guild_id

    resolved_role_id = None
    role_created = False

    if discord_role is not None:
        try:
            bot_id = resolve_bot_id(None, session=session)
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
    session.flush()

    # Sync membership if auto_sync is enabled and we have a role
    if not auto_sync_discord or not resolved_role_id or not resolved_guild_id:
        return sync_info, warnings

    try:
        bot_id = resolve_bot_id(None, session=session)

        # Re-query team with members + discord_accounts eagerly loaded
        team = (
            session.query(Team)
            .options(
                selectinload(Team.members).selectinload(Person.discord_accounts),
            )
            .filter(Team.id == team_id)
            .first()
        )
        if not team:
            return sync_info, warnings

        # Capture sync info before any awaits
        team_info = TeamSyncInfo.from_team(team)
        internal_discord_ids: dict[int, PersonSyncInfo] = {}
        for person in team.members:
            person_info = PersonSyncInfo.from_person(person)
            for account_id, _username in person_info.discord_accounts:
                internal_discord_ids[account_id] = person_info

        # Fetch current Discord role members
        members_data = await asyncio.to_thread(
            discord_client.list_role_members, bot_id, resolved_guild_id, resolved_role_id
        )
        external_discord_ids = {
            int(m["id"]) for m in (members_data or {}).get("members", []) if m.get("id")
        }

        # Diff: add internal members missing from Discord role
        to_add = {
            did: pinfo for did, pinfo in internal_discord_ids.items()
            if did not in external_discord_ids
        }
        # Diff: remove Discord role members not in internal team
        to_remove_ids = external_discord_ids - set(internal_discord_ids.keys())

        members_added = 0
        members_removed = 0
        errors: list[str] = []

        for person_info in set(to_add.values()):
            add_result = await _discord_add_role(team_info, person_info)
            if add_result.get("success"):
                members_added += len(add_result.get("users_added", []))
            errors.extend(add_result.get("errors", []))

        for discord_id in to_remove_ids:
            # Create a minimal PersonSyncInfo for the removal
            remove_person = PersonSyncInfo(
                identifier=f"discord:{discord_id}",
                discord_accounts=((discord_id, str(discord_id)),),
                github_usernames=(),
            )
            remove_result = await _discord_remove_role(team_info, remove_person)
            if remove_result.get("success"):
                members_removed += len(remove_result.get("users_removed", []))
            errors.extend(remove_result.get("errors", []))

        sync_info["members_added"] = members_added
        sync_info["members_removed"] = members_removed
        if errors:
            sync_info["errors"] = errors
    except Exception as e:
        warnings.append(f"Discord membership sync failed: {e}")

    return sync_info, warnings


async def ensure_github_team(
    session: Session | scoped_session[Session],
    team_id: int,
    name: str,
    github_org: str | None,
    github_team_slug: str | None,
    auto_sync_github: bool,
) -> tuple[dict[str, Any], list[str]]:
    """Ensure GitHub team exists and membership matches internal team.

    1. Fetch or create the GitHub team
    2. If auto_sync_github: diff current GitHub members vs internal team members,
       add missing, remove extras

    Returns (sync_info, warnings)
    """
    sync_info: dict[str, Any] = {}
    warnings: list[str] = []

    team = session.query(Team).filter(Team.id == team_id).first()
    if not team:
        return sync_info, [f"Team {team_id} not found"]

    team.auto_sync_github = auto_sync_github

    if github_org is None:
        return sync_info, warnings

    team.github_org = github_org

    if github_team_slug is None:
        return sync_info, warnings

    team.github_team_slug = github_team_slug

    try:
        github_team_data = await fetch_or_create_github_team(
            session, github_org, github_team_slug, name
        )
    except Exception as e:
        return sync_info, [f"GitHub team resolution failed: {e}"]

    if not github_team_data:
        return sync_info, warnings

    if github_team_data.get("created"):
        sync_info["team_created"] = True
    if github_team_data.get("id"):
        team.github_team_id = github_team_data["id"]

    session.flush()

    # Sync membership if auto_sync is enabled and we have a team
    if not auto_sync_github or not team.github_team_id or not team.github_org:
        return sync_info, warnings

    # Resolve GitHub client
    user = get_mcp_current_user(session=session, full=True)
    if not user or user.id is None:
        warnings.append("GitHub membership sync skipped: no authenticated user")
        return sync_info, warnings

    github_client = get_github_client_for_org(session, github_org, user.id)
    if not github_client:
        warnings.append("GitHub membership sync skipped: no GitHub client for org")
        return sync_info, warnings

    try:
        # Re-query team with members + github_accounts eagerly loaded
        team = (
            session.query(Team)
            .options(
                selectinload(Team.members).selectinload(Person.github_accounts),
            )
            .filter(Team.id == team_id)
            .first()
        )
        if not team:
            return sync_info, warnings

        # Capture sync info before any awaits
        team_info = TeamSyncInfo.from_team(team)
        internal_github_usernames: dict[str, PersonSyncInfo] = {}
        for person in team.members:
            person_info = PersonSyncInfo.from_person(person)
            for username in person_info.github_usernames:
                internal_github_usernames[username] = person_info

        # Fetch current GitHub team members
        github_members = await asyncio.to_thread(
            github_client.get_team_members, github_org, github_team_slug
        )
        external_github_usernames = {
            m["login"] for m in (github_members or []) if m.get("login")
        }

        # Diff: add internal members missing from GitHub team
        to_add = {
            uname: pinfo for uname, pinfo in internal_github_usernames.items()
            if uname not in external_github_usernames
        }
        # Diff: remove GitHub team members not in internal team
        to_remove_usernames = external_github_usernames - set(internal_github_usernames.keys())

        members_added = 0
        members_removed = 0
        errors: list[str] = []

        for person_info in set(to_add.values()):
            add_result = await _github_add_member(github_client, team_info, person_info)
            if add_result.get("success"):
                members_added += len(add_result.get("users_added", []))
            errors.extend(add_result.get("errors", []))

        for username in to_remove_usernames:
            remove_person = PersonSyncInfo(
                identifier=f"github:{username}",
                discord_accounts=(),
                github_usernames=(username,),
            )
            remove_result = await _github_remove_member(github_client, team_info, remove_person)
            if remove_result.get("success"):
                members_removed += len(remove_result.get("users_removed", []))
            errors.extend(remove_result.get("errors", []))

        sync_info["members_added"] = members_added
        sync_info["members_removed"] = members_removed
        if errors:
            sync_info["errors"] = errors
    except Exception as e:
        warnings.append(f"GitHub membership sync failed: {e}")

    return sync_info, warnings


@teams_mcp.tool()
@visible_when(require_scopes(SCOPE_TEAMS_WRITE))
async def upsert(
    name: str,
    slug: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    # Ownership
    owner: str | int | None = _UNSET,
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
    # Status
    is_active: bool | None = None,
) -> dict:
    """
    Create or update a team with optional Discord/GitHub integration.

    Args:
        name: Team display name
        slug: URL-safe identifier (auto-generated from name if not provided)
        description: Optional description of the team's purpose
        tags: Tags for categorization (e.g., ["engineering", "core"])
        owner: Person responsible for this team - can be person identifier or ID.
               Set to null to clear the owner.
        guild: Discord guild - can be numeric ID or server name
        discord_role: Discord role - can be numeric ID or role name (creates if doesn't exist)
        auto_sync_discord: Whether to auto-sync membership to Discord (default: true)
        github_org: GitHub organization for team sync
        github_team_slug: GitHub team slug - creates team if doesn't exist
        auto_sync_github: Whether to auto-sync membership to GitHub (default: true)
        members: If provided, set team to exactly these members.
                 Pass [] to remove all members. Pass None to leave unchanged.
        is_active: Active status (set to false to archive team)

    Behavior:
        - If team with slug exists: updates it
        - If discord_role name doesn't exist: creates new role
        - If github_team_slug doesn't exist: creates team using `name`
        - If auto_sync_discord/auto_sync_github: syncs membership bidirectionally
          (adds internal members to external service, removes external-only members)
        - Members are set first, then external services are synced to match

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
        # Resolve owner: _UNSET = skip, None = clear, str/int = resolve person
        owner_id: int | None = 0  # 0 = skip
        if owner is None:
            owner_id = None  # Explicitly clear
        elif owner is not _UNSET:
            if isinstance(owner, int) or (isinstance(owner, str) and owner.isdigit()):
                owner_person = session.query(Person).filter(Person.id == int(owner)).first()
            else:
                owner_person = session.query(Person).filter(Person.identifier == owner).first()
            if owner_person:
                owner_id = owner_person.id
            else:
                result["warnings"].append(f"Owner not found: {owner}")

        team, action = upsert_team_record(session, slug, name, description, tags, is_active, owner_id)
        result["action"] = action

        # Capture team.id immediately - before any queries that might trigger autoflush
        # and expire the object (DetachedInstanceError with scoped_session)
        team_id = team.id

        # Step 2: Set explicit members if provided
        if members is not None:
            team = (
                session.query(Team)
                .options(selectinload(Team.members))
                .filter(Team.id == team_id)
                .first()
            )
            if team:
                membership_result = await set_team_members(session, team, members)
                result["membership_changes"] = membership_result

        session.flush()

        # Step 3: Ensure Discord role + sync membership
        if guild is not None or discord_role is not None:
            discord_sync, discord_warnings = await ensure_discord_role(
                session, team_id, guild, discord_role, auto_sync_discord
            )
            result["discord_sync"].update(discord_sync)
            result["warnings"].extend(discord_warnings)

        # Step 4: Ensure GitHub team + sync membership
        if github_org is not None:
            github_sync, github_warnings = await ensure_github_team(
                session, team_id, name, github_org, github_team_slug, auto_sync_github
            )
            result["github_sync"].update(github_sync)
            result["warnings"].extend(github_warnings)

        session.flush()
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


async def fetch_or_create_github_team(
    session: Session | scoped_session[Session],
    org: str,
    team_slug: str,
    team_name: str,
) -> dict[str, Any] | None:
    """Fetch or create a GitHub team.

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


def find_or_create_person(
    session: Session | scoped_session[Session],
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
    session: Session | scoped_session[Session],
    team: Team,
    member_identifiers: list[str],
) -> dict[str, Any]:
    """Set team to exactly these members, syncing to external services.

    Returns dict with added/removed counts and any warnings.
    """
    current_member_list = list(team.members)
    current_members = {p.identifier for p in current_member_list}
    current_member_ids = {p.id for p in current_member_list}
    target_members = set(member_identifiers)

    to_add = target_members - current_members
    to_remove = current_members - target_members

    result: dict[str, Any] = {
        "added": [],
        "removed": [],
        "created_people": [],
        "warnings": [],
    }

    # Capture sync info upfront (before any DB changes that might affect relationships)
    team_info = TeamSyncInfo.from_team(team)
    pending_syncs: list[tuple[PersonSyncInfo, bool]] = []  # (person_info, is_add)

    # Resolve GitHub client upfront if needed for sync.
    # IMPORTANT: Must resolve before any DB operations that might trigger commits,
    # as get_github_client_for_org() opens its own session. Nested sessions cause
    # DetachedInstanceError when the inner session closes and invalidates objects
    # that the outer session expects to use.
    github_client: GithubClient | None = None
    if team_info.should_sync_github:
        user = get_mcp_current_user(session, full=True)
        if user and user.id and team.github_org:
            github_client = get_github_client_for_org(session, team.github_org, user.id)

    # Remove members no longer in list
    for identifier in to_remove:
        person = session.query(Person).filter(Person.identifier == identifier).first()
        if person and person.id in current_member_ids:
            pending_syncs.append((PersonSyncInfo.from_person(person), False))
            team.members.remove(person)
            result["removed"].append(identifier)

    # Add new members
    for identifier in to_add:
        person = (
            session.query(Person).filter(Person.identifier == identifier).first()
            or session.query(Person).filter(Person.display_name.ilike(identifier)).first()
        )

        if not person:
            person = find_or_create_person(session, identifier, identifier)
            result["created_people"].append(identifier)

        if person.id not in current_member_ids:
            team.members.append(person)
            current_member_ids.add(person.id)
            pending_syncs.append((PersonSyncInfo.from_person(person), True))
            result["added"].append(identifier)

    session.flush()

    # Run external syncs after DB changes are flushed
    for person_info, is_add in pending_syncs:
        await _run_external_sync(
            team_info, person_info, add=is_add, github_client=github_client
        )

    # Warning for clearing all members
    if not target_members and current_members:
        result["warnings"].append(f"Removed all {len(to_remove)} members from team")

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
        user = get_mcp_current_user(session, full=True)
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
        user = get_mcp_current_user(session, full=True)
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
async def team_add_member(
    team: str | int,
    person: str | int,
    role: str = "member",
) -> dict:
    """
    Add a person to a team.

    If the team has Discord/GitHub integration enabled (auto_sync_discord/auto_sync_github),
    this will also add the person to the corresponding Discord role and/or GitHub team.

    Args:
        team: Team slug or ID
        person: Person identifier or ID
        role: Team role - "member", "lead", or "admin" (default: "member")

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

        # Sync to external services based on team's auto_sync settings
        # Resolve GitHub client if needed (avoids nested sessions in sync functions)
        github_client: GithubClient | None = None
        if team_obj.github_org and team_obj.auto_sync_github:
            github_client = get_github_client_for_org(
                session, team_obj.github_org, user.id
            )
        result["sync"] = await sync_membership_add(
            team_obj, person_obj, github_client=github_client
        )

        return result


@teams_mcp.tool()
@visible_when(require_scopes(SCOPE_TEAMS_WRITE))
async def team_remove_member(
    team: str | int,
    person: str | int,
) -> dict:
    """
    Remove a person from a team.

    If the team has Discord/GitHub integration enabled (auto_sync_discord/auto_sync_github),
    this will also remove the person from the corresponding Discord role and/or GitHub team.

    Args:
        team: Team slug or ID
        person: Person identifier or ID

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

        # Sync to external services based on team's auto_sync settings
        # Resolve GitHub client if needed (avoids nested sessions in sync functions)
        github_client: GithubClient | None = None
        if team_obj.github_org and team_obj.auto_sync_github:
            github_client = get_github_client_for_org(
                session, team_obj.github_org, user.id
            )
        result["sync"] = await sync_membership_remove(
            team_obj, person_obj, github_client=github_client
        )

        return result


# ============== External Service Sync ==============


async def _run_external_sync(
    team: TeamSyncInfo,
    person: PersonSyncInfo,
    add: bool,
    github_client: GithubClient | None = None,
) -> dict[str, Any]:
    """Run Discord and GitHub sync for a membership change.

    Args:
        team: Team sync info
        person: Person sync info
        add: True to add member, False to remove
        github_client: Pre-resolved GitHub client (required if team has GitHub sync enabled)
    """
    result: dict[str, Any] = {"discord": None, "github": None}

    if team.should_sync_discord:
        result["discord"] = await (
            _discord_add_role(team, person) if add else _discord_remove_role(team, person)
        )

    if team.should_sync_github:
        if not github_client:
            result["github"] = {
                "success": False,
                "errors": ["GitHub sync skipped: no authenticated client (user not logged in or missing org access)"],
            }
        elif add:
            result["github"] = await _github_add_member(github_client, team, person)
        else:
            result["github"] = await _github_remove_member(github_client, team, person)

    return result


async def sync_membership_add(
    team: Team,
    person: Person,
    github_client: GithubClient | None = None,
) -> dict[str, Any]:
    """Sync membership addition to Discord and GitHub.

    Args:
        team: Team ORM object
        person: Person ORM object
        github_client: Pre-resolved GitHub client (required if team has GitHub sync)
    """
    return await _run_external_sync(
        TeamSyncInfo.from_team(team),
        PersonSyncInfo.from_person(person),
        add=True,
        github_client=github_client,
    )


async def sync_membership_remove(
    team: Team,
    person: Person,
    github_client: GithubClient | None = None,
) -> dict[str, Any]:
    """Sync membership removal to Discord and GitHub.

    Args:
        team: Team ORM object
        person: Person ORM object
        github_client: Pre-resolved GitHub client (required if team has GitHub sync)
    """
    return await _run_external_sync(
        TeamSyncInfo.from_team(team),
        PersonSyncInfo.from_person(person),
        add=False,
        github_client=github_client,
    )


async def _discord_add_role(team: TeamSyncInfo, person: PersonSyncInfo) -> dict[str, Any]:
    """Add Discord role to person's Discord accounts."""
    if not team.discord_guild_id or not team.discord_role_id:
        return {"success": False, "users_added": [], "errors": ["Discord sync not configured"]}

    bot_id = resolve_bot_id(None)
    successes: list[str] = []
    errors: list[str] = []

    for account_id, username in person.discord_accounts:
        try:
            result = await asyncio.to_thread(
                discord_client.add_role_member,
                bot_id,
                team.discord_guild_id,
                team.discord_role_id,
                account_id,
            )
            if result is None or "error" in result:
                errors.append(f"{username}: {result.get('error') if result else 'Failed'}")
            else:
                successes.append(username)
        except Exception as e:
            errors.append(sanitize_error(e, f"Discord add role for {username}"))

    return {"success": not errors, "users_added": successes, "errors": errors}


async def _discord_remove_role(team: TeamSyncInfo, person: PersonSyncInfo) -> dict[str, Any]:
    """Remove Discord role from person's Discord accounts."""
    if not team.discord_guild_id or not team.discord_role_id:
        return {"success": False, "users_removed": [], "errors": ["Discord sync not configured"]}

    bot_id = resolve_bot_id(None)
    successes: list[str] = []
    errors: list[str] = []

    for account_id, username in person.discord_accounts:
        try:
            result = await asyncio.to_thread(
                discord_client.remove_role_member,
                bot_id,
                team.discord_guild_id,
                team.discord_role_id,
                account_id,
            )
            if result is None or "error" in result:
                errors.append(f"{username}: {result.get('error') if result else 'Failed'}")
            else:
                successes.append(username)
        except Exception as e:
            errors.append(sanitize_error(e, f"Discord remove role for {username}"))

    return {"success": not errors, "users_removed": successes, "errors": errors}


async def _github_add_member(
    client: GithubClient,
    team: TeamSyncInfo,
    person: PersonSyncInfo,
) -> dict[str, Any]:
    """Add person's GitHub accounts to GitHub team.

    Args:
        client: Pre-resolved GitHub client with org access
        team: Team sync info with github_org and github_team_slug
        person: Person sync info with github_usernames
    """
    if not team.github_org or not team.github_team_slug:
        return {
            "success": False,
            "users_added": [],
            "errors": [f"Team {team.slug} missing github_org or github_team_slug"],
        }

    successes: list[str] = []
    errors: list[str] = []

    for username in person.github_usernames:
        try:
            result = await asyncio.to_thread(
                client.add_team_member,
                team.github_org,
                team.github_team_slug,
                username,
            )
            if result.get("error"):
                errors.append(f"{username}: {result['error']}")
            elif result.get("success"):
                successes.append(username)
            else:
                errors.append(f"{username}: Unknown result")
        except Exception as e:
            errors.append(sanitize_error(e, f"GitHub add {username}"))

    return {"success": not errors, "users_added": successes, "errors": errors}


async def _github_remove_member(
    client: GithubClient,
    team: TeamSyncInfo,
    person: PersonSyncInfo,
) -> dict[str, Any]:
    """Remove person's GitHub accounts from GitHub team.

    Args:
        client: Pre-resolved GitHub client with org access
        team: Team sync info with github_org and github_team_slug
        person: Person sync info with github_usernames
    """
    if not team.github_org or not team.github_team_slug:
        return {
            "success": False,
            "users_removed": [],
            "errors": [f"Team {team.slug} missing github_org or github_team_slug"],
        }

    successes: list[str] = []
    errors: list[str] = []

    for username in person.github_usernames:
        try:
            # remove_team_member returns bool
            success = await asyncio.to_thread(
                client.remove_team_member,
                team.github_org,
                team.github_team_slug,
                username,
            )
            if success:
                successes.append(username)
            else:
                errors.append(f"{username}: Failed to remove")
        except Exception as e:
            errors.append(sanitize_error(e, f"GitHub remove {username}"))

    return {"success": not errors, "users_removed": successes, "errors": errors}


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


# ============== Helper Functions ==============


def find_project_with_access(session: Session | scoped_session[Session], user: User, project: int | str) -> Project | None:
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
