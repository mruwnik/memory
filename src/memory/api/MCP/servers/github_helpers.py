"""Helper functions for GitHub MCP tools.

This module contains database queries, serialization, and GitHub API operations
used by the MCP tool definitions in github.py.
"""

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import Text, desc, func
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import ARRAY

from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import (
    GithubItem,
    GithubMilestone,
    GithubProject,
    GithubTeam,
)
from memory.common.db.models.sources import GithubAccount, GithubRepo
from memory.common.celery_app import app as celery_app, SYNC_GITHUB_ITEM
from memory.common.github import GithubClient, GithubCredentials, serialize_issue_data

logger = logging.getLogger(__name__)


# =============================================================================
# Serialization Helpers
# =============================================================================


def build_github_url(repo_path: str, number: int | None, kind: str) -> str:
    """Build GitHub URL from repo path and issue/PR number."""
    if number is None:
        return f"https://github.com/{repo_path}"
    url_type = "pull" if kind == "pr" else "issues"
    return f"https://github.com/{repo_path}/{url_type}/{number}"


def extract_deadline(item: GithubItem) -> str | None:
    """Extract deadline from issue's project fields or milestone.

    Priority:
    1. Explicit "Due Date" project field (stored as "EquiStamp.Due Date")
    2. Milestone due_on date as fallback
    """
    if item.project_fields:
        deadline = item.project_fields.get("EquiStamp.Due Date")
        if deadline:
            return deadline

    if item.milestone_rel and item.milestone_rel.due_on:
        return item.milestone_rel.due_on.date().isoformat()

    return None


def serialize_issue(item: GithubItem, include_content: bool = False) -> dict[str, Any]:
    """Serialize a GithubItem to a dict for API response."""
    result = {
        "id": item.id,
        "number": item.number,
        "kind": item.kind,
        "repo_path": item.repo_path,
        "title": item.title,
        "state": item.state,
        "author": item.author,
        "assignees": item.assignees or [],
        "labels": item.labels or [],
        "milestone": item.milestone_rel.title if item.milestone_rel else None,
        "milestone_id": item.milestone_id,
        "project_status": item.project_status,
        "project_priority": item.project_priority,
        "project_fields": item.project_fields,
        "deadline": extract_deadline(item),
        "comment_count": item.comment_count,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "closed_at": item.closed_at.isoformat() if item.closed_at else None,
        "merged_at": item.merged_at.isoformat() if item.merged_at else None,
        "github_updated_at": (
            item.github_updated_at.isoformat() if item.github_updated_at else None
        ),
        "url": build_github_url(item.repo_path, item.number, item.kind),
    }
    if include_content:
        result["content"] = item.content
        if item.kind == "pr" and item.pr_data:
            result["pr_data"] = {
                "additions": item.pr_data.additions,
                "deletions": item.pr_data.deletions,
                "changed_files_count": item.pr_data.changed_files_count,
                "files": item.pr_data.files,
                "reviews": item.pr_data.reviews,
                "review_comments": item.pr_data.review_comments,
                "diff": item.pr_data.diff,
            }
    return result


# =============================================================================
# Database Query Functions
# =============================================================================


