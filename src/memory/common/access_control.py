"""
Access control logic for project-based RBAC.

This module provides role-based access control for content in the knowledge base.
Users are linked to Persons who are collaborators on projects (GitHub milestones),
and content has sensitivity levels (basic, internal, confidential).

Key design decisions:
- Projects are GitHub milestones with collaborators (Person entries)
- User -> Person -> project_collaborators -> GithubMilestone
- NULL project_id = superadmin only (prevents accidental exposure)
- Superadmins (users with admin scope) bypass filters but access is still logged
- Defense in depth: filter at Qdrant, BM25, AND final merge
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, FrozenSet, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from sqlalchemy.orm.scoping import scoped_session

    from memory.common.db.models.source_item import SourceItem
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
    include_public: bool = True  # Whether to include public items

    def is_empty(self) -> bool:
        """Check if filter has no conditions (user has no project access)."""
        return len(self.conditions) == 0


def has_admin_scope(user: UserLike) -> bool:
    """Check if user has admin scope (superadmin access)."""
    scopes = getattr(user, "scopes", None)
    if scopes is None:
        scopes = []
    return "*" in scopes or "admin" in scopes


def get_user_project_roles(db: "Session | scoped_session[Session]", user: "User") -> dict[int, str]:
    """
    Get the user's roles in each project they collaborate on.

    Returns a dict mapping project_id (milestone ID) to role string.
    """
    from memory.common.db.models.sources import project_collaborators

    # User must have a linked Person to have project access
    person = getattr(user, "person", None)
    if person is None:
        return {}

    # Query project_collaborators for this person
    rows = db.execute(
        project_collaborators.select().where(
            project_collaborators.c.person_id == person.id
        )
    ).fetchall()

    return {row.project_id: row.role for row in rows}


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

    if project_roles is None:
        # No roles provided - still allow public items and person override
        return AccessFilter(conditions=[], person_id=person_id, include_public=True)

    conditions = []

    # Per-project access based on role
    # NOTE: No "global content" condition - NULL project_id is superadmin-only
    for project_id, role_str in project_roles.items():
        allowed = get_allowed_sensitivities(role_str)
        if allowed is not None:
            conditions.append(
                AccessCondition(
                    project_id=project_id,
                    sensitivities=allowed,
                )
            )

    return AccessFilter(conditions=conditions, person_id=person_id, include_public=True)


def get_allowed_project_ids(project_roles: dict[int, str]) -> set[int]:
    """Get the set of project IDs the user has any access to."""
    return set(project_roles.keys())


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
