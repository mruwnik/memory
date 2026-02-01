"""
Access control helpers for MCP tools.

Provides functions to build access filters and log access from MCP tool context.
"""

import logging
from typing import Protocol

from fastmcp.server.dependencies import get_access_token

from memory.api.auth import lookup_api_key
from memory.common.access_control import (
    AccessFilter,
    build_access_filter,
    get_user_project_roles,
    has_admin_scope,
)
from memory.common.db.connection import make_session
from memory.common.db.models import User, UserSession
from memory.common.db.models.access import log_access

logger = logging.getLogger(__name__)


class UserLike(Protocol):
    """Protocol for user-like objects that can be used for access control."""

    id: int | None
    scopes: list[str]


def get_project_roles_by_user_id(user_id: int) -> dict[int, str]:
    """
    Fetch project roles for a user by their ID.

    This queries the database to find the user, their linked Person,
    and that person's project collaborations.

    Args:
        user_id: The user's database ID

    Returns:
        Dict mapping project_id to role string
    """
    with make_session() as db:
        user = db.query(User).filter(User.id == user_id).first()
        if user is None:
            logger.warning("get_project_roles_by_user_id: user %d not found", user_id)
            return {}
        return get_user_project_roles(db, user)


def build_user_access_filter(user: "User") -> AccessFilter | None:
    """
    Build an access filter for a user based on their project collaborations.

    Args:
        user: User object (must have id and scopes attributes)

    Returns:
        AccessFilter for search queries, or None if user is superadmin
    """
    with make_session() as db:
        project_roles = get_user_project_roles(db, user)
    return build_access_filter(user, project_roles)


class UserProxy:
    """Minimal user proxy for access control when only dict is available."""

    def __init__(self, user_dict: dict):
        self.id = user_dict.get("id")
        self.scopes = user_dict.get("scopes", [])


def get_mcp_current_user() -> UserProxy | None:
    """Get the current MCP user as a UserProxy for access control.

    This is for MCP tool context (uses fastmcp's get_access_token).
    For REST API endpoints, use memory.api.auth.get_current_user instead.

    Returns a UserProxy with id and scopes, or None if not authenticated.
    Callers can access user.id for just the ID, or user.scopes for scopes.
    """
    access_token = get_access_token()
    if access_token is None:
        return None

    with make_session() as session:
        user_session = session.get(UserSession, access_token.token)
        if user_session and user_session.user:
            user = user_session.user
            return UserProxy({"id": user.id, "scopes": list(user.scopes or [])})

        # Try as API key
        api_key_record = lookup_api_key(access_token.token, session)
        if api_key_record and api_key_record.user:
            user = api_key_record.user
            return UserProxy({"id": user.id, "scopes": list(user.scopes or [])})

    return None


def build_user_access_filter_from_dict(user_dict: dict) -> AccessFilter | None:
    """
    Build an access filter from a user info dictionary.

    This is useful when working with serialized user info from get_current_user().

    Args:
        user_dict: Dictionary with user info (must have "id", optionally "scopes")

    Returns:
        AccessFilter for search queries, or None if user is superadmin
    """
    user_id = user_dict.get("id")
    if user_id is None:
        logger.warning("build_user_access_filter_from_dict: no user ID in dict")
        return AccessFilter(conditions=[])

    user_proxy = UserProxy(user_dict)

    # Check for superadmin
    if has_admin_scope(user_proxy):  # type: ignore[arg-type]
        return None

    # Fetch project roles directly by user_id
    # This queries User -> Person -> project_collaborators
    project_roles = get_project_roles_by_user_id(user_id)
    return build_access_filter(user_proxy, project_roles)  # type: ignore[arg-type]


def log_search_access(
    user_id: int,
    query: str,
    result_count: int,
) -> None:
    """
    Log a search access event for audit purposes.

    Args:
        user_id: The user who performed the search
        query: The search query
        result_count: Number of results returned
    """
    with make_session() as db:
        log_access(
            db,
            user_id=user_id,
            action="search",
            query=query,
            result_count=result_count,
        )
        db.commit()


def log_item_access(
    user_id: int,
    item_id: int,
) -> None:
    """
    Log an item view access event for audit purposes.

    Args:
        user_id: The user who viewed the item
        item_id: The SourceItem ID that was viewed
    """
    with make_session() as db:
        log_access(
            db,
            user_id=user_id,
            action="view_item",
            item_id=item_id,
        )
        db.commit()