def list_issues(
    repo: str | None = None,
    assignee: str | None = None,
    author: str | None = None,
    state: str | None = None,
    kind: str | None = None,
    labels: list[str] | None = None,
    milestone: str | int | None = None,
    project_status: str | None = None,
    project_field: dict[str, str] | None = None,
    updated_since: str | None = None,
    updated_before: str | None = None,
    limit: int = 50,
    order_by: str = "updated",
) -> list[dict]:
    """List GitHub issues and PRs with flexible filtering."""
    limit = min(limit, 200)

    with make_session() as session:
        query = session.query(GithubItem)

        if repo:
            query = query.filter(GithubItem.repo_path == repo)
        if assignee:
            query = query.filter(GithubItem.assignees.contains([assignee]))
        if author:
            query = query.filter(GithubItem.author == author)
        if state:
            query = query.filter(GithubItem.state == state)
        if kind:
            query = query.filter(GithubItem.kind == kind)
        else:
            query = query.filter(GithubItem.kind.in_(["issue", "pr"]))
        if labels:
            query = query.filter(
                GithubItem.labels.op("&&")(sql_cast(labels, ARRAY(Text)))
            )
        if milestone is not None:
            if isinstance(milestone, int):
                query = query.filter(GithubItem.milestone_id == milestone)
            else:
                query = query.join(GithubMilestone).filter(
                    GithubMilestone.title == milestone
                )
        if project_status:
            query = query.filter(GithubItem.project_status == project_status)
        if project_field:
            for key, value in project_field.items():
                query = query.filter(GithubItem.project_fields[key].astext == value)
        if updated_since:
            since_dt = datetime.fromisoformat(updated_since.replace("Z", "+00:00"))
            query = query.filter(GithubItem.github_updated_at >= since_dt)
        if updated_before:
            before_dt = datetime.fromisoformat(updated_before.replace("Z", "+00:00"))
            query = query.filter(GithubItem.github_updated_at <= before_dt)

        if order_by == "created":
            query = query.order_by(desc(GithubItem.created_at))
        elif order_by == "number":
            query = query.order_by(desc(GithubItem.number))
        else:
            query = query.order_by(desc(GithubItem.github_updated_at))

        query = query.limit(limit)
        items = query.all()

        return [serialize_issue(item) for item in items]


def list_milestones(
    repo: str | None = None,
    state: str | None = None,
    has_due_date: bool | None = None,
    limit: int = 50,
) -> list[dict]:
    """List GitHub milestones with filtering options."""
    limit = min(limit, 200)

    with make_session() as session:
        query = session.query(GithubMilestone).join(GithubRepo)

        if repo:
            parts = repo.split("/")
            if len(parts) == 2:
                owner, name = parts
                query = query.filter(
                    GithubRepo.owner == owner,
                    GithubRepo.name == name,
                )

        if state:
            query = query.filter(GithubMilestone.state == state)

        if has_due_date is True:
            query = query.filter(GithubMilestone.due_on.isnot(None))
        elif has_due_date is False:
            query = query.filter(GithubMilestone.due_on.is_(None))

        query = query.order_by(
            desc(GithubMilestone.due_on.isnot(None)),
            GithubMilestone.due_on,
            desc(GithubMilestone.github_updated_at),
        ).limit(limit)

        milestones = query.all()

        results = []
        for ms in milestones:
            open_count = (
                session.query(func.count(GithubItem.id))
                .filter(
                    GithubItem.milestone_id == ms.id,
                    GithubItem.state == "open",
                )
                .scalar()
                or 0
            )
            closed_count = (
                session.query(func.count(GithubItem.id))
                .filter(
                    GithubItem.milestone_id == ms.id,
                    GithubItem.state.in_(["closed", "merged"]),
                )
                .scalar()
                or 0
            )
            total = open_count + closed_count
            progress = round(closed_count / total * 100, 1) if total > 0 else 0

            results.append(
                {
                    "id": ms.id,
                    "repo_path": f"{ms.repo.owner}/{ms.repo.name}",
                    "number": ms.number,
                    "title": ms.title,
                    "description": ms.description,
                    "state": ms.state,
                    "due_on": ms.due_on.isoformat() if ms.due_on else None,
                    "open_issues": open_count,
                    "closed_issues": closed_count,
                    "progress_percent": progress,
                    "url": f"https://github.com/{ms.repo.owner}/{ms.repo.name}/milestone/{ms.number}",
                }
            )

        return results


def list_projects(
    owner: str | None = None,
    include_closed: bool = False,
    limit: int = 50,
) -> list[dict]:
    """List GitHub Projects (v2) that have been synced to the database."""
    limit = min(limit, 200)

    with make_session() as session:
        query = session.query(GithubProject)

        if owner:
            query = query.filter(GithubProject.owner_login.ilike(owner))
        if not include_closed:
            query = query.filter(GithubProject.closed == False)  # noqa: E712

        query = query.order_by(GithubProject.title).limit(limit)
        projects = query.all()

        return [project.as_payload() for project in projects]


