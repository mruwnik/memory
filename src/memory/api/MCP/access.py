"""
Access control helpers for MCP tools.

Provides functions to build access filters and log access from MCP tool context.
"""

import logging
from typing import TYPE_CHECKING, Any, Literal, Protocol, overload

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, scoped_session

from fastmcp.server.dependencies import get_access_token

from memory.api.auth import handle_api_key_use, is_expired, lookup_api_key
from memory.common.access_control import (
    AccessFilter,
    SensitivityLevel,
    build_access_filter,
    get_user_project_roles,
    has_admin_scope,
    user_can_create_in_project,
)
from memory.common.db.connection import make_session
from memory.common.db.models import User, UserSession
from memory.common.db.models.access import log_access

logger = logging.getLogger(__name__)


# The full set of sensitivity strings the access-control matrix understands.
# Anything outside this set must be rejected at the write boundary —
# previously a typo or attacker-supplied string was stored verbatim,
# producing an "invisible" record that no read-side filter matched.
ALLOWED_SENSITIVITIES: frozenset[str] = frozenset(s.value for s in SensitivityLevel)


class UserLike(Protocol):
    """Protocol for user-like objects that can be used for access control."""

    id: int | None
    scopes: list[str]


def get_project_roles_by_user_id(
    user_id: int | None,
    session: "Session | scoped_session[Session] | None" = None,
) -> dict[int, str]:
    """
    Fetch project roles for a user by their ID.

    This queries the database to find the user, their linked Person,
    and that person's project collaborations.

    Args:
        user_id: The user's database ID. None (an unauthenticated /
            id-less user) yields no roles.
        session: Optional existing session to use (avoids nested session issues)

    Returns:
        Dict mapping project_id to role string
    """
    # No user id -> no roles. Skip the pointless `WHERE id IS NULL` query
    # (never matches a PK) and the misleading "user None not found" warning.
    if user_id is None:
        return {}

    if session is not None:
        user = session.query(User).filter(User.id == user_id).first()
        if user is None:
            logger.warning("get_project_roles_by_user_id: user %s not found", user_id)
            return {}
        return get_user_project_roles(session, user)

    with make_session() as db:
        user = db.query(User).filter(User.id == user_id).first()
        if user is None:
            logger.warning("get_project_roles_by_user_id: user %s not found", user_id)
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
    """Minimal user proxy for access control when only dict is available.

    Normalizes ``scopes`` to ``list[str]`` at the boundary so callers can rely
    on the shape regardless of what the source dict contained (None, tuple,
    missing key, etc.).
    """

    def __init__(self, user_dict: dict):
        self.id: int | None = user_dict.get("id")
        raw_scopes = user_dict.get("scopes") or []
        self.scopes: list[str] = [str(s) for s in raw_scopes]


def is_session_expired(user_session: UserSession) -> bool:
    """Return True if the session's expires_at is in the past.

    Thin wrapper around :func:`memory.api.auth.is_expired` — the
    naive/aware normalization logic was previously duplicated here, with
    even this docstring citing auth.py as the reference implementation.
    Two security-critical session-expiry checks side-by-side are a
    drift hazard, so we delegate to the canonical helper.

    A session with no expires_at is treated as expired (fail-closed) by
    is_expired().
    """
    return is_expired(user_session.expires_at)


def fetch_user_by_token(
    session: "Session | scoped_session[Session]", token: str
) -> User | None:
    """Look up a user by token (session token or API key).

    For API keys, validates expiry via is_valid() and handles one-time key
    consumption via handle_api_key_use() to mirror the REST auth path.
    """
    user_session = session.get(UserSession, token)
    if user_session and user_session.user and not is_session_expired(user_session):
        return user_session.user

    api_key_record = lookup_api_key(token, session)
    if api_key_record is None or not api_key_record.is_valid():
        return None

    # Eagerly load user before handle_api_key_use, which may delete the key
    # (for one-time keys). This prevents DetachedInstanceError on lazy load.
    user = api_key_record.user
    if user is None:
        return None
    if not handle_api_key_use(api_key_record, session):
        # Concurrent request consumed the one-time key first. Debug-level
        # to stay out of normal logs unless operators chase a race.
        logger.debug(
            "One-time API key %s consumed by concurrent request; this request loses race",
            api_key_record.id,
        )
        return None
    return user


