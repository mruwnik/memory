"""
Access control helpers for MCP tools.

Provides functions to build access filters and log access from MCP tool context.
"""

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, scoped_session

from fastmcp.server.dependencies import get_access_token

from memory.api.auth import handle_api_key_use, is_expired, lookup_api_key, lookup_principal
from memory.common.access_control import (
    AccessFilter,
    SensitivityLevel,
    UserProxy,
    apply_access_filter_to_query,
    build_access_filter,
    deny_all_filter,
    get_user_project_roles,
    has_admin_scope,
    user_can_create_in_project,
)
from memory.common.db.connection import make_session
from memory.common.db.models import SourceItem, User, UserSession
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


def build_mcp_user_access_filter(
    session: "Session | scoped_session[Session]",
    user: Any,
) -> AccessFilter | None:
    """Build the access filter for a ``UserProxy`` (or ``User``) in the given
    session. ``None`` ⇒ superadmin (no filtering); no user ⇒ deny everything.
    The filter's scopes are ``user.scopes``: for token-scope semantics (API-key
    overrides) use ``get_current_user_access_filter``.
    """
    if user is None:
        return deny_all_filter()
    if has_admin_scope(user):
        return None
    return build_access_filter(user, get_user_project_roles(session, user))


def fetch_accessible_source_items(
    session: "Session | scoped_session[Session]",
    ids: Sequence[int],
    access_filter: AccessFilter | None,
    *,
    base_query: Any = None,
) -> list[SourceItem]:
    """Load SourceItems by id, gated by ``access_filter`` — the single fetch path.

    The one choke point for "fetch these items for this caller": it applies the
    caller's ``AccessFilter`` via ``apply_access_filter_to_query`` — the same
    canonical SQL gate used by search, BM25, and ``list_items`` / ``count_items``
    — so the creator / person / public / project-role rules AND the ``hidden``
    tombstone exclusion (enforced even on the admin / ``access_filter is None``
    path) are applied in one place. Fetch-by-id surfaces call this instead of
    re-deriving access rules, so they can't drift apart.

    Args:
        session: Active DB session.
        ids: SourceItem ids to load. Empty ⇒ ``[]`` (no query).
        access_filter: The caller's filter (``None`` ⇒ superadmin). Build it from
            the request token via ``get_current_user_access_filter`` (honours
            API-key scope overrides) or from a ``UserProxy`` via
            ``build_mcp_user_access_filter``.
        base_query: Optional pre-built ``session.query(SourceItem)`` carrying
            caller-specific options/filters (e.g. ``selectinload(people)`` or
            ``embed_status == "STORED"``). The id filter and access filter are
            applied on top. Defaults to a bare ``SourceItem`` query.
    """
    if not ids:
        return []
    query = base_query if base_query is not None else session.query(SourceItem)
    query = query.filter(SourceItem.id.in_(list(ids)))
    query = apply_access_filter_to_query(query, access_filter)
    return query.all()


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

    For API keys, validates expiry via is_valid() and records use via
    handle_api_key_use() to mirror the REST auth path.
    """
    user_session = session.get(UserSession, token)
    if user_session and user_session.user and not is_session_expired(user_session):
        return user_session.user

    api_key_record = lookup_api_key(token, session)
    if api_key_record is None or not api_key_record.is_valid():
        return None

    # Eagerly load user before handle_api_key_use, which commits. This
    # prevents DetachedInstanceError on lazy load.
    user = api_key_record.user
    if user is None:
        return None
    handle_api_key_use(api_key_record, session)
    return user


def get_mcp_current_user(
    session: "Session | scoped_session[Session] | None" = None,
) -> UserProxy | None:
    """The current MCP caller, or None if not authenticated.

    For REST API endpoints, use memory.api.auth.get_current_user instead.
    ``scopes`` are the access token's, not the user's: an admin's read-only API
    key or a narrowly-scoped OAuth client is not treated as admin. The auth
    layer already recorded the key's use for this request.
    """
    access_token = get_access_token()
    if access_token is None:
        return None

    def resolve(db: "Session | scoped_session[Session]") -> UserProxy | None:
        principal = lookup_principal(access_token.token, db, record_use=False)
        if principal is None:
            return None
        return UserProxy.from_user(principal.user, scopes=list(access_token.scopes or []))

    if session is not None:
        return resolve(session)
    with make_session() as db:
        return resolve(db)


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