def list_teams(
    org: str | None = None,
    user_id: int | None = None,
    limit: int = 50,
) -> list[dict]:
    """List GitHub teams by querying GitHub API directly.

    Args:
        org: Organization login name (required)
        user_id: ID of the authenticated user (required for API access)
        limit: Maximum teams to return
    """
    limit = min(limit, 200)

    if not org:
        raise ValueError("org is required for listing teams")
    if not user_id:
        raise ValueError("user_id is required for listing teams")

    with make_session() as session:
        client = get_github_client_for_org(session, org, user_id)
        if not client:
            raise ValueError(f"No GitHub account configured with access to {org}")

        teams = []
        for team_data in client.list_teams(org):
            teams.append(
                {
                    "node_id": team_data["node_id"],
                    "github_id": team_data["github_id"],
                    "slug": team_data["slug"],
                    "name": team_data["name"],
                    "description": team_data["description"],
                    "privacy": team_data["privacy"],
                    "permission": team_data["permission"],
                    "org_login": team_data["org_login"],
                    "parent_team_slug": team_data["parent_team_slug"],
                    "members_count": team_data["members_count"],
                    "repos_count": team_data["repos_count"],
                }
            )
            if len(teams) >= limit:
                break

        return teams


def extract_status_priority(
    project_fields: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Extract status and priority from project fields dict.

    Returns (status, priority) tuple. Values are None if not found.
    Only extracts string/numeric values, ignoring None and complex types.
    """
    status: str | None = None
    priority: str | None = None
    for key, value in project_fields.items():
        # Skip None and complex types
        if value is None or not isinstance(value, (str, int, float)):
            continue
        key_lower = key.lower()
        if "status" in key_lower and status is None:
            status = str(value)
        elif "priority" in key_lower and priority is None:
            priority = str(value)
    return status, priority


def fetch_issue(repo: str, number: int) -> dict[str, Any]:
    """Get full details of a specific GitHub issue or PR.

    Fetches project fields from GitHub if not cached in the database.
    Caches the result (including empty dict for issues not in projects)
    to avoid repeated API calls.
    """
    with make_session() as session:
        item = (
            session.query(GithubItem)
            .filter(
                GithubItem.repo_path == repo,
                GithubItem.number == number,
                GithubItem.kind.in_(["issue", "pr"]),
            )
            .first()
        )

        if not item:
            raise ValueError(f"Issue #{number} not found in {repo}")

        result = serialize_issue(item, include_content=True)

        # Fetch project fields from GitHub if not cached locally
        # None means "never checked", {} means "checked but not in any project"
        if result.get("project_fields") is None:
            project_fields = fetch_project_fields_for_item(
                session, repo, number, item.kind
            )
            # Cache the result (even if empty) to avoid repeated API calls
            if project_fields is not None:
                result["project_fields"] = project_fields
                project_status, project_priority = extract_status_priority(
                    project_fields
                )

                if project_status:
                    result["project_status"] = project_status
                if project_priority:
                    result["project_priority"] = project_priority

                # Cache in database for future fetches
                item.project_fields = project_fields  # type: ignore
                item.project_status = project_status  # type: ignore
                item.project_priority = project_priority  # type: ignore
                session.commit()

        return result


def fetch_project_fields_for_item(
    session: DBSession,
    repo_path: str,
    number: int,
    kind: str,
) -> dict[str, Any] | None:
    """Fetch project fields from GitHub API for an issue or PR.

    Args:
        session: Database session
        repo_path: Repository path in "owner/repo" format
        number: Issue or PR number
        kind: "issue" or "pr"

    Returns:
        Project fields dict (may be empty if not in any project),
        or None if fetch failed (no credentials, API error, etc.)
    """
    parts = repo_path.split("/")
    if len(parts) != 2:
        return None

    owner, repo_name = parts

    # Try to get GitHub client from the repo's account
    repo_obj = (
        session.query(GithubRepo)
        .filter(GithubRepo.owner == owner, GithubRepo.name == repo_name)
        .first()
    )

    if not repo_obj or not repo_obj.account or not repo_obj.account.active:
        return None

    try:
        account = repo_obj.account
        credentials = GithubCredentials(
            auth_type=account.auth_type,
            access_token=account.access_token,
            app_id=account.app_id,
            installation_id=account.installation_id,
            private_key=account.private_key,
        )
        client = GithubClient(credentials)

        # Fetch project fields based on item type
        if kind == "pr":
            project_fields = client.fetch_pr_project_fields(owner, repo_name, number)
        else:
            project_fields = client.fetch_project_fields(owner, repo_name, number)

        # Return empty dict if API returned None (issue not in any project)
        # This distinguishes "checked but empty" from "never checked"
        return project_fields if project_fields is not None else {}
    except Exception as e:
        logger.warning(f"Failed to fetch project fields for {repo_path}#{number}: {e}")
        return None


def fetch_milestone(repo: str, number: int) -> dict:
    """Get full details of a specific milestone including its issues."""
    with make_session() as session:
        parts = repo.split("/")
        if len(parts) != 2:
            raise ValueError(f"Invalid repo format: {repo}. Expected 'owner/name'")
        owner, name = parts

        ms = (
            session.query(GithubMilestone)
            .join(GithubRepo)
            .filter(
                GithubRepo.owner == owner,
                GithubRepo.name == name,
                GithubMilestone.number == number,
            )
            .first()
        )

        if not ms:
            raise ValueError(f"Milestone #{number} not found in {repo}")

        # Count issues
        open_count = (
            session.query(func.count(GithubItem.id))
            .filter(GithubItem.milestone_id == ms.id, GithubItem.state == "open")
            .scalar()
            or 0
        )
        closed_count = (
            session.query(func.count(GithubItem.id))
            .filter(
                GithubItem.milestone_id == ms.id,
                GithubItem.state.in_(["closed", "merged"]),
            )
            .scalar()
            or 0
        )
        total = open_count + closed_count
        progress = round(closed_count / total * 100, 1) if total > 0 else 0

        # Get issues in milestone
        issues = (
            session.query(GithubItem)
            .filter(GithubItem.milestone_id == ms.id)
            .order_by(desc(GithubItem.github_updated_at))
            .all()
        )

        return {
            "id": ms.id,
            "repo_path": f"{ms.repo.owner}/{ms.repo.name}",
            "number": ms.number,
            "title": ms.title,
            "description": ms.description,
            "state": ms.state,
            "due_on": ms.due_on.isoformat() if ms.due_on else None,
            "open_issues": open_count,
            "closed_issues": closed_count,
            "progress_percent": progress,
            "url": f"https://github.com/{ms.repo.owner}/{ms.repo.name}/milestone/{ms.number}",
            "issues": [serialize_issue(item) for item in issues],
        }


def fetch_project(owner: str, project_number: int) -> dict:
    """Get full details of a specific GitHub Project."""
    with make_session() as session:
        project = (
            session.query(GithubProject)
            .filter(
                GithubProject.owner_login.ilike(owner),
                GithubProject.number == project_number,
            )
            .first()
        )

        if not project:
            raise ValueError(f"Project #{project_number} not found for {owner}")

        return project.as_payload()


def fetch_team(org: str, team_slug: str, user_id: int | None = None) -> dict:
    """Get full details of a specific team including members.

    Queries GitHub API directly - no database sync required.

    Args:
        org: Organization login name
        team_slug: Team slug (URL-safe name)
        user_id: ID of the authenticated user (required for API access)
    """
    if not user_id:
        raise ValueError("user_id is required for fetching teams")

    with make_session() as session:
        client = get_github_client_for_org(session, org, user_id)
        if not client:
            raise ValueError(f"No GitHub account configured with access to {org}")

        # Fetch team details from GitHub
        team_data = client.fetch_team(org, team_slug)
        if not team_data:
            raise ValueError(f"Team '{team_slug}' not found in {org}")

        result = {
            "node_id": team_data["node_id"],
            "github_id": team_data["github_id"],
            "slug": team_data["slug"],
            "name": team_data["name"],
            "description": team_data["description"],
            "privacy": team_data["privacy"],
            "permission": team_data["permission"],
            "org_login": team_data["org_login"],
            "parent_team_slug": team_data["parent_team_slug"],
            "members_count": team_data["members_count"],
            "repos_count": team_data["repos_count"],
        }

        # Fetch members
        members = client.get_team_members(org, team_slug)
        result["members"] = [{"login": m["login"], "role": m["role"]} for m in members]

        return result


# =============================================================================
# Client Factory Functions
# =============================================================================


def get_github_client(
    session: Any, repo_path: str, user_id: int
) -> tuple[GithubClient, GithubRepo | None]:
    """Get authenticated GithubClient for a repository path.

    Args:
        session: Database session
        repo_path: Repository path in "owner/repo" format
        user_id: ID of the authenticated user

    Returns:
        Tuple of (GithubClient, GithubRepo or None). GithubRepo is None when
        the repository is not explicitly tracked but the user has a GitHub account.

    Raises:
        ValueError: If credentials unavailable for user
    """
    parts = repo_path.split("/")
    if len(parts) != 2:
        raise ValueError(f"Invalid repo path format: {repo_path}")

    owner, name = parts

    # Try to find the repo in the user's accounts first
    repo = (
        session.query(GithubRepo)
        .join(GithubAccount)
        .filter(
            GithubRepo.owner == owner,
            GithubRepo.name == name,
            GithubAccount.user_id == user_id,
        )
        .first()
    )

    # If repo not tracked by user, find any active account for the user
    if not repo:
        # Get user's first active account
        account = (
            session.query(GithubAccount)
            .filter(
                GithubAccount.user_id == user_id,
                GithubAccount.active == True,  # noqa: E712
            )
            .first()
        )
        if not account:
            raise ValueError(
                "No GitHub account configured. Please add a GitHub account first."
            )

        credentials = GithubCredentials(
            auth_type=account.auth_type,
            access_token=account.access_token,
            app_id=account.app_id,
            installation_id=account.installation_id,
            private_key=account.private_key,
        )
        # Return None for repo since we're using a generic account
        # The caller will need to handle this case
        return GithubClient(credentials), None

    account = repo.account
    if not account or not account.active:
        raise ValueError(f"No active credentials for {repo_path}")

    credentials = GithubCredentials(
        auth_type=account.auth_type,
        access_token=account.access_token,
        app_id=account.app_id,
        installation_id=account.installation_id,
        private_key=account.private_key,
    )
    return GithubClient(credentials), repo


def get_github_client_for_org(
    session: Any, org: str, user_id: int
) -> GithubClient | None:
    """Get authenticated GithubClient for an organization.

    Finds a GitHub account that has access to the specified org.

    Args:
        session: Database session
        org: Organization login name
        user_id: ID of the authenticated user

    Returns:
        GithubClient or None if no suitable account found
    """
    # Find an account that has teams in this org, or just any active account
    team_with_org = (
        session.query(GithubTeam)
        .join(GithubAccount)
        .filter(
            GithubTeam.org_login.ilike(org),
            GithubAccount.user_id == user_id,
            GithubAccount.active == True,  # noqa: E712
        )
        .first()
    )

    if team_with_org:
        account = team_with_org.account
    else:
        # Fall back to any active account
        account = (
            session.query(GithubAccount)
            .filter(
                GithubAccount.user_id == user_id,
                GithubAccount.active == True,  # noqa: E712
            )
            .first()
        )

    if not account:
        return None

    credentials = GithubCredentials(
        auth_type=account.auth_type,
        access_token=account.access_token,
        app_id=account.app_id,
        installation_id=account.installation_id,
        private_key=account.private_key,
    )
    return GithubClient(credentials)


# =============================================================================
# Issue Operation Helpers
# =============================================================================


def handle_project_integration(
    client: GithubClient,
    owner: str,
    repo_name: str,
    issue_number: int,
    issue_node_id: str | None,
    project_name: str,
    project_fields: dict[str, str] | None,
) -> tuple[list[str], str | None]:
    """Add issue to project and set field values.

    Returns:
        Tuple of (list of update messages, error message or None)
    """
    updates: list[str] = []

    if not issue_node_id:
        issue_node_id = client.get_issue_node_id(owner, repo_name, issue_number)
    if not issue_node_id:
        logger.warning(
            f"Could not get issue node ID for {owner}/{repo_name}#{issue_number}"
        )
        return updates, "Could not get issue node ID"

    # Try org project first, then user project
    project_info = client.find_project_by_name(owner, project_name, is_org=True)
    if not project_info:
        project_info = client.find_project_by_name(owner, project_name, is_org=False)
    if not project_info:
        return updates, f"Project '{project_name}' not found in org or user '{owner}'"

    project_id = project_info["id"]
    logger.debug(f"Found project '{project_name}' with ID {project_id}")

    # Add to project if not already there
    item_id = client.get_project_item_id(owner, repo_name, issue_number, project_id)
    if not item_id:
        logger.debug(
            f"Issue #{issue_number} not in project, adding with content_id={issue_node_id}"
        )
        item_id = client.add_issue_to_project(project_id, issue_node_id)
        if item_id:
            updates.append(f"Added to project '{project_name}'")
        else:
            return (
                updates,
                f"Failed to add to project '{project_name}' (check API logs for details)",
            )

    # Set field values
    if item_id and project_fields:
        available_fields = project_info.get("fields", {})
        for field_name, field_value in project_fields.items():
            msg = _set_project_field(
                client, project_id, item_id, field_name, field_value, available_fields
            )
            updates.append(msg)

    return updates, None


def _set_project_field(
    client: GithubClient,
    project_id: str,
    item_id: str,
    field_name: str,
    field_value: str,
    available_fields: dict[str, Any],
) -> str:
    """Set a single project field value. Returns status message."""
    field_def = available_fields.get(field_name)
    if not field_def:
        return f"Field '{field_name}' not found in project"

    field_id = field_def["id"]
    data_type = field_def.get("data_type", "TEXT")

    # Single-select field needs option ID resolution
    if "options" in field_def and field_def["options"]:
        option_id = field_def["options"].get(field_value)
        if not option_id:
            return f"Option '{field_value}' not found for field '{field_name}'"
        success = client.update_project_field_value(
            project_id, item_id, field_id, option_id, "singleSelectOptionId"
        )
    elif data_type == "NUMBER":
        try:
            num_value = float(field_value)
            success = client.update_project_field_value(
                project_id, item_id, field_id, str(num_value), "number"
            )
        except ValueError:
            return f"Invalid number '{field_value}' for field '{field_name}'"
    elif data_type == "DATE":
        # GitHub expects ISO 8601 date format (YYYY-MM-DD)
        success = client.update_project_field_value(
            project_id, item_id, field_id, field_value, "date"
        )
    else:
        # Text field (default)
        success = client.update_project_field_value(
            project_id, item_id, field_id, field_value, "text"
        )

    return (
        f"Set {field_name}={field_value}" if success else f"Failed to set {field_name}"
    )


def sync_issue_to_database(
    client: GithubClient,
    session: Any,
    repo_obj: GithubRepo | None,
    owner: str,
    repo_name: str,
    issue_number: int,
) -> tuple[bool, str | None]:
    """Fetch issue from GitHub via GraphQL and trigger database sync.

    Args:
        repo_obj: Can be None if repo isn't tracked - in that case,
                  the sync is skipped (issue was created but not tracked locally).

    Returns:
        Tuple of (success, error message or None)
    """
    if repo_obj is None:
        logger.info(
            f"Skipping database sync for {owner}/{repo_name}#{issue_number} - "
            "repo not tracked"
        )
        return (
            False,
            "Repository not tracked - issue created but not synced to database",
        )

    fetched = client.fetch_issue_graphql(owner, repo_name, issue_number)
    if fetched is None:
        return False, "Failed to fetch issue for sync"

    if repo_obj.track_project_fields:
        fetched["project_fields"] = client.fetch_project_fields(
            owner, repo_name, issue_number
        )

    # Look up milestone_id
    milestone_id = None
    if fetched.get("milestone_number"):
        ms = (
            session.query(GithubMilestone)
            .filter(
                GithubMilestone.repo_id == repo_obj.id,
                GithubMilestone.number == fetched["milestone_number"],
            )
            .first()
        )
        if ms:
            milestone_id = ms.id

    serialized = serialize_issue_data(fetched)
    serialized["milestone_id"] = milestone_id
    celery_app.send_task(SYNC_GITHUB_ITEM, args=[repo_obj.id, serialized])
    return True, None


def resolve_milestone_node_id(
    client: GithubClient,
    owner: str,
    repo_name: str,
    milestone: str | int,
) -> str | None:
    """Resolve milestone title or number to GraphQL node ID.

    Always queries GitHub directly for reliable resolution.
    """
    # Handle int directly, or string that looks like a number
    if isinstance(milestone, int):
        node_id = client.get_milestone_node_id(owner, repo_name, milestone)
        if node_id:
            logger.debug(f"Resolved milestone #{milestone} to node ID {node_id}")
        else:
            logger.warning(f"Milestone #{milestone} not found in {owner}/{repo_name}")
        return node_id

    # String - could be a number as string, or a title
    if isinstance(milestone, str) and milestone.isdigit():
        milestone_num = int(milestone)
        node_id = client.get_milestone_node_id(owner, repo_name, milestone_num)
        if node_id:
            logger.debug(f"Resolved milestone #{milestone_num} to node ID {node_id}")
        else:
            logger.warning(
                f"Milestone #{milestone_num} not found in {owner}/{repo_name}"
            )
        return node_id

    # Look up by title via GitHub API
    node_id = client.find_milestone_by_title(owner, repo_name, milestone)
    if node_id:
        logger.debug(f"Resolved milestone '{milestone}' to node ID {node_id}")
    else:
        logger.warning(f"Milestone '{milestone}' not found in {owner}/{repo_name}")
    return node_id


def create_issue(
    client: GithubClient,
    owner: str,
    repo_name: str,
    title: str,
    body: str | None,
    label_ids: list[str] | None,
    assignee_ids: list[str] | None,
    milestone_node_id: str | None,
) -> tuple[dict[str, Any], int]:
    """Create a new GitHub issue via GraphQL. Returns (issue_data, number)."""
    repository_id = client.get_repository_id(owner, repo_name)
    if not repository_id:
        raise ValueError(f"Could not get repository ID for {owner}/{repo_name}")

    issue_data = client.create_issue_graphql(
        repository_id=repository_id,
        title=title,
        body=body,
        label_ids=label_ids,
        assignee_ids=assignee_ids,
        milestone_id=milestone_node_id,
    )
    if not issue_data:
        raise ValueError("Failed to create issue on GitHub")

    return issue_data, issue_data["number"]


def update_issue(
    client: GithubClient,
    owner: str,
    repo_name: str,
    number: int,
    title: str,
    body: str | None,
    state: str | None,
    label_ids: list[str] | None,
    assignee_ids: list[str] | None,
    milestone_node_id: str | None,
) -> dict[str, Any]:
    """Update an existing GitHub issue via GraphQL."""
    issue_node_id = client.get_issue_node_id(owner, repo_name, number)
    if not issue_node_id:
        raise ValueError(f"Issue #{number} not found in {owner}/{repo_name}")

    issue_data = client.update_issue_graphql(
        issue_id=issue_node_id,
        title=title,
        body=body,
        state=state,
        label_ids=label_ids,
        assignee_ids=assignee_ids,
        milestone_id=milestone_node_id,
    )
    if not issue_data:
        raise ValueError(f"Failed to update issue #{number}")

    return issue_data


def add_issue_comment(
    client: GithubClient,
    owner: str,
    repo_name: str,
    number: int,
    body: str,
) -> dict[str, Any]:
    """Add a comment to a GitHub issue via GraphQL.

    Args:
        client: Authenticated GitHub client
        owner: Repository owner
        repo_name: Repository name
        number: Issue number
        body: Comment body (markdown supported)

    Returns:
        Dict with comment data including id, url, body, author, created_at
    """
    issue_node_id = client.get_issue_node_id(owner, repo_name, number)
    if not issue_node_id:
        raise ValueError(f"Issue #{number} not found in {owner}/{repo_name}")

    comment_data = client.add_issue_comment(issue_node_id, body)
    if not comment_data:
        raise ValueError(f"Failed to add comment to issue #{number}")

    return {
        "id": comment_data.get("databaseId"),
        "node_id": comment_data.get("id"),
        "url": comment_data.get("url"),
        "body": comment_data.get("body"),
        "author": comment_data.get("author", {}).get("login"),
        "created_at": comment_data.get("createdAt"),
    }
