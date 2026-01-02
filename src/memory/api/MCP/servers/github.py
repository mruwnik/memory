"""MCP subserver for GitHub issue tracking and management."""

import logging
from datetime import datetime, timezone
from typing import Any

from fastmcp import FastMCP
from sqlalchemy import Text, case, desc, func
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import ARRAY

from memory.api.search.search import search
from memory.api.search.types import SearchConfig, SearchFilters
from memory.common import extract
from memory.common.db.connection import make_session
from memory.common.db.models import GithubItem, GithubMilestone
from memory.common.db.models.sources import GithubRepo

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
    logger.info(f"list_github_issues called: repo={repo}, assignee={assignee}, state={state}")

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

            results.append({
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
            })

        return results


@github_mcp.tool()
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
                serialized["search_score"] = result.score
                output.append(serialized)

    return output


@github_mcp.tool()
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


@github_mcp.tool()
async def github_work_summary(
    since: str,
    until: str | None = None,
    group_by: str = "client",
    repo: str | None = None,
) -> dict:
    """
    Summarize GitHub work activity for billing and time tracking.
    Groups issues by client, author, status, or repository.

    Args:
        since: ISO date - start of period (e.g., "2025-12-16")
        until: ISO date - end of period (default: now)
        group_by: How to group results: "client", "status", "author", "repo", "task_type"
        repo: Optional filter by repository path

    Returns: Summary with grouped counts and sample issues for each group
    """
    logger.info(f"github_work_summary called: since={since}, group_by={group_by}")

    since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    if until:
        until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
    else:
        until_dt = datetime.now(timezone.utc)

    group_mappings = {
        "client": GithubItem.project_fields["EquiStamp.Client"].astext,
        "status": GithubItem.project_status,
        "author": GithubItem.author,
        "repo": GithubItem.repo_path,
        "task_type": GithubItem.project_fields["EquiStamp.Task Type"].astext,
    }

    if group_by not in group_mappings:
        raise ValueError(
            f"Invalid group_by: {group_by}. Must be one of: {list(group_mappings.keys())}"
        )

    group_col = group_mappings[group_by]

    with make_session() as session:
        base_query = session.query(GithubItem).filter(
            GithubItem.github_updated_at >= since_dt,
            GithubItem.github_updated_at <= until_dt,
            GithubItem.kind.in_(["issue", "pr"]),
        )

        if repo:
            base_query = base_query.filter(GithubItem.repo_path == repo)

        agg_query = (
            session.query(
                group_col.label("group_name"),
                func.count(GithubItem.id).label("total"),
                func.count(case((GithubItem.kind == "issue", 1))).label("issue_count"),
                func.count(case((GithubItem.kind == "pr", 1))).label("pr_count"),
                func.count(
                    case((GithubItem.state.in_(["closed", "merged"]), 1))
                ).label("closed_count"),
            )
            .filter(
                GithubItem.github_updated_at >= since_dt,
                GithubItem.github_updated_at <= until_dt,
                GithubItem.kind.in_(["issue", "pr"]),
            )
            .group_by(group_col)
            .order_by(desc("total"))
        )

        if repo:
            agg_query = agg_query.filter(GithubItem.repo_path == repo)

        groups = agg_query.all()

        summary = []
        total_issues = 0
        total_prs = 0

        for group_name, total, issue_count, pr_count, closed_count in groups:
            if group_name is None:
                group_name = "(unset)"

            total_issues += issue_count
            total_prs += pr_count

            sample_query = base_query.filter(group_col == group_name).limit(5)
            samples = [
                {
                    "number": item.number,
                    "title": item.title,
                    "repo_path": item.repo_path,
                    "state": item.state,
                    "url": _build_github_url(item.repo_path, item.number, item.kind),
                }
                for item in sample_query.all()
            ]

            summary.append(
                {
                    "group": group_name,
                    "total": total,
                    "issue_count": issue_count,
                    "pr_count": pr_count,
                    "closed_count": closed_count,
                    "issues": samples,
                }
            )

        return {
            "period": {
                "since": since_dt.isoformat(),
                "until": until_dt.isoformat(),
            },
            "group_by": group_by,
            "summary": summary,
            "total_issues": total_issues,
            "total_prs": total_prs,
        }


