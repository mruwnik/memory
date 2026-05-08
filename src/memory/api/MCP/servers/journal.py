"""MCP subserver for journal entries on various entities."""

import logging
from typing import Any, Literal

from fastmcp import FastMCP
from sqlalchemy import or_

from memory.api.MCP.access import (
    UserProxy,
    get_mcp_current_user,
    get_project_roles_by_user_id,
)
from memory.api.MCP.visibility import require_scopes, visible_when
from memory.common.access_control import (
    has_admin_scope,
    user_can_access,
    user_can_access_project,
    user_can_access_team,
)
from memory.common.db.models.users import User
from memory.common.scopes import SCOPE_READ, SCOPE_WRITE
from memory.common.db.connection import make_session
from memory.common.db.models import JournalEntry, SourceItem
from memory.common.db.models.polls import AvailabilityPoll
from memory.common.db.models.sources import Project, Team

logger = logging.getLogger(__name__)

journal_mcp = FastMCP("memory-journal")

TargetType = Literal["source_item", "project", "team", "poll"]

# Map target types to their model classes for validation
TARGET_MODELS: dict[str, type] = {
    "source_item": SourceItem,
    "project": Project,
    "team": Team,
    "poll": AvailabilityPoll,
}


def can_access_journal_target(
    session,
    user: UserProxy | User,
    target_type: str,
    target: Any,
    project_roles: dict[int, str] | None,
) -> bool:
    """Per-target-type access gate for journal add/list_all.

    Pre-fix the journal MCP tools only checked access for ``source_item``
    targets and let project / team / poll targets through unchecked, so any
    user with SCOPE_READ could read every non-private entry on any project
    or team in the instance, and any SCOPE_WRITE caller could plant entries
    on those targets. This function centralises the per-type gates so add
    and list_all share one source of truth.

    Returns True for admins (they bypass everywhere).

    ``user`` may be a :class:`UserProxy` (just id + scopes) or a full
    :class:`User`. Membership-based gates (project, team) need the User's
    ``person`` relationship; if a UserProxy is passed in, we hydrate it
    from ``session`` for those branches.
    """
    if has_admin_scope(user):
        return True

    if target_type == "source_item":
        return user_can_access(user, target, project_roles)

    if target_type == "project":
        # Use project_roles when available — it was fetched via
        # ``get_project_roles_by_user_id`` which queries the same User →
        # Person → team_members → project_teams chain that
        # ``user_can_access_project`` would re-execute. Avoids a second
        # round-trip and works without ``user.person`` being available
        # (UserProxy lacks the ORM relationship).
        if project_roles is not None:
            return target.id in project_roles
        full_user = session.get(User, user.id)
        if full_user is None:
            return False
        return user_can_access_project(session, full_user, target.id)

    if target_type == "team":
        # Gate on team membership — needs ``user.person`` so we hydrate to
        # the full User if a UserProxy was passed in.
        full_user = user if isinstance(user, User) else session.get(User, user.id)
        if full_user is None:
            return False
        return user_can_access_team(session, full_user, target.id)

    if target_type == "poll":
        # Polls have no project_id (per the model), so default to the most
        # conservative rule: only the poll's creator may journal it. If a
        # broader access model is ever defined for polls (e.g. anyone with
        # the slug can read the poll, but only the creator can journal it),
        # update this branch — but never silently widen by leaving it open.
        target_user_id = getattr(target, "user_id", None)
        return target_user_id is not None and target_user_id == user.id

    # Unknown target type — fail closed. Validation in
    # ``get_target_and_project_id`` should have caught this earlier.
    return False


def get_target_and_project_id(
    session, target_type: str, target_id: int
) -> tuple[Any, int | None]:
    """
    Fetch target entity and determine project_id for access control.

    Returns:
        (target_entity, project_id) tuple
    """
    model = TARGET_MODELS.get(target_type)
    if model is None:
        raise ValueError(f"Invalid target_type: {target_type}")

    target = session.get(model, target_id)
    if target is None:
        raise ValueError(f"{target_type} {target_id} not found")

    # Determine project_id for access control
    if target_type == "project":
        # For projects, the project IS the target
        project_id = target.id
    elif target_type == "team":
        # Teams don't have project_id, journal entries are team-scoped
        project_id = None
    elif target_type == "poll":
        # Polls may have project_id
        project_id = getattr(target, "project_id", None)
    else:
        # SourceItems have project_id
        project_id = getattr(target, "project_id", None)

    return target, project_id


