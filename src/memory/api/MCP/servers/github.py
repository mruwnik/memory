"""MCP subserver for GitHub issue tracking and management."""

import logging
from datetime import datetime, timezone
from typing import Any

from fastmcp import FastMCP
from sqlalchemy import Text, desc, func
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import ARRAY

from memory.api.MCP.visibility import has_items, require_scopes, visible_when
from memory.api.search.search import search
from memory.api.search.types import SearchConfig, SearchFilters
from memory.common import extract
from memory.common.db.connection import make_session
from memory.common.db.models import GithubItem, GithubMilestone
from memory.common.db.models.sources import GithubRepo
from memory.common.celery_app import app as celery_app, SYNC_GITHUB_ITEM
from memory.parsers.github import GithubClient, GithubCredentials, serialize_issue_data

logger = logging.getLogger(__name__)

github_mcp = FastMCP("memory-github")


def _build_github_url(repo_path: str, number: int | None, kind: str) -> str:
    """Build GitHub URL from repo path and issue/PR number."""
    if number is None:
        return f"https://github.com/{repo_path}"
    url_type = "pull" if kind == "pr" else "issues"
    return f"https://github.com/{repo_path}/{url_type}/{number}"


def _serialize_issue(item: GithubItem, include_content: bool = False) -> dict[str, Any]:
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
        "comment_count": item.comment_count,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "closed_at": item.closed_at.isoformat() if item.closed_at else None,
        "merged_at": item.merged_at.isoformat() if item.merged_at else None,
        "github_updated_at": (
            item.github_updated_at.isoformat() if item.github_updated_at else None
        ),
        "url": _build_github_url(item.repo_path, item.number, item.kind),
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


