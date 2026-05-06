"""Project attach / promote / detach orchestration.

Functions:
- handle_attach: link a standalone project to a GitHub repo
- handle_promote_to_milestone: pin a repo-level project to a milestone
- handle_clear_repo: detach a project from its repo (becomes standalone)
- handle_clear_milestone: demote a milestone-level project to repo-level
"""

import logging
from typing import Any

from sqlalchemy import func

from memory.common.db.models import GithubItem, Project
from memory.common.db.models.sources import GithubAccount
from memory.common.github import GithubClient
from memory.common.project import client as _client
from memory.common.project.errors import (
    GithubClientUnavailableError,
    GithubSyncError,
    InvalidRepoPathError,
    LinkedItemsError,
    MilestoneCreationFailedError,
    MilestoneNotFoundOnGithubError,
    ProjectAlreadyAttachedError,
    ProjectStateInconsistentError,
    RepoCreationFailedError,
    RepoNotFoundOnGithubError,
)
from memory.common.project import sync as _sync


logger = logging.getLogger(__name__)


def handle_attach(
    session: Any,
    user: Any,
    project: Project,
    repo_path: str,
    create_repo: bool,
    private: bool,
) -> bool:
    """Attach a standalone project to a GitHub repo.

    Validates the repo on GitHub (via existing `ensure_github_repo`),
    creates a `GithubRepo` row if missing, then refreshes from GitHub.

    Returns:
        repo_was_just_created: True if this attach created a brand-new GitHub
        repo. The flag tells the caller whether subsequent operations (e.g.
        `handle_promote_to_milestone`) can skip their own `refresh_from_github`
        call to avoid the eventual-consistency window.

    Note (TOCTOU): the "is this project already attached?" check at
    `project.repo is None` runs without locking. Two concurrent callers can
    each read `repo is None`, each set `project.repo_id` to (potentially
    different) repos, and last-commit-wins silently. Symmetric with the
    handle_clear_repo race (see its docstring). For a single-user MCP server
    this is mostly theoretical, but it is real with concurrent automation
    paths. Serialize attaches at the application level if the risk matters.

    Raises:
        InvalidRepoPathError, ProjectAlreadyAttachedError,
        GithubClientUnavailableError, RepoCreationFailedError,
        RepoNotFoundOnGithubError, plus any GithubSyncError raised by
        the post-attach refresh.
    """
    if "/" not in repo_path:
        raise InvalidRepoPathError(repo_path)
    owner, repo_name = repo_path.split("/", 1)

    # If project already attached to this same repo: no-op for the link
    if project.repo is not None:
        if (
            project.repo.owner.lower() == owner.lower()
            and project.repo.name.lower() == repo_name.lower()
        ):
            return False
        raise ProjectAlreadyAttachedError(project.repo.owner, project.repo.name)

    try:
        client, repo_obj = _client.get_github_client(session, repo_path, user.id)
    except ValueError as e:
        raise GithubClientUnavailableError(
            f"Could not get GitHub client: {e}"
        ) from e

    repo_was_just_created = False
    if not repo_obj:
        account = (
            session.query(GithubAccount)
            .filter(
                GithubAccount.user_id == user.id,
                GithubAccount.active.is_(True),
            )
            .first()
        )
        if not account:
            raise GithubClientUnavailableError("No GitHub account configured")

        repo_obj, repo_was_just_created, _ = _client.ensure_github_repo(
            session,
            client,
            account.id,
            owner,
            repo_name,
            create_if_missing=create_repo,
            private=private,
        )
        if not repo_obj:
            if create_repo:
                # Match historic message wording for handle_attach
                raise RepoCreationFailedError(repo_path)
            raise RepoNotFoundOnGithubError(repo_path)

    project.repo_id = repo_obj.id
    project.repo = repo_obj  # Sync relationship so refresh_from_github sees the repo

    # Skip refresh if we just created the repo on GitHub — it may not be
    # immediately readable, and ensure_repository already gave us authoritative state.
    if repo_was_just_created:
        return True

    # Refresh state from GitHub for the now-attached project. refresh_from_github
    # is mutate-only and does not commit, so an in-memory rollback here is sufficient
    # to keep the project standalone if the refresh reports an error.
    #
    # Narrow catch: only handle the documented refresh_from_github raises
    # (GithubSyncError covers RepoMissing/RepoArchived/MilestoneMissing,
    # ProjectStateInconsistentError covers the repo-row invariant break).
    # Any other exception (transient OperationalError, KeyError from a
    # malformed payload, etc.) propagates without the rollback dance —
    # leaving it to the outer transaction handler.
    try:
        _sync.refresh_from_github(session, client, project)
    except (GithubSyncError, ProjectStateInconsistentError):
        # Roll back the link assignment so the project stays standalone.
        project.repo_id = None
        project.repo = None
        # If the refresh wanted to deactivate the repo (missing/archived), persist
        # that side effect explicitly so periodic sync sees the inactivation too.
        if repo_obj.active is False:
            _sync.mark_repo_inactive(session, repo_obj)
        raise
    return False


