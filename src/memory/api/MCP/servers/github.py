"""MCP subserver for GitHub issue tracking and management."""

import asyncio
import logging
from typing import Any, Literal

from fastmcp import FastMCP

from memory.api.MCP.access import get_mcp_current_user
from memory.api.MCP.visibility import require_scopes, visible_when
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import UserSession
from memory.common.db.models.sources import GithubAccount

from memory.api.MCP.servers.github_helpers import (
    list_issues,
    list_milestones,
    list_projects,
    list_teams,
    fetch_issue,
    fetch_milestone,
    fetch_project,
    fetch_team,
    get_github_client,
    get_github_client_for_org,
    handle_project_integration,
    sync_issue_to_database,
    resolve_milestone_node_id,
    create_issue,
    update_issue,
    add_issue_comment,
)

logger = logging.getLogger(__name__)

github_mcp = FastMCP("memory-github")


async def has_github_account(user_info: dict, session: DBSession | None) -> bool:
    """Visibility checker: only show GitHub write tools if user has an active account."""
    token = user_info.get("token")
    if not token or session is None:
        return False

    def _check(session: DBSession) -> bool:
        user_session = session.get(UserSession, token)
        if not user_session or not user_session.user:
            return False
        return (
            session.query(GithubAccount)
            .filter(
                GithubAccount.user_id == user_session.user.id,
                GithubAccount.active == True,  # noqa: E712
            )
            .first()
            is not None
        )

    return await asyncio.to_thread(_check, session)


def _get_current_user_id() -> int:
    """Get the current user ID from the MCP access token."""
    user = get_mcp_current_user()
    if not user or user.id is None:
        raise ValueError("Not authenticated")
    return user.id


GithubEntityType = Literal["issue", "milestone", "project", "team"]


@github_mcp.tool()
@visible_when(require_scopes("github"))
async def list_entities(
    type: GithubEntityType,
    repo: str | None = None,
    owner: str | None = None,
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
    has_due_date: bool | None = None,
    include_closed: bool = False,
    limit: int = 50,
    order_by: str = "updated",
) -> list[dict]:
    """
    List GitHub entities (issues, milestones, projects, or teams).

    Args:
        type: Entity type to list: "issue", "milestone", "project", or "team"

        Common params:
            repo: Filter by repository path (e.g., "owner/name") - for issues/milestones
            owner: Filter by owner/org login - for projects/teams
            state: Filter by state (open/closed for issues/milestones)
            limit: Maximum results (default 50, max 200)

        Issue-specific params:
            assignee: Filter by assignee username
            author: Filter by author username
            kind: Filter by type: "issue" or "pr" (default: both)
            labels: Filter by GitHub labels (matches ANY label in list)
            milestone: Filter by milestone title (string) or milestone ID (int)
            project_status: Filter by project status (e.g., "In Progress", "Backlog")
            project_field: Filter by project field values (e.g., {"Client": "Acme"})
            updated_since: ISO date - only issues updated after this time
            updated_before: ISO date - only issues updated before this
            order_by: Sort order: "updated", "created", or "number"

        Milestone-specific params:
            has_due_date: If True, only milestones with due dates; if False, only without

        Project-specific params:
            include_closed: Include closed projects (default: False)

        Team-specific params:
            owner: Organization login (required for teams)

    Returns: List of matching entities with relevant fields
    """
    logger.info(f"github_list called: type={type}, repo={repo}, owner={owner}")

    if type == "issue":
        return list_issues(
            repo=repo,
            assignee=assignee,
            author=author,
            state=state,
            kind=kind,
            labels=labels,
            milestone=milestone,
            project_status=project_status,
            project_field=project_field,
            updated_since=updated_since,
            updated_before=updated_before,
            limit=limit,
            order_by=order_by,
        )
    elif type == "milestone":
        return list_milestones(
            repo=repo,
            state=state,
            has_due_date=has_due_date,
            limit=limit,
        )
    elif type == "project":
        return list_projects(
            owner=owner,
            include_closed=include_closed,
            limit=limit,
        )
    elif type == "team":
        user_id = _get_current_user_id()
        return list_teams(
            org=owner,
            user_id=user_id,
            limit=limit,
        )
    else:
        raise ValueError(f"Unknown type: {type}")


