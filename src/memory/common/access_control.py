"""
Access control logic for project-based RBAC.

This module provides role-based access control for content in the knowledge base.
Users are linked to Persons who are members of Teams, which are assigned to Projects.
Content has sensitivity levels (basic, internal, confidential).

Key design decisions:
- Projects with Teams assigned (team_projects junction)
- User -> Person -> team_members -> Team -> project_teams -> Project
- NULL project_id = superadmin only (prevents accidental exposure)
- Superadmins (users with admin scope) bypass filters but access is still logged
- Defense in depth: filter at Qdrant, BM25, AND final merge
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, FrozenSet, Literal, Protocol, runtime_checkable

from sqlalchemy import literal, select
from sqlalchemy.orm import Query

from memory.common.db.models.sources import Project, Team, project_teams, team_members
from memory.common.scopes import SCOPE_ADMIN

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from sqlalchemy.orm.scoping import scoped_session

    from memory.common.db.models.users import User


# Protocols for duck-typed access control checks
# These allow tests to use simple mock objects without importing full models
# Using Any return types to accommodate SQLAlchemy's Mapped[T] types


@runtime_checkable
class UserLike(Protocol):
    """Protocol for objects that can be checked for access control."""

    @property
    def id(self) -> Any: ...

    @property
    def scopes(self) -> Any: ...


@runtime_checkable
class SourceItemLike(Protocol):
    """Protocol for objects that can be checked for item access."""

    @property
    def project_id(self) -> Any: ...

    @property
    def sensitivity(self) -> Any: ...

logger = logging.getLogger(__name__)

# Type alias for database columns and TypedDict fields
SensitivityLevelLiteral = Literal["public", "basic", "internal", "confidential"]


class SensitivityLevel(str, Enum):
    """Content sensitivity levels, from least to most restricted."""

    PUBLIC = "public"  # Visible to all authenticated users (books, blogs, forums)
    BASIC = "basic"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"


class ProjectRole(str, Enum):
    """User roles within a project, with increasing access levels."""

    CONTRIBUTOR = "contributor"
    MANAGER = "manager"
    ADMIN = "admin"


# Maps roles to the sensitivity levels they can access
ROLE_SENSITIVITY: dict[ProjectRole, FrozenSet[SensitivityLevel]] = {
    ProjectRole.CONTRIBUTOR: frozenset({SensitivityLevel.PUBLIC, SensitivityLevel.BASIC}),
    ProjectRole.MANAGER: frozenset(
        {SensitivityLevel.PUBLIC, SensitivityLevel.BASIC, SensitivityLevel.INTERNAL}
    ),
    ProjectRole.ADMIN: frozenset(
        {
            SensitivityLevel.PUBLIC,
            SensitivityLevel.BASIC,
            SensitivityLevel.INTERNAL,
            SensitivityLevel.CONFIDENTIAL,
        }
    ),
}


def get_allowed_sensitivities(role_str: str) -> frozenset[str] | None:
    """
    Get allowed sensitivity level strings for a role.

    Args:
        role_str: Role string (contributor, manager, admin)

    Returns:
        Frozenset of allowed sensitivity strings, or None if invalid role
    """
    try:
        role = ProjectRole(role_str)
        return frozenset(s.value for s in ROLE_SENSITIVITY[role])
    except (ValueError, KeyError):
        logger.warning("Invalid role encountered: %r", role_str)
        return None


@dataclass(frozen=True)
class AccessCondition:
    """A single access condition: project + allowed sensitivities."""

    project_id: int
    sensitivities: frozenset[str]


@dataclass
class AccessFilter:
    """Filter for search queries based on user's project collaborations."""

    conditions: list[AccessCondition]
    person_id: int | None = None  # For person override filtering
    creator_id: int | None = None  # For creator-based access (user sees their own items)
    include_public: bool = True  # Whether to include public items

    def is_empty(self) -> bool:
        """Check if filter has no conditions (user has no project access)."""
        return len(self.conditions) == 0


