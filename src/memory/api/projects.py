"""API endpoints for the Projects View feature.

Provides endpoints for viewing milestones across tracked GitHub repositories,
grouped by client/repo for project management visibility.
"""

from typing import cast

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_
from sqlalchemy.orm import Session

from memory.api.auth import get_current_user
from memory.common.db.connection import get_session
from memory.common.db.models import User, GithubMilestone
from memory.common.db.models.sources import GithubRepo, GithubAccount

router = APIRouter(prefix="/projects", tags=["projects"])


# --- Response Models ---


class MilestoneResponse(BaseModel):
    """A milestone with progress information."""

    id: int
    repo_path: str
    repo_name: str
    number: int
    title: str
    description: str | None
    state: str
    due_on: str | None
    open_issues: int
    closed_issues: int
    total_issues: int
    progress_percent: int
    github_created_at: str | None
    github_updated_at: str | None
    url: str


class RepoMilestonesResponse(BaseModel):
    """Milestones grouped by repository."""

    repo_path: str
    repo_name: str
    owner: str
    milestones: list[MilestoneResponse]
    total_open_milestones: int
    total_closed_milestones: int


class ProjectsOverviewResponse(BaseModel):
    """Overview of all projects/milestones."""

    repos: list[RepoMilestonesResponse]
    total_repos: int
    total_open_milestones: int
    total_closed_milestones: int
    last_updated: str | None


# --- Helper Functions ---


def milestone_to_response(milestone: GithubMilestone, repo: GithubRepo) -> MilestoneResponse:
    """Convert a GithubMilestone to response model."""
    open_issues = cast(int, milestone.open_issues) or 0
    closed_issues = cast(int, milestone.closed_issues) or 0
    total = open_issues + closed_issues
    progress = round(100 * closed_issues / max(1, total))

    return MilestoneResponse(
        id=cast(int, milestone.id),
        repo_path=repo.repo_path,
        repo_name=cast(str, repo.name),
        number=cast(int, milestone.number),
        title=cast(str, milestone.title),
        description=cast(str | None, milestone.description),
        state=cast(str, milestone.state),
        due_on=milestone.due_on.isoformat() if milestone.due_on else None,
        open_issues=open_issues,
        closed_issues=closed_issues,
        total_issues=total,
        progress_percent=progress,
        github_created_at=(
            milestone.github_created_at.isoformat() if milestone.github_created_at else None
        ),
        github_updated_at=(
            milestone.github_updated_at.isoformat() if milestone.github_updated_at else None
        ),
        url=f"https://github.com/{repo.repo_path}/milestone/{milestone.number}",
    )


# --- Endpoints ---


@router.get("/milestones")
def list_milestones(
    state: str | None = Query(None, description="Filter by state: 'open', 'closed', or None for all"),
    repo_filter: list[str] | None = Query(None, description="Filter by repo paths (owner/name)"),
    include_closed: bool = Query(False, description="Include closed milestones"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ProjectsOverviewResponse:
    """Get all milestones across tracked repositories.

    Returns milestones grouped by repository for the Projects view.
    """
    # Build query for all milestones from active repos
    query = (
        db.query(GithubMilestone, GithubRepo)
        .join(GithubRepo, GithubMilestone.repo_id == GithubRepo.id)
        .join(GithubAccount, GithubRepo.account_id == GithubAccount.id)
        .filter(
            GithubRepo.active == True,  # noqa: E712
            GithubAccount.active == True,  # noqa: E712
        )
    )

    # Filter by state
    if state:
        query = query.filter(GithubMilestone.state == state)
    elif not include_closed:
        query = query.filter(GithubMilestone.state == "open")

    # Filter by repo paths
    if repo_filter:
        # Build OR conditions for each repo path
        repo_conditions = []
        for repo_path in repo_filter:
            if "/" in repo_path:
                owner, name = repo_path.split("/", 1)
                repo_conditions.append(
                    and_(GithubRepo.owner == owner, GithubRepo.name == name)
                )
        if repo_conditions:
            from sqlalchemy import or_

            query = query.filter(or_(*repo_conditions))

    # Order by due date, then by repo
    query = query.order_by(
        GithubMilestone.due_on.asc().nullslast(),
        GithubRepo.owner,
        GithubRepo.name,
        GithubMilestone.title,
    )

    results = query.all()

    # Group by repo
    repos_dict: dict[str, RepoMilestonesResponse] = {}
    last_updated = None

    for milestone, repo in results:
        repo_path = repo.repo_path
        milestone_resp = milestone_to_response(milestone, repo)

        # Track last updated
        if milestone.github_updated_at:
            if last_updated is None or milestone.github_updated_at > last_updated:
                last_updated = milestone.github_updated_at

        if repo_path not in repos_dict:
            repos_dict[repo_path] = RepoMilestonesResponse(
                repo_path=repo_path,
                repo_name=cast(str, repo.name),
                owner=cast(str, repo.owner),
                milestones=[],
                total_open_milestones=0,
                total_closed_milestones=0,
            )

        repos_dict[repo_path].milestones.append(milestone_resp)
        if milestone.state == "open":
            repos_dict[repo_path].total_open_milestones += 1
        else:
            repos_dict[repo_path].total_closed_milestones += 1

    # Build response
    repos_list = list(repos_dict.values())
    total_open = sum(r.total_open_milestones for r in repos_list)
    total_closed = sum(r.total_closed_milestones for r in repos_list)

    return ProjectsOverviewResponse(
        repos=repos_list,
        total_repos=len(repos_list),
        total_open_milestones=total_open,
        total_closed_milestones=total_closed,
        last_updated=last_updated.isoformat() if last_updated else None,
    )


@router.get("/milestones/{milestone_id}")
def get_milestone(
    milestone_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> MilestoneResponse:
    """Get a single milestone by ID."""
    result = (
        db.query(GithubMilestone, GithubRepo)
        .join(GithubRepo, GithubMilestone.repo_id == GithubRepo.id)
        .filter(GithubMilestone.id == milestone_id)
        .first()
    )

    if not result:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Milestone not found")

    milestone, repo = result
    return milestone_to_response(milestone, repo)


@router.get("/repos")
def list_tracked_repos(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict]:
    """List all tracked repos that can have milestones."""
    repos = (
        db.query(GithubRepo)
        .join(GithubAccount)
        .filter(
            GithubRepo.active == True,  # noqa: E712
            GithubAccount.active == True,  # noqa: E712
        )
        .order_by(GithubRepo.owner, GithubRepo.name)
        .all()
    )

    return [
        {
            "id": repo.id,
            "repo_path": repo.repo_path,
            "owner": repo.owner,
            "name": repo.name,
            "last_sync_at": repo.last_sync_at.isoformat() if repo.last_sync_at else None,
        }
        for repo in repos
    ]