def handle_promote_to_milestone(
    session: Any,
    client: GithubClient,
    project: Project,
    milestone_title: str,
    create_milestone: bool,
    skip_refresh: bool = False,
) -> None:
    """Promote a repo-level project to milestone-level.

    Looks up the milestone on GitHub (creating it if `create_milestone=True`),
    then sets `project.github_id` and `project.number` and overlays milestone state.

    Args:
        skip_refresh: If True, skip the `refresh_from_github` call after
            assigning github_id/number. Used when the repo was just created
            in the same call (eventual-consistency window — the repo isn't
            immediately readable via `get_repo`). The milestone data we just
            wrote is already authoritative in that case.

    Raises:
        ProjectStateInconsistentError: project has no repo to promote against.
        MilestoneNotFoundOnGithubError: milestone missing and create_milestone=False.
        MilestoneCreationFailedError: milestone creation failed.
        Plus any GithubSyncError raised by the post-promote refresh.
    """
    repo = project.repo
    if repo is None:
        raise ProjectStateInconsistentError(
            "Cannot promote to milestone: project has no repo."
        )

    if create_milestone:
        ms_data, _ = client.ensure_milestone(repo.owner, repo.name, milestone_title)
    else:
        ms_data = None
        for ms in client.fetch_milestones(repo.owner, repo.name):
            if ms["title"] == milestone_title:
                ms_data = ms
                break
        if ms_data is None:
            raise MilestoneNotFoundOnGithubError(
                milestone_title, repo.owner, repo.name
            )

    if not ms_data:
        raise MilestoneCreationFailedError(milestone_title)

    project.github_id = ms_data["github_id"]
    project.number = ms_data["number"]
    if skip_refresh:
        # Repo was just created — `get_repo` may not see it yet, and the
        # milestone data we just wrote is authoritative. Overlay the basic
        # title/state so callers can read coherent fields without committing
        # a refresh that would falsely inactivate the repo.
        project.title = ms_data["title"]
        project.description = ms_data.get("description")
        project.state = ms_data.get("state", "open")
        project.due_on = ms_data.get("due_on")
        return

    # See note above (handle_attach): only handle the documented
    # refresh_from_github raises here. Other exceptions propagate.
    try:
        _sync.refresh_from_github(session, client, project)
    except (GithubSyncError, ProjectStateInconsistentError):
        project.github_id = None
        project.number = None
        # Persist the deactivation of the repo if the refresh decided so —
        # periodic sync will agree once eventual consistency catches up.
        if repo.active is False:
            _sync.mark_repo_inactive(session, repo)
        raise


def handle_clear_repo(session: Any, project: Project, force: bool) -> None:
    """Detach a project from its GitHub repo.

    Mutates the project in-memory; the caller decides whether to commit.
    Standalone projects pass through as a no-op.

    Note (TOCTOU): the linked-item count is checked without locking. A
    concurrent worker (e.g. periodic GitHub sync) inserting a `GithubItem`
    between the count query and the commit will leave that item with a
    `project_id` pointing at a now-detached project. For a single-user MCP
    server this is mostly theoretical, but it is real with the periodic
    sync. Pass `force=True` if you accept the risk; otherwise serialize
    detaches against running syncs at the application level.

    Raises:
        LinkedItemsError: project has linked GithubItems and force=False.
    """
    if project.repo_id is None:
        return

    if not force:
        item_count = (
            session.query(func.count(GithubItem.id))
            .filter(GithubItem.project_id == project.id)
            .scalar()
        ) or 0
        if item_count > 0:
            raise LinkedItemsError(item_count, "detach")

    project.repo_id = None
    project.github_id = None
    project.number = None


def handle_clear_milestone(session: Any, project: Project, force: bool) -> None:
    """Demote a milestone-level project to repo-level.

    Mutates the project in-memory; the caller decides whether to commit.
    Repo-level projects (number is None) pass through as a no-op.

    Note (TOCTOU): same caveat as `handle_clear_repo` — a concurrent
    worker inserting a `GithubItem` between the count query and the commit
    can orphan items against the demoted project. Use `force=True` if you
    accept the risk.

    Raises:
        LinkedItemsError: project has linked GithubItems and force=False.
    """
    if project.number is None:
        return

    if not force:
        item_count = (
            session.query(func.count(GithubItem.id))
            .filter(GithubItem.project_id == project.id)
            .scalar()
        ) or 0
        if item_count > 0:
            raise LinkedItemsError(item_count, "demote")

    project.github_id = None
    project.number = None