def has_admin_scope(user: UserLike) -> bool:
    """Check if user has admin scope (superadmin access)."""
    scopes = getattr(user, "scopes", None)
    if scopes is None:
        scopes = []
    return SCOPE_ADMIN in scopes


def get_user_project_roles(db: "Session | scoped_session[Session]", user: "User") -> dict[int, str]:
    """
    Get the user's roles in each project they have access to via team membership.

    Returns a dict mapping project_id to role string.
    Team-based access: user is in a team -> team is assigned to project.
    The role returned is based on team membership role (member -> contributor, lead -> manager, admin -> admin).
    """
    # User must have a linked Person to have project access
    person = getattr(user, "person", None)
    if person is None:
        return {}

    # Query teams the person belongs to and their assigned projects
    # Join: team_members -> project_teams to find accessible projects
    stmt = (
        select(team_members.c.role, project_teams.c.project_id)
        .select_from(
            team_members.join(
                project_teams, team_members.c.team_id == project_teams.c.team_id
            )
        )
        .where(team_members.c.person_id == person.id)
    )
    rows = db.execute(stmt).fetchall()

    # Map team roles to project roles (best role wins if in multiple teams)
    # member -> contributor, lead -> manager, admin -> admin
    role_mapping = {"member": "contributor", "lead": "manager", "admin": "admin"}
    role_priority = {"contributor": 1, "manager": 2, "admin": 3}

    project_roles: dict[int, str] = {}
    for row in rows:
        project_id = row.project_id
        team_role = row.role
        project_role = role_mapping.get(team_role, "contributor")

        # Keep the highest-privilege role if multiple teams grant access
        current = project_roles.get(project_id)
        if current is None or role_priority.get(project_role, 0) > role_priority.get(current, 0):
            project_roles[project_id] = project_role

    return project_roles


def normalize_sensitivity(sensitivity: SensitivityLevel | str) -> str:
    """Normalize sensitivity to string value."""
    if isinstance(sensitivity, SensitivityLevel):
        return sensitivity.value
    return str(sensitivity)


def user_can_access(
    user: UserLike,
    item: SourceItemLike,
    project_roles: dict[int, str] | None = None,
) -> bool:
    """
    Determine if user can access a content item.

    Access is granted if ANY of these conditions is true:
    1. User has admin scope (superadmin)
    2. User is the creator of the item (creator_id matches user.id)
    3. User's Person is attached to the item (person override)
    4. Item has public sensitivity
    5. Item has a project_id and user has appropriate role in that project

    Args:
        user: The user attempting access
        item: The content item being accessed
        project_roles: Optional pre-fetched project roles (from get_user_project_roles)

    Returns:
        True if user can access the item, False otherwise
    """
    # Superadmins see everything
    if has_admin_scope(user):
        return True

    # Creator always sees their own items
    creator_id = getattr(item, "creator_id", None)
    if creator_id is not None and creator_id == user.id:
        return True

    # Person override: if user's person is attached to item, grant full access
    # This allows people to see content they're associated with (emails, meetings, etc.)
    person = getattr(user, "person", None)
    if person is not None:
        item_people = getattr(item, "people", None) or []
        if any(p.id == person.id for p in item_people):
            return True

    # Public sensitivity bypasses project membership check
    item_sensitivity = normalize_sensitivity(item.sensitivity or "basic")
    if item_sensitivity == "public":
        return True

    # Unclassified content (no project) is NOT visible to regular users
    # (creator already checked above, so this is for non-creator access)
    # This prevents accidental exposure during migration or classification failures
    if item.project_id is None:
        return False

    # If project_roles not provided, we can't check access
    # Caller should pre-fetch with get_user_project_roles()
    if project_roles is None:
        logger.debug("user_can_access called with project_roles=None for user %s", getattr(user, "id", "?"))
        return False

    # Check if user has access to this project
    role_str = project_roles.get(item.project_id)
    if role_str is None:
        return False

    allowed = get_allowed_sensitivities(role_str)
    if allowed is None:
        return False

    return item_sensitivity in allowed