@github_mcp.tool()
@visible_when(require_scopes("github"))
async def fetch(
    type: GithubEntityType,
    repo: str | None = None,
    owner: str | None = None,
    number: int | None = None,
    slug: str | None = None,
) -> dict:
    """
    Get full details of a specific GitHub entity.

    Args:
        type: Entity type: "issue", "milestone", "project", or "team"
        repo: Repository path (e.g., "owner/name") - required for issue/milestone
        owner: Owner/org login - required for project/team
        number: Entity number (issue/PR number, milestone number, or project number)
        slug: Team slug (URL-safe name) - required for team

    Returns:
        For issues: Full details including content, comments, project fields.
                   PRs also include diff, files changed, reviews.
        For milestones: Details including description, due date, progress, and list of issues.
        For projects: Full project details including fields with options.
        For teams: Team details including member list.
    """
    logger.info(
        f"github_fetch called: type={type}, repo={repo}, owner={owner}, number={number}, slug={slug}"
    )

    if type == "issue":
        if not repo:
            raise ValueError("repo is required for fetching issues")
        if number is None:
            raise ValueError("number is required for fetching issues")
        return fetch_issue(repo, number)
    elif type == "milestone":
        if not repo:
            raise ValueError("repo is required for fetching milestones")
        if number is None:
            raise ValueError("number is required for fetching milestones")
        return fetch_milestone(repo, number)
    elif type == "project":
        if not owner:
            raise ValueError("owner is required for fetching projects")
        if number is None:
            raise ValueError("number is required for fetching projects")
        return fetch_project(owner, number)
    elif type == "team":
        if not owner:
            raise ValueError("owner (org) is required for fetching teams")
        if not slug:
            raise ValueError("slug is required for fetching teams")
        user_id = _get_current_user_id()
        return fetch_team(owner, slug, user_id=user_id)
    else:
        raise ValueError(f"Unknown type: {type}")


@github_mcp.tool()
@visible_when(require_scopes("github"), has_github_account)
async def upsert_issue(
    repo: str,
    title: str,
    body: str | None = None,
    number: int | None = None,
    state: str | None = None,
    labels: list[str] | None = None,
    assignees: list[str] | None = None,
    milestone: str | int | None = None,
    project: str | None = None,
    project_fields: dict[str, str] | None = None,
    deadline: str | None = None,
) -> dict:
    """
    Create or update a GitHub issue with optional project integration.

    Args:
        repo: Repository path (e.g., "owner/name")
        title: Issue title (required for create, optional for update)
        body: Issue body/description
        number: Issue number - if provided, updates existing; if None, creates new
        state: Issue state: "open" or "closed" (update only)
        labels: List of label names to apply
        assignees: List of GitHub usernames to assign
        milestone: Milestone title (string) or number (int) to assign
        project: Project name to add issue to (e.g., "My Project Board")
        project_fields: Dict of project field values (e.g., {"Status": "In Progress", "Priority": "High"})
        deadline: Due date in ISO format (YYYY-MM-DD). Sets the "Due Date" project field.

    Returns:
        Dict with issue details including number, url, and any project updates made
    """
    logger.info(f"upsert_github_issue: repo={repo}, number={number}, title={title}")

    parts = repo.split("/")
    if len(parts) != 2:
        raise ValueError(f"Invalid repo format: {repo}. Expected 'owner/name'")
    owner, repo_name = parts

    user = get_mcp_current_user()
    if not user or user.id is None:
        raise ValueError("Not authenticated")
    user_id = user.id

    with make_session() as session:
        client, repo_obj = get_github_client(session, repo, user_id)

        # Resolve IDs for labels, assignees, milestone
        milestone_node_id = None
        if milestone is not None:
            milestone_node_id = resolve_milestone_node_id(
                client, owner, repo_name, milestone
            )
        label_ids = client.get_label_ids(owner, repo_name, labels) if labels else None
        assignee_ids = client.get_user_ids(assignees) if assignees else None

        # Create or update
        if number is None:
            issue_data, number = create_issue(
                client,
                owner,
                repo_name,
                title,
                body,
                label_ids,
                assignee_ids,
                milestone_node_id,
            )
            action = "created"
        else:
            issue_data = update_issue(
                client,
                owner,
                repo_name,
                number,
                title,
                body,
                state,
                label_ids,
                assignee_ids,
                milestone_node_id,
            )
            action = "updated"

        # Merge deadline into project_fields before building result
        if deadline:
            project_fields = dict(project_fields) if project_fields else {}
            project_fields["Due Date"] = deadline

        result: dict[str, Any] = {
            "action": action,
            "number": number,
            "title": issue_data.get("title"),
            "state": issue_data.get("state"),
            "url": issue_data.get("url"),
            "project_updates": [],
            "milestone_id": milestone_node_id,
            "label_ids": label_ids,
            "assignee_ids": assignee_ids,
            "project_fields": project_fields,
        }

        # Handle project integration
        if project:
            updates, error = handle_project_integration(
                client,
                owner,
                repo_name,
                number,
                issue_data.get("id"),
                project,
                project_fields,
            )
            result["project_updates"] = updates
            if error:
                result["project_error"] = error

        # Trigger database sync (only if repo is tracked)
        if repo_obj is not None:
            success, error = sync_issue_to_database(
                client, session, repo_obj, owner, repo_name, number
            )
            result["sync_triggered"] = success
            if error:
                result["sync_error"] = error
        else:
            result["sync_triggered"] = False
            result["sync_note"] = (
                "Repository not tracked - issue created/updated but not synced to database"
            )

        return result