@journal_mcp.tool()
@visible_when(require_scopes(SCOPE_WRITE))
async def add(
    target_id: int,
    content: str,
    target_type: TargetType = "source_item",
    private: bool = False,
) -> dict[str, Any]:
    """
    Add a journal entry to an entity.

    Journal entries are append-only notes that accumulate over time.
    Use them to track thoughts, progress, or updates about any item.

    Args:
        target_id: ID of the entity to attach the entry to
        content: The journal entry text
        target_type: Type of entity ('source_item', 'project', 'team', 'poll')
        private: If True, only you can see this entry (default: False)

    Returns:
        The created journal entry with status.
    """
    user = get_mcp_current_user()
    if user is None or user.id is None:
        raise ValueError("Authentication required")

    # Fetch project_roles BEFORE opening session to avoid nested session issues
    project_roles: dict[int, str] | None = None
    if not has_admin_scope(user):
        project_roles = get_project_roles_by_user_id(user.id)

    with make_session() as session:
        # Verify target exists and get project_id for access control
        target, project_id = get_target_and_project_id(session, target_type, target_id)

        # Per-type access gate. Pre-fix only source_item was gated, so a
        # SCOPE_WRITE caller could plant entries on any project/team/poll
        # they had no membership in.
        if not can_access_journal_target(
            session, user, target_type, target, project_roles
        ):
            # "not found or access denied" wording matches the source_item
            # branch and avoids leaking the target's existence.
            raise ValueError(f"{target_type} {target_id} not found or access denied")

        # Create journal entry
        entry = JournalEntry(
            target_type=target_type,
            target_id=target_id,
            creator_id=user.id,
            project_id=project_id,
            content=content,
            private=private,
        )
        session.add(entry)
        session.commit()
        session.refresh(entry)

        return {
            "status": "created",
            "entry": entry.as_payload(),
        }


@journal_mcp.tool()
@visible_when(require_scopes(SCOPE_READ))
async def list_all(
    target_id: int,
    target_type: TargetType = "source_item",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """
    List journal entries for an entity.

    Returns entries in chronological order (oldest first).
    Private entries from other users are automatically filtered out.

    Args:
        target_id: ID of the entity
        target_type: Type of entity ('source_item', 'project', 'team', 'poll')
        limit: Maximum entries to return (default: 50)
        offset: Number of entries to skip (default: 0)

    Returns:
        List of journal entries with total count.
    """
    user = get_mcp_current_user()
    if user is None or user.id is None:
        raise ValueError("Authentication required")

    # Fetch project_roles BEFORE opening session to avoid nested session issues
    project_roles: dict[int, str] | None = None
    if not has_admin_scope(user):
        project_roles = get_project_roles_by_user_id(user.id)

    with make_session() as session:
        # Verify target exists
        target, _ = get_target_and_project_id(session, target_type, target_id)

        # Per-type access gate. Pre-fix only source_item was gated, so a
        # SCOPE_READ caller could read every non-private entry on any
        # project/team/poll across the instance.
        if not can_access_journal_target(
            session, user, target_type, target, project_roles
        ):
            raise ValueError(f"{target_type} {target_id} not found or access denied")

        # Build query with target type and id filter
        user_id = user.id
        query = session.query(JournalEntry).filter(
            JournalEntry.target_type == target_type,
            JournalEntry.target_id == target_id,
        )

        # Filter private entries unless admin
        if not has_admin_scope(user):
            query = query.filter(
                or_(
                    JournalEntry.private == False,  # noqa: E712
                    JournalEntry.creator_id == user_id,
                )
            )

        total = query.count()
        entries = (
            query.order_by(JournalEntry.created_at.asc()).offset(offset).limit(limit).all()
        )

        return {
            "entries": [e.as_payload() for e in entries],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