def user_can_create_in_project(
    user: UserLike,
    project_id: int,
    sensitivity: SensitivityLevel | str,
    project_roles: dict[int, str] | None = None,
) -> bool:
    """
    Check if user can create content at given sensitivity in project.

    Args:
        user: The user attempting to create content
        project_id: The target project (milestone) ID
        sensitivity: The sensitivity level for the new content
        project_roles: Optional pre-fetched project roles

    Returns:
        True if user can create content at this sensitivity level
    """
    if has_admin_scope(user):
        return True

    if project_roles is None:
        return False

    role_str = project_roles.get(project_id)
    if role_str is None:
        return False

    allowed = get_allowed_sensitivities(role_str)
    if allowed is None:
        return False

    sensitivity_str = normalize_sensitivity(sensitivity)
    return sensitivity_str in allowed


def build_access_filter(
    user: UserLike,
    project_roles: dict[int, str] | None = None,
) -> AccessFilter | None:
    """
    Build filter for search queries based on user's access.

    Args:
        user: The user performing the search
        project_roles: Pre-fetched project roles from get_user_project_roles()

    Returns:
        AccessFilter with conditions, or None for superadmins (no filtering)
    """
    # Superadmins see everything - no filter needed
    if has_admin_scope(user):
        return None

    # Get person ID for person override filtering
    person = getattr(user, "person", None)
    person_id = person.id if person else None

    # Get user ID for creator-based filtering
    user_id = getattr(user, "id", None)

    if project_roles is None:
        # No roles provided - still allow public items, person override, and creator access
        return AccessFilter(
            conditions=[],
            person_id=person_id,
            creator_id=user_id,
            include_public=True,
        )

    conditions = []

    # Per-project access based on role
    # NOTE: No "global content" condition - NULL project_id is superadmin-only
    # (except for creator access, which is handled separately in search)
    for project_id, role_str in project_roles.items():
        allowed = get_allowed_sensitivities(role_str)
        if allowed is not None:
            conditions.append(
                AccessCondition(
                    project_id=project_id,
                    sensitivities=allowed,
                )
            )

    return AccessFilter(
        conditions=conditions,
        person_id=person_id,
        creator_id=user_id,
        include_public=True,
    )


def get_allowed_project_ids(project_roles: dict[int, str]) -> set[int]:
    """Get the set of project IDs the user has any access to."""
    return set(project_roles.keys())


def user_can_edit(user: UserLike, item: SourceItemLike) -> bool:
    """
    Check if user can edit a content item.

    Only the creator or an admin can edit content.

    Args:
        user: The user attempting to edit
        item: The content item to edit

    Returns:
        True if user can edit the item, False otherwise
    """
    if has_admin_scope(user):
        return True

    creator_id = getattr(item, "creator_id", None)
    return creator_id is not None and creator_id == user.id


def user_can_delete(user: UserLike, item: SourceItemLike) -> bool:
    """
    Check if user can delete a content item.

    Same permissions as editing - only creator or admin.

    Args:
        user: The user attempting to delete
        item: The content item to delete

    Returns:
        True if user can delete the item, False otherwise
    """
    return user_can_edit(user, item)


def get_max_sensitivity_for_project(
    project_roles: dict[int, str],
    project_id: int,
) -> SensitivityLevel | None:
    """Get the maximum sensitivity level a user can access in a project."""
    role_str = project_roles.get(project_id)
    if role_str is None:
        return None

    allowed = get_allowed_sensitivities(role_str)
    if allowed is None:
        return None

    # Return highest sensitivity in the set
    # Order: confidential > internal > basic > public
    if "confidential" in allowed:
        return SensitivityLevel.CONFIDENTIAL
    elif "internal" in allowed:
        return SensitivityLevel.INTERNAL
    elif "basic" in allowed:
        return SensitivityLevel.BASIC
    elif "public" in allowed:
        return SensitivityLevel.PUBLIC

    return None