@github_mcp.tool()
@visible_when(require_scopes("github"), has_github_account)
async def add_team_member(
    org: str,
    team_slug: str,
    username: str,
    role: str = "member",
) -> dict:
    """
    Add a user to a GitHub team.

    If the user is not a member of the organization, they will be invited first.
    The invitation will include the team, so they'll be added automatically upon acceptance.

    Args:
        org: Organization login name
        team_slug: Team slug (URL-safe team name, e.g., "engineering" not "Engineering Team")
        username: GitHub username to add
        role: Team role: "member" or "maintainer" (default: "member")

    Returns:
        Dict with:
            - success: Whether the operation succeeded
            - action: What happened - "added", "invited", or "pending"
            - org_membership: User's org membership state before operation
            - note: Additional context (e.g., "User must accept team invitation")
            - error: Error message if failed
    """
    logger.info(
        f"github_add_team_member called: org={org}, team={team_slug}, user={username}"
    )

    user = get_mcp_current_user()
    if not user or user.id is None:
        raise ValueError("Not authenticated")
    user_id = user.id

    with make_session() as session:
        client = get_github_client_for_org(session, org, user_id)
        if not client:
            raise ValueError(f"No GitHub account configured with access to {org}")

        result = client.add_team_member(org, team_slug, username, role)
        return result


@github_mcp.tool()
@visible_when(require_scopes("github"), has_github_account)
async def remove_team_member(
    org: str,
    team_slug: str,
    username: str,
) -> dict:
    """
    Remove a user from a GitHub team.

    Note: This only removes from the team, not from the organization.

    Args:
        org: Organization login name
        team_slug: Team slug (URL-safe team name)
        username: GitHub username to remove

    Returns:
        Dict with success status and any error message
    """
    logger.info(
        f"github_remove_team_member called: org={org}, team={team_slug}, user={username}"
    )

    user = get_mcp_current_user()
    if not user or user.id is None:
        raise ValueError("Not authenticated")
    user_id = user.id

    with make_session() as session:
        client = get_github_client_for_org(session, org, user_id)
        if not client:
            raise ValueError(f"No GitHub account configured with access to {org}")

        success = client.remove_team_member(org, team_slug, username)
        return {
            "success": success,
            "action": "removed" if success else "failed",
            "org": org,
            "team": team_slug,
            "username": username,
        }


@github_mcp.tool()
@visible_when(require_scopes("github"), has_github_account)
async def comment_on_issue(
    repo: str,
    number: int,
    body: str,
) -> dict:
    """
    Add a comment to a GitHub issue or pull request.

    Args:
        repo: Repository path (e.g., "owner/name")
        number: Issue or PR number
        body: Comment body (markdown supported)

    Returns:
        Dict with comment details including id, url, body, author, created_at
    """
    logger.info(f"github_comment_on_issue: repo={repo}, number={number}")

    parts = repo.split("/")
    if len(parts) != 2:
        raise ValueError(f"Invalid repo format: {repo}. Expected 'owner/name'")
    owner, repo_name = parts

    user = get_mcp_current_user()
    if not user or user.id is None:
        raise ValueError("Not authenticated")
    user_id = user.id

    with make_session() as session:
        client, repo_obj = get_github_client(session, repo, user_id)

        comment_data = add_issue_comment(client, owner, repo_name, number, body)

        result: dict[str, Any] = {
            "success": True,
            "repo": repo,
            "issue_number": number,
            "comment": comment_data,
        }

        # Trigger database sync to ingest the new comment
        if repo_obj is not None:
            success, error = sync_issue_to_database(
                client, session, repo_obj, owner, repo_name, number
            )
            result["sync_triggered"] = success
            if error:
                result["sync_error"] = error
        else:
            result["sync_triggered"] = False
            result["sync_note"] = "Repository not tracked - comment added but not synced"

        return result
