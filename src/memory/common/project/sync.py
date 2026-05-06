"""GitHub <-> Project sync helpers (refresh state, push due dates)."""

import logging
from datetime import datetime, timezone
from typing import Any

from memory.common.db.models import Project
from memory.common.db.models.sources import GithubRepo
from memory.common.github import GithubClient, GithubCredentials
from memory.common.project.errors import (
    GithubMilestoneSyncError,
    MilestoneMissingError,
    ProjectStateInconsistentError,
    RepoArchivedError,
    RepoMissingError,
)


logger = logging.getLogger(__name__)


def mark_repo_inactive(session: Any, repo: GithubRepo) -> None:
    """Persist `repo.active = False` in its own commit.

    Used by callers that want the deactivation to survive even when the
    larger operation returns an error. Kept separate from
    `refresh_from_github` so the refresh can stay mutate-only and let the
    caller control commit boundaries.
    """
    repo.active = False  # type: ignore[assignment]
    session.commit()


def refresh_from_github(
    session: Any,
    client: GithubClient,
    project: Project,
) -> None:
    """Pull live state from GitHub and overlay onto a GitHub-backed project.

    Mutate-only: this function does NOT commit. The caller decides whether
    to commit the overlay (success) or roll back (error). The repo
    deactivation side effect for missing/archived repos is also left
    uncommitted — callers that want it to persist across an error return
    must invoke `mark_repo_inactive` explicitly before commit.

    Side effects (in-memory only):
        - Sets repo.active = False if repo is missing or archived on GitHub.
        - Updates project title/description/state/due_on/timestamps for milestones.

    Raises:
        ProjectStateInconsistentError: project has repo_id but no repo row.
        RepoMissingError: repo has been deleted on GitHub.
        RepoArchivedError: repo is archived on GitHub.
        MilestoneMissingError: milestone has been deleted on GitHub.
    """
    repo = project.repo
    if repo is None:
        raise ProjectStateInconsistentError()

    repo_data = client.get_repo(repo.owner, repo.name)
    if repo_data is None:
        repo.active = False  # type: ignore[assignment]
        raise RepoMissingError(repo.owner, repo.name)
    if repo_data.get("archived", False):
        repo.active = False  # type: ignore[assignment]
        raise RepoArchivedError(repo.owner, repo.name)

    if project.number is None:
        # Repo-level project: nothing milestone-shaped to refresh
        return

    milestone_data = client.fetch_milestone(repo.owner, repo.name, project.number)
    if milestone_data is None:
        raise MilestoneMissingError(repo.owner, repo.name, project.number)

    project.title = milestone_data["title"]
    project.description = milestone_data["description"]
    project.state = milestone_data["state"]
    project.due_on = milestone_data["due_on"]
    project.github_created_at = milestone_data["github_created_at"]
    project.github_updated_at = milestone_data["github_updated_at"]
    project.closed_at = milestone_data["closed_at"]


def sync_milestone_due_date(
    project: Project,
    new_due_on: datetime | None,
) -> None:
    """Sync a project's due date to its GitHub milestone.

    This function syncs the due_on date to GitHub for milestone-backed projects.
    It should be called BEFORE updating the local database to ensure consistency
    (if GitHub fails, we don't leave the local database in an inconsistent state).

    Args:
        project: The project to sync (must have repo and milestone number)
        new_due_on: The new due date to set, or None to clear it

    Raises:
        GithubMilestoneSyncError: if GitHub rejects the update.
    """
    github_repo = project.repo
    if not github_repo:
        return

    account = github_repo.account
    if not account:
        logger.warning(
            f"Cannot sync due_on to GitHub: repo {github_repo.owner}/{github_repo.name} "
            "has no associated account"
        )
        return

    # Format for GitHub API (None clears the date)
    github_due_on: str | None = None
    if new_due_on is not None:
        utc_due_on = new_due_on.astimezone(timezone.utc)
        github_due_on = utc_due_on.strftime("%Y-%m-%dT%H:%M:%SZ")

    credentials = GithubCredentials(
        auth_type=account.auth_type,
        access_token=account.access_token,
        app_id=account.app_id,
        installation_id=account.installation_id,
        private_key=account.private_key,
    )
    client = GithubClient(credentials)
    try:
        if project.number is None:
            raise GithubMilestoneSyncError("Project has no milestone number")
        result = client.update_milestone(
            owner=github_repo.owner,
            repo=github_repo.name,
            milestone_number=project.number,
            due_on=github_due_on,
        )
        if result is None:
            raise GithubMilestoneSyncError(
                "Failed to update GitHub milestone due date"
            )
    except GithubMilestoneSyncError:
        raise
    except Exception as e:
        logger.exception("Failed to sync due date to GitHub")
        raise GithubMilestoneSyncError(
            f"Failed to sync due date to GitHub: {e}"
        ) from e