@github_mcp.tool()
async def github_repo_overview(
    repo: str,
) -> dict:
    """
    Get an overview of a GitHub repository's issues and PRs.
    Shows counts, status breakdown, top assignees, and labels.

    Args:
        repo: Repository path (e.g., "EquiStamp/equistamp" or "owner/name")

    Returns: Repository statistics including counts, status breakdown, top assignees, labels
    """
    logger.info(f"github_repo_overview called: repo={repo}")

    with make_session() as session:
        counts_query = session.query(
            func.count(GithubItem.id).label("total"),
            func.count(case((GithubItem.kind == "issue", 1))).label("total_issues"),
            func.count(
                case(((GithubItem.kind == "issue") & (GithubItem.state == "open"), 1))
            ).label("open_issues"),
            func.count(
                case(((GithubItem.kind == "issue") & (GithubItem.state == "closed"), 1))
            ).label("closed_issues"),
            func.count(case((GithubItem.kind == "pr", 1))).label("total_prs"),
            func.count(
                case(((GithubItem.kind == "pr") & (GithubItem.state == "open"), 1))
            ).label("open_prs"),
            func.count(
                case(((GithubItem.kind == "pr") & (GithubItem.merged_at.isnot(None)), 1))
            ).label("merged_prs"),
            func.max(GithubItem.github_updated_at).label("last_updated"),
        ).filter(
            GithubItem.repo_path == repo,
            GithubItem.kind.in_(["issue", "pr"]),
        )

        counts = counts_query.first()

        status_query = (
            session.query(
                GithubItem.project_status.label("status"),
                func.count(GithubItem.id).label("count"),
            )
            .filter(
                GithubItem.repo_path == repo,
                GithubItem.kind.in_(["issue", "pr"]),
                GithubItem.project_status.isnot(None),
            )
            .group_by(GithubItem.project_status)
            .order_by(desc("count"))
        )

        status_breakdown = {row.status: row.count for row in status_query.all()}

        assignee_query = (
            session.query(
                func.unnest(GithubItem.assignees).label("assignee"),
                func.count(GithubItem.id).label("count"),
            )
            .filter(
                GithubItem.repo_path == repo,
                GithubItem.kind.in_(["issue", "pr"]),
                GithubItem.state == "open",
            )
            .group_by("assignee")
            .order_by(desc("count"))
            .limit(10)
        )

        top_assignees = [
            {"username": row.assignee, "open_count": row.count}
            for row in assignee_query.all()
        ]

        label_query = (
            session.query(
                func.unnest(GithubItem.labels).label("label"),
                func.count(GithubItem.id).label("count"),
            )
            .filter(
                GithubItem.repo_path == repo,
                GithubItem.kind.in_(["issue", "pr"]),
            )
            .group_by("label")
            .order_by(desc("count"))
            .limit(20)
        )

        labels = {row.label: row.count for row in label_query.all()}

        return {
            "repo_path": repo,
            "counts": {
                "total": counts.total if counts else 0,
                "total_issues": counts.total_issues if counts else 0,
                "open_issues": counts.open_issues if counts else 0,
                "closed_issues": counts.closed_issues if counts else 0,
                "total_prs": counts.total_prs if counts else 0,
                "open_prs": counts.open_prs if counts else 0,
                "merged_prs": counts.merged_prs if counts else 0,
            },
            "status_breakdown": status_breakdown,
            "top_assignees": top_assignees,
            "labels": labels,
            "last_updated": (
                counts.last_updated.isoformat()
                if counts and counts.last_updated
                else None
            ),
        }