# ==============================================================================
# Team and Project Visibility
# ==============================================================================


def get_user_team_ids(db: "Session | scoped_session[Session]", user: "User") -> set[int]:
    """Get the IDs of all teams the user belongs to via their Person record.

    Returns an empty set if the user has no associated person or no team memberships.
    """
    person = getattr(user, "person", None)
    if person is None:
        return set()

    result = db.execute(
        select(team_members.c.team_id).where(team_members.c.person_id == person.id)
    ).fetchall()
    return {row[0] for row in result}


def get_accessible_project_ids(
    db: "Session | scoped_session[Session]", user: "User"
) -> set[int] | None:
    """Get the IDs of all projects the user can access.

    A user can access a project if they are a member of ANY team
    assigned to that project.

    Returns:
        None for admins (meaning no filtering - they see all projects)
        set[int] for regular users (the specific project IDs they can access)
    """
    if has_admin_scope(user):
        return None  # None means no filtering (admin sees all)

    team_ids = get_user_team_ids(db, user)
    if not team_ids:
        return set()

    result = db.execute(
        select(project_teams.c.project_id).where(project_teams.c.team_id.in_(team_ids))
    ).fetchall()
    return {row[0] for row in result}


def get_accessible_team_ids(
    db: "Session | scoped_session[Session]", user: "User"
) -> set[int] | None:
    """Get the IDs of all teams the user can see.

    A user can see a team if they are a member of that team.

    Returns:
        None for admins (meaning no filtering - they see all teams)
        set[int] for regular users (the specific team IDs they can access)
    """
    if has_admin_scope(user):
        return None  # None means no filtering (admin sees all)

    return get_user_team_ids(db, user)


def user_can_access_project(
    db: "Session | scoped_session[Session]", user: "User", project_id: int
) -> bool:
    """Check if a user can access a specific project.

    Admins can access all projects. For regular users, checks team membership
    directly without loading all accessible project IDs.
    """
    if has_admin_scope(user):
        return True

    team_ids = get_user_team_ids(db, user)
    if not team_ids:
        return False

    # Direct query for this specific project - more efficient than loading all IDs
    result = db.execute(
        select(project_teams.c.project_id)
        .where(project_teams.c.project_id == project_id)
        .where(project_teams.c.team_id.in_(team_ids))
        .limit(1)
    ).first()
    return result is not None


def user_can_access_team(
    db: "Session | scoped_session[Session]", user: "User", team_id: int
) -> bool:
    """Check if a user can access a specific team.

    Admins can access all teams. For regular users, checks team membership directly.
    """
    if has_admin_scope(user):
        return True
    # For regular users, just check if they're a member of this team
    team_ids = get_user_team_ids(db, user)
    return team_id in team_ids


def filter_projects_query(
    db: "Session | scoped_session[Session]", user: "User", query: Query[Project]
) -> Query[Project]:
    """Filter a project query to only include projects the user can access.

    Admins see all projects. Regular users only see projects they have
    team membership access to.

    Usage:
        query = db.query(Project)
        query = filter_projects_query(db, user, query)

    Returns the filtered query.
    """
    accessible_ids = get_accessible_project_ids(db, user)
    if accessible_ids is None:
        return query  # Admins see all

    if not accessible_ids:
        # Return query that matches nothing
        return query.filter(literal(False))
    return query.filter(Project.id.in_(accessible_ids))


def filter_teams_query(
    db: "Session | scoped_session[Session]", user: "User", query: Query[Team]
) -> Query[Team]:
    """Filter a team query to only include teams the user can access.

    Admins see all teams. Regular users only see teams they are members of.

    Usage:
        query = db.query(Team)
        query = filter_teams_query(db, user, query)

    Returns the filtered query.
    """
    accessible_ids = get_accessible_team_ids(db, user)
    if accessible_ids is None:
        return query  # Admins see all

    if not accessible_ids:
        # Return query that matches nothing
        return query.filter(literal(False))
    return query.filter(Team.id.in_(accessible_ids))