@overload
def get_mcp_current_user() -> UserProxy | None:
    ...


@overload
def get_mcp_current_user(
    session: "Session | scoped_session[Session]", full: Literal[True]
) -> User | None:
    ...


@overload
def get_mcp_current_user(
    session: "Session | scoped_session[Session]", full: Literal[False] = False
) -> UserProxy | None:
    ...


def get_mcp_current_user(
    session: "Session | scoped_session[Session] | None" = None,
    full: bool = False,
) -> UserProxy | User | None:
    """Get the current MCP user for access control.

    This is for MCP tool context (uses fastmcp's get_access_token).
    For REST API endpoints, use memory.api.auth.get_current_user instead.

    Args:
        session: SQLAlchemy session. Required when full=True.
        full: If True, return full User ORM object with relationships intact
              (including user.person for team membership checks).
              If False (default), return a lightweight UserProxy.

    Returns:
        If full=False: UserProxy with id and scopes, or None if not authenticated.
        If full=True: Full User object, or None if not authenticated.
    """
    access_token = get_access_token()
    if access_token is None:
        return None

    if full:
        if session is None:
            raise ValueError("session is required when full=True")
        return fetch_user_by_token(session, access_token.token)

    # Lightweight path - use provided session or create our own
    if session is not None:
        user = fetch_user_by_token(session, access_token.token)
        if user:
            return UserProxy({"id": user.id, "scopes": list(user.scopes or [])})
        return None

    with make_session() as db:
        user = fetch_user_by_token(db, access_token.token)
        if user:
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
    # This queries User -> Person -> team_members -> project_teams
    project_roles = get_project_roles_by_user_id(user_id)
    return build_access_filter(user_proxy, project_roles)  # type: ignore[arg-type]


def require_project_membership(user: Any, project_id: int) -> None:
    """Enforce that the caller may write to ``project_id``.

    Admins can assign any project; regular users must be a member.  Raises
    ``PermissionError`` otherwise.

    Note: this checks *membership only*, not role. The sensitivity-vs-role
    matrix is enforced separately by :func:`require_can_write_at_sensitivity`,
    so write paths that accept both ``project_id`` AND ``sensitivity`` from
    user input should call both. Read-side enforcement alone is not enough:
    a contributor could plant a `confidential` item that downstream high-role
    readers would treat as in-band content.
    """
    if user and has_admin_scope(user):
        return
    user_id = getattr(user, "id", None) if user else None
    if user_id is None:
        raise PermissionError("Cannot verify project membership without user ID")
    if project_id not in get_project_roles_by_user_id(user_id):
        raise PermissionError(f"You are not a member of project {project_id}")


def require_can_write_at_sensitivity(
    user: Any, project_id: int, sensitivity: str
) -> None:
    """Enforce the sensitivity-vs-role matrix on writes.

    A contributor on the project can write `public`/`basic`; a manager can
    additionally write `internal`; an admin can write `confidential`. The
    matrix is :data:`memory.common.access_control.ROLE_SENSITIVITY`. Raises
    ``ValueError`` for an unrecognised sensitivity (rejected up front so
    typo'd values don't get stored verbatim and produce content the
    read-side filters can't match), and ``PermissionError`` when the user
    holds the project but at too low a role.

    Pairs with :func:`require_project_membership`: callers that accept
    ``project_id`` AND ``sensitivity`` from user input should call both.
    """
    if sensitivity not in ALLOWED_SENSITIVITIES:
        raise ValueError(
            f"Invalid sensitivity {sensitivity!r}; must be one of "
            f"{sorted(ALLOWED_SENSITIVITIES)}."
        )
    if user and has_admin_scope(user):
        return
    user_id = getattr(user, "id", None) if user else None
    if user_id is None:
        raise PermissionError("Cannot verify project role without user ID")
    project_roles = get_project_roles_by_user_id(user_id)
    if not user_can_create_in_project(user, project_id, sensitivity, project_roles):
        raise PermissionError(
            f"Your role on project {project_id} does not permit creating "
            f"{sensitivity!r} content."
        )


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
