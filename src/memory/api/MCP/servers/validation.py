"""Validation helpers for project MCP tools.

Pure validation/parse functions extracted from `projects.py` so they can be
reused by sibling helper modules without circular imports.
"""

from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from memory.common.access_control import (
    get_user_team_ids,
    has_admin_scope,
    user_can_access_project,
)
from memory.common.db.models import Project, Team
from memory.common.db.models.sources import Person


ALLOWED_DOC_URL_SCHEMES = {"http", "https"}


def validate_doc_url(url: str) -> str | None:
    """Validate a doc_url has a safe scheme. Returns error message or None."""
    try:
        parsed = urlparse(url)
    except Exception:
        return f"Invalid doc_url: {url!r}"
    if parsed.scheme not in ALLOWED_DOC_URL_SCHEMES:
        return f"doc_url must use http or https scheme, got: {parsed.scheme!r}"
    if not parsed.netloc:
        return f"doc_url must include a host, got: {url!r}"
    return None


def validate_teams_for_project(
    session: Any,
    user: Any,
    team_ids: list[int] | None,
    require_non_empty: bool = True,
) -> tuple[list[Team] | None, dict | None]:
    """Validate team_ids for project operations.

    Args:
        session: Database session
        user: Current user
        team_ids: List of team IDs to validate
        require_non_empty: If True, empty team_ids returns an error

    Returns:
        Tuple of (teams list or None, error dict or None).
        If error dict is returned, teams will be None.
    """
    if require_non_empty and not team_ids:
        return None, {
            "error": "team_ids must be a non-empty list for new projects",
            "project": None,
        }

    if not team_ids:
        return [], None

    # Validate all specified teams exist
    teams = session.query(Team).filter(Team.id.in_(team_ids)).all()
    found_ids = {t.id for t in teams}
    missing_ids = set(team_ids) - found_ids

    if missing_ids:
        return None, {
            "error": f"Invalid team_ids: teams {missing_ids} do not exist",
            "project": None,
        }

    # Non-admins must be a member of at least one specified team
    if not has_admin_scope(user):
        user_teams = get_user_team_ids(session, user)
        accessible_team_ids = set(team_ids) & user_teams
        if not accessible_team_ids:
            return None, {
                "error": "You do not have access to any of the specified teams",
                "project": None,
            }

    return teams, None


def validate_parent_project(
    session: Any,
    user: Any,
    parent_id: int | None,
) -> dict | None:
    """Validate that a parent project exists and user has access.

    Args:
        session: Database session
        user: Current user
        parent_id: Parent project ID to validate (None is valid)

    Returns:
        Error dict if validation fails, None if valid
    """
    if parent_id is None:
        return None

    parent = session.get(Project, parent_id)
    if not parent:
        return {"error": f"Parent project not found: {parent_id}", "project": None}

    if not has_admin_scope(user) and not user_can_access_project(
        session, user, parent_id
    ):
        return {"error": f"Parent project not found: {parent_id}", "project": None}

    return None


def validate_owner(
    session: Any, owner_id: int | None
) -> tuple[Person | None, dict | None]:
    """Validate that an owner exists.

    Returns:
        Tuple of (Person or None, error dict or None)
    """
    if owner_id is None:
        return None, None
    owner = session.get(Person, owner_id)
    if not owner:
        return None, {"error": f"Owner not found: {owner_id}", "project": None}
    return owner, None


def parse_due_on(due_on_str: str | None) -> tuple[datetime | None, dict | None]:
    """Parse a due_on ISO string to datetime.

    Returns:
        Tuple of (datetime or None, error dict or None)
    """
    if due_on_str is None:
        return None, None
    try:
        return datetime.fromisoformat(due_on_str.replace("Z", "+00:00")), None
    except ValueError:
        return None, {"error": "Invalid due_on format. Use ISO 8601.", "project": None}