@github_mcp.tool()
@visible_when(require_scopes("github"), has_items(GithubItem))
async def list_github_issues(
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
    """
    List GitHub issues and PRs with flexible filtering.
    Use for daily triage, finding assigned issues, tracking stale issues, etc.

    Args:
        repo: Filter by repository path (e.g., "owner/name")
        assignee: Filter by assignee username
        author: Filter by author username
        state: Filter by state: "open", "closed", "merged" (default: all)
        kind: Filter by type: "issue" or "pr" (default: both)
        labels: Filter by GitHub labels (matches ANY label in list)
        milestone: Filter by milestone title (string) or milestone ID (int)
        project_status: Filter by project status (e.g., "In Progress", "Backlog")
        project_field: Filter by project field values (e.g., {"EquiStamp.Client": "Redwood"})
        updated_since: ISO date - only issues updated after this time
        updated_before: ISO date - only issues updated before this (for finding stale issues)
        limit: Maximum results (default 50, max 200)
        order_by: Sort order: "updated", "created", or "number" (default: "updated" descending)

    Returns: List of issues with id, number, title, state, assignees, labels, project_fields, timestamps, url
    """
    logger.info(
        f"list_github_issues called: repo={repo}, assignee={assignee}, state={state}"
    )

    limit = min(limit, 200)

    with make_session() as session:
        query = session.query(GithubItem)

        if repo:
            query = query.filter(GithubItem.repo_path == repo)
        if assignee:
            query = query.filter(GithubItem.assignees.any(assignee))
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
                # Filter by milestone title via join
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

        return [_serialize_issue(item) for item in items]


@github_mcp.tool()
@visible_when(require_scopes("github"), has_items(GithubMilestone))
async def list_milestones(
    repo: str | None = None,
    state: str | None = None,
    has_due_date: bool | None = None,
    limit: int = 50,
) -> list[dict]:
    """
    List GitHub milestones with filtering options.
    Use to track project progress, find upcoming deadlines, or review milestone status.

    Args:
        repo: Filter by repository path (e.g., "owner/name")
        state: Filter by state: "open" or "closed" (default: all)
        has_due_date: If True, only milestones with due dates; if False, only without
        limit: Maximum results (default 50, max 200)

    Returns: List of milestones with id, number, title, description, state, due_on,
             open_issues, closed_issues (computed), progress percentage, url
    """
    logger.info(f"list_milestones called: repo={repo}, state={state}")

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

        # Order: due dates first (soonest), then by updated
        query = query.order_by(
            desc(GithubMilestone.due_on.isnot(None)),
            GithubMilestone.due_on,
            desc(GithubMilestone.github_updated_at),
        ).limit(limit)

        milestones = query.all()

        results = []
        for ms in milestones:
            # Count open and closed issues for this milestone
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


@github_mcp.tool()
@visible_when(require_scopes("github"), has_items(GithubItem))
async def search_github_issues(
    query: str,
    repo: str | None = None,
    state: str | None = None,
    kind: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """
    Search GitHub issues using natural language.
    Searches across issue titles, bodies, and comments.

    Args:
        query: Natural language search query (e.g., "authentication bug", "database migration")
        repo: Optional filter by repository path
        state: Optional filter: "open", "closed", "merged"
        kind: Optional filter: "issue" or "pr"
        limit: Maximum results (default 20, max 100)

    Returns: List of matching issues with search score
    """
    logger.info(f"search_github_issues called: query={query}, repo={repo}")

    limit = min(limit, 100)

    source_ids = None
    if repo or state or kind:
        with make_session() as session:
            q = session.query(GithubItem.id)
            if repo:
                q = q.filter(GithubItem.repo_path == repo)
            if state:
                q = q.filter(GithubItem.state == state)
            if kind:
                q = q.filter(GithubItem.kind == kind)
            else:
                q = q.filter(GithubItem.kind.in_(["issue", "pr"]))
            source_ids = [item.id for item in q.all()]

    data = extract.extract_text(query, skip_summary=True)
    config = SearchConfig(limit=limit, previews=True)
    filters = SearchFilters()
    if source_ids is not None:
        filters["source_ids"] = source_ids

    results = await search(
        data,
        modalities={"github"},
        filters=filters,
        config=config,
    )

    output = []
    with make_session() as session:
        for result in results:
            item = session.get(GithubItem, result.id)
            if item:
                serialized = _serialize_issue(item)
                serialized["search_score"] = result.search_score
                output.append(serialized)

    return output


@github_mcp.tool()
@visible_when(require_scopes("github"), has_items(GithubItem))
async def github_issue_details(
    repo: str,
    number: int,
) -> dict:
    """
    Get full details of a specific GitHub issue or PR including all comments.

    Args:
        repo: Repository path (e.g., "owner/name")
        number: Issue or PR number

    Returns: Full issue details including content (body + comments), project fields, timestamps.
             For PRs, also includes pr_data with: diff (full), files changed, reviews, review comments.
    """
    logger.info(f"github_issue_details called: repo={repo}, number={number}")

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

        return _serialize_issue(item, include_content=True)


def _get_github_client(session: Any, repo_path: str) -> tuple[GithubClient, GithubRepo]:
    """Get authenticated GithubClient for a repository path.

    Args:
        session: Database session
        repo_path: Repository path in "owner/repo" format

    Returns:
        Tuple of (GithubClient, GithubRepo)

    Raises:
        ValueError: If repo not found or credentials unavailable
    """
    parts = repo_path.split("/")
    if len(parts) != 2:
        raise ValueError(f"Invalid repo path format: {repo_path}")

    owner, name = parts
    repo = (
        session.query(GithubRepo)
        .filter(GithubRepo.owner == owner, GithubRepo.name == name)
        .first()
    )
    if not repo:
        raise ValueError(f"Repository {repo_path} not found in database")

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


def _handle_project_integration(
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
        logger.warning(f"Could not get issue node ID for {owner}/{repo_name}#{issue_number}")
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
        logger.debug(f"Issue #{issue_number} not in project, adding with content_id={issue_node_id}")
        item_id = client.add_issue_to_project(project_id, issue_node_id)
        if item_id:
            updates.append(f"Added to project '{project_name}'")
        else:
            return updates, f"Failed to add to project '{project_name}' (check API logs for details)"

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

    # Single-select field needs option ID resolution
    if "options" in field_def:
        option_id = field_def["options"].get(field_value)
        if not option_id:
            return f"Option '{field_value}' not found for field '{field_name}'"
        success = client.update_project_field_value(
            project_id, item_id, field_id, option_id, "singleSelectOptionId"
        )
    else:
        # Text field
        success = client.update_project_field_value(
            project_id, item_id, field_id, field_value, "text"
        )

    return f"Set {field_name}={field_value}" if success else f"Failed to set {field_name}"


def _sync_issue_to_database(
    client: GithubClient,
    session: Any,
    repo_obj: GithubRepo,
    owner: str,
    repo_name: str,
    issue_number: int,
) -> tuple[bool, str | None]:
    """Fetch issue from GitHub via GraphQL and trigger database sync.

    Returns:
        Tuple of (success, error message or None)
    """
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


def _resolve_milestone_node_id(
    client: GithubClient,
    session: Any,
    repo_obj: GithubRepo,
    owner: str,
    repo_name: str,
    milestone: str | int,
) -> str | None:
    """Resolve milestone title or number to GraphQL node ID."""
    if isinstance(milestone, int):
        return client.get_milestone_node_id(owner, repo_name, milestone)

    # Look up by title in database
    ms = (
        session.query(GithubMilestone)
        .filter(
            GithubMilestone.repo_id == repo_obj.id,
            GithubMilestone.title == milestone,
        )
        .first()
    )
    if ms:
        return client.get_milestone_node_id(owner, repo_name, ms.number)

    logger.warning(f"Milestone '{milestone}' not found in database")
    return None


def _create_issue(
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


def _update_issue(
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


@github_mcp.tool()
@visible_when(require_scopes("github"), has_items(GithubRepo))
async def upsert_github_issue(
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

    Returns:
        Dict with issue details including number, url, and any project updates made
    """
    logger.info(f"upsert_github_issue: repo={repo}, number={number}, title={title}")

    parts = repo.split("/")
    if len(parts) != 2:
        raise ValueError(f"Invalid repo format: {repo}. Expected 'owner/name'")
    owner, repo_name = parts

    with make_session() as session:
        client, repo_obj = _get_github_client(session, repo)

        # Resolve IDs for labels, assignees, milestone
        milestone_node_id = None
        if milestone is not None:
            milestone_node_id = _resolve_milestone_node_id(
                client, session, repo_obj, owner, repo_name, milestone
            )
        label_ids = client.get_label_ids(owner, repo_name, labels) if labels else None
        assignee_ids = client.get_user_ids(assignees) if assignees else None

        # Create or update
        if number is None:
            issue_data, number = _create_issue(
                client, owner, repo_name, title, body,
                label_ids, assignee_ids, milestone_node_id,
            )
            action = "created"
        else:
            issue_data = _update_issue(
                client, owner, repo_name, number, title, body, state,
                label_ids, assignee_ids, milestone_node_id,
            )
            action = "updated"

        result: dict[str, Any] = {
            "action": action,
            "number": number,
            "title": issue_data.get("title"),
            "state": issue_data.get("state"),
            "url": issue_data.get("url"),
            "project_updates": [],
        }

        # Handle project integration
        if project:
            updates, error = _handle_project_integration(
                client, owner, repo_name, number, issue_data.get("id"),
                project, project_fields,
            )
            result["project_updates"] = updates
            if error:
                result["project_error"] = error

        # Trigger database sync
        success, error = _sync_issue_to_database(
            client, session, repo_obj, owner, repo_name, number
        )
        result["sync_triggered"] = success
        if error:
            result["sync_error"] = error

        return result
