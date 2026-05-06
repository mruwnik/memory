"""Project creation orchestration.

Creates standalone, repo-level, and milestone-level projects, taking care
of GitHub repo/milestone provisioning, team assignment (inbound + outbound
sync), and unique-ID generation.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from memory.common.db.models import Project, Team
from memory.common.db.models.sources import GithubAccount, GithubRepo
from memory.common.github import GithubClient
# Imported as a module alias (rather than `from .client import ...`) so tests
# can `patch("memory.common.project.creation._client.get_github_client", ...)`
# and have the patch intercept calls made from this module. A direct
# `from .client import get_github_client` would bind a local reference at import
# time and bypass test patches applied to `_client.get_github_client`.
from memory.common.project import client as _client
from memory.common.project.errors import (
    GithubClientUnavailableError,
    InvalidRepoPathError,
    MilestoneCreationFailedError,
    ProjectIdGenerationError,
    RepoCreationFailedError,
    RepoNotFoundOnGithubError,
)
from memory.common.project.teams import (
    SyncResult,
    sync_repo_teams_inbound,
    sync_repo_teams_outbound,
)


logger = logging.getLogger(__name__)


@dataclass
class ProjectCreationResult:
    """Outcome of a creation orchestration call.

    The MCP layer turns these into its response dicts. Non-MCP callers can
    read fields directly.
    """

    project: Project
    created: bool
    github_repo_created: bool = False
    tracking_created: bool = False
    milestone_created: bool = False
    sync_result: SyncResult | None = None
    inbound_teams: list[Team] = field(default_factory=list)


def _next_negative_project_id(session: Any) -> int:
    """Compute the next negative ID candidate (`min(id) - 1`).

    Used by `create_project_with_retry`. Note: the value is racy on its own —
    a concurrent writer can grab the same ID between this read and the
    eventual INSERT. The caller is responsible for handling IntegrityError
    via savepoint rollback + retry.
    """
    max_negative_id = (
        session.query(func.min(Project.id)).filter(Project.id < 0).scalar()
    )
    return (max_negative_id or 0) - 1


def create_project_with_retry(
    session: Any,
    teams: list[Team],
    max_retries: int = 3,
    **project_kwargs: Any,
) -> Project:
    """Create a project with negative ID, retrying on collision.

    Args:
        session: Database session
        teams: Teams to assign to the project
        max_retries: Number of retries on ID collision
        **project_kwargs: Arguments to pass to Project constructor (except id)

    Returns:
        Created Project.

    Raises:
        ProjectIdGenerationError: If the project couldn't be created after retries.
    """
    for attempt in range(max_retries):
        new_id = _next_negative_project_id(session)
        project = Project(id=new_id, **project_kwargs)

        # `begin_nested()` as a context manager auto-RELEASEs the SAVEPOINT
        # on a clean exit and ROLLBACKs to it on exception — we don't have
        # to track the handle ourselves, and we never leak a half-closed
        # nested transaction on the success path.
        try:
            with session.begin_nested():
                session.add(project)
                session.flush()

                # Assign teams
                for team in teams:
                    project.teams.append(team)
            return project
        except IntegrityError:
            # SAVEPOINT was rolled back by the context manager; retry with a
            # fresh ID candidate.
            if attempt == max_retries - 1:
                raise ProjectIdGenerationError()
            continue

    raise ProjectIdGenerationError("Failed to create project")


def find_existing_project_by_repo(
    session: Any,
    repo_path: str,
    milestone_title: str | None = None,
) -> Project | None:
    """Find an existing project by repo path and optional milestone title.

    Args:
        session: Database session
        repo_path: GitHub repo path (e.g., "owner/name")
        milestone_title: Optional milestone title to match. The match is
            case-sensitive and exact - if the local project title was
            modified after creation, it won't match the GitHub milestone.

    Returns:
        Existing Project if found, None otherwise
    """
    if "/" not in repo_path:
        return None

    owner, repo_name = repo_path.split("/", 1)

    # Look up repo in database
    repo_obj = (
        session.query(GithubRepo)
        .filter(GithubRepo.owner == owner, GithubRepo.name == repo_name)
        .first()
    )
    if not repo_obj:
        return None

    if milestone_title is not None:
        # Look for milestone project - match by title since we don't have the number yet
        # This is a best-effort match; if title was overridden, it won't match
        return (
            session.query(Project)
            .filter(
                Project.repo_id == repo_obj.id,
                Project.number.isnot(None),  # Has a milestone number
                Project.title == milestone_title,
            )
            .first()
        )
    else:
        # Look for repo-level project (no milestone)
        return (
            session.query(Project)
            .filter(Project.repo_id == repo_obj.id, Project.number.is_(None))
            .first()
        )


def get_inbound_teams(
    session: Session,
    client: GithubClient,
    owner: str,
    repo_name: str,
    existing_teams: list[Team],
    github_repo_created: bool,
) -> tuple[list[Team], list[Team]]:
    """Fetch teams from GitHub repo that should be added to a project.

    For existing repos (not newly created), fetches teams that have access
    on GitHub and returns any matching local teams that aren't already assigned.

    Args:
        session: Database session
        client: GitHub client
        owner: Repo owner
        repo_name: Repo name
        existing_teams: Teams already assigned to the project (not modified)
        github_repo_created: Whether the repo was just created (skip sync if so)

    Returns:
        Tuple of (teams_to_add, all_inbound_teams):
        - teams_to_add: Teams from GitHub not in existing_teams (to be added)
        - all_inbound_teams: All matching teams from GitHub (for reporting)
    """
    if github_repo_created:
        return [], []

    inbound_teams = sync_repo_teams_inbound(session, client, owner, repo_name)
    existing_team_ids = {t.id for t in existing_teams}
    teams_to_add = [t for t in inbound_teams if t.id not in existing_team_ids]
    return teams_to_add, inbound_teams


def perform_outbound_sync(
    client: GithubClient,
    owner: str,
    repo_name: str,
    teams: list[Team],
) -> SyncResult | None:
    """Grant teams access to repo on GitHub.

    Args:
        client: Authenticated GitHub client
        owner: Repository owner
        repo_name: Repository name
        teams: List of teams to sync

    Returns:
        SyncResult with synced/skipped/failed lists, or None if no teams provided.
        Logs warnings for any failed syncs.
    """
    if not teams:
        return None
    sync_result = sync_repo_teams_outbound(client, owner, repo_name, teams)
    if sync_result and sync_result.get("failed"):
        logger.warning(
            f"Some teams failed to sync to GitHub for {owner}/{repo_name}: "
            f"{sync_result['failed']}"
        )
    return sync_result


def create_standalone_project(
    session: Any,
    teams: list[Team],
    title: str,
    description: str | None,
    state: str,
    parent_id: int | None,
    owner_id: int | None,
    due_on: Any | None,
    doc_url: str | None,
) -> ProjectCreationResult:
    """Create a new standalone project.

    Validation (teams, parent, owner, due_on parsing) is the caller's
    responsibility — by the time we reach this function the inputs are
    expected to be ready-to-use ORM objects / parsed values.

    Raises:
        ProjectIdGenerationError: if the unique-ID retry loop fails.
    """
    logger.info(f"Creating standalone project: {title}")

    project = create_project_with_retry(
        session,
        teams,
        repo_id=None,
        github_id=None,
        number=None,
        title=title,
        description=description,
        state=state,
        parent_id=parent_id,
        owner_id=owner_id,
        due_on=due_on,
        doc_url=doc_url,
    )

    session.commit()
    session.refresh(project)

    return ProjectCreationResult(project=project, created=True)


def _ensure_repo_for_create(
    session: Any,
    user: Any,
    repo_path: str,
    description: str | None,
    create_repo: bool,
    private: bool,
) -> tuple[GithubClient, GithubRepo, bool, bool, str, str]:
    """Shared helper: parse repo path, get client, ensure repo tracking exists.

    Returns:
        Tuple of (client, repo_obj, github_repo_created, tracking_created, owner, repo_name).

    Raises:
        InvalidRepoPathError: repo_path malformed
        GithubClientUnavailableError: no GitHub client / account configured
        RepoCreationFailedError: create_repo=True but creation failed
        RepoNotFoundOnGithubError: repo doesn't exist and create_repo=False
    """
    if "/" not in repo_path:
        raise InvalidRepoPathError(repo_path)
    owner, repo_name = repo_path.split("/", 1)

    # Get GitHub client
    try:
        client, repo_obj = _client.get_github_client(session, repo_path, user.id)
    except ValueError as e:
        raise GithubClientUnavailableError(
            f"No GitHub access configured for '{repo_path}': {e}"
        ) from e
    if not client:
        raise GithubClientUnavailableError(
            f"No GitHub access configured for '{repo_path}'"
        )

    github_repo_created = False
    tracking_created = False
    if not repo_obj:
        # Get user's account for tracking
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

        repo_obj, github_repo_created, tracking_created = _client.ensure_github_repo(
            session,
            client,
            account.id,
            owner,
            repo_name,
            description=description,
            create_if_missing=create_repo,
            private=private,
        )
        if not repo_obj:
            if create_repo:
                raise RepoCreationFailedError(repo_path)
            raise RepoNotFoundOnGithubError(repo_path)

    return client, repo_obj, github_repo_created, tracking_created, owner, repo_name


def create_repo_project(
    session: Any,
    user: Any,
    teams: list[Team],
    repo_path: str,
    description: str | None,
    state: str,
    parent_id: int | None,
    title_override: str | None,
    owner_id: int | None,
    due_on: Any | None,
    doc_url: str | None,
    create_repo: bool = False,
    private: bool = True,
) -> ProjectCreationResult:
    """Create a project at the repo level (linked to a GitHub repo, no milestone).

    Optionally creates the repo on GitHub if it doesn't exist.

    Raises:
        InvalidRepoPathError, GithubClientUnavailableError, RepoCreationFailedError,
        RepoNotFoundOnGithubError, ProjectIdGenerationError.
    """
    logger.info(f"Creating repo-level project: {repo_path}")

    client, repo_obj, github_repo_created, tracking_created, owner, repo_name = (
        _ensure_repo_for_create(
            session, user, repo_path, description, create_repo, private
        )
    )

    # Check if project already exists for this repo (without milestone)
    existing_project = (
        session.query(Project)
        .filter(Project.repo_id == repo_obj.id, Project.number.is_(None))
        .first()
    )
    if existing_project:
        # Update teams if provided
        existing_project.teams = teams  # type: ignore[assignment]
        if parent_id is not None:
            existing_project.parent_id = parent_id
        if description is not None:
            existing_project.description = description
        session.commit()
        session.refresh(existing_project)
        return ProjectCreationResult(
            project=existing_project,
            created=False,
            github_repo_created=github_repo_created,
            tracking_created=tracking_created,
        )

    # Inbound sync: add existing repo teams to project (for existing repos)
    teams_to_add, inbound_teams = get_inbound_teams(
        session, client, owner, repo_name, teams, github_repo_created
    )
    all_teams = list(teams) + teams_to_add

    project = create_project_with_retry(
        session,
        all_teams,
        repo_id=repo_obj.id,
        github_id=repo_obj.github_id,
        number=None,
        title=title_override or repo_name,
        description=description,
        state=state,
        parent_id=parent_id,
        owner_id=owner_id,
        due_on=due_on,
        doc_url=doc_url,
    )

    session.commit()
    session.refresh(project)

    # Outbound sync: grant teams access to repo on GitHub (after commit)
    sync_result = perform_outbound_sync(client, owner, repo_name, all_teams)

    return ProjectCreationResult(
        project=project,
        created=True,
        github_repo_created=github_repo_created,
        tracking_created=tracking_created,
        sync_result=sync_result,
        inbound_teams=inbound_teams,
    )


def create_milestone_project(
    session: Any,
    user: Any,
    teams: list[Team],
    repo_path: str,
    milestone_title: str,
    description: str | None,
    parent_id: int | None,
    title_override: str | None,
    owner_id: int | None,
    due_on: Any | None,
    doc_url: str | None,
    create_repo: bool = False,
    private: bool = True,
) -> ProjectCreationResult:
    """Create a project backed by a GitHub milestone.

    The milestone is created if it doesn't exist.
    Optionally creates the repo on GitHub if create_repo=True.

    Raises:
        InvalidRepoPathError, GithubClientUnavailableError, RepoCreationFailedError,
        RepoNotFoundOnGithubError, MilestoneCreationFailedError,
        ProjectIdGenerationError.
    """
    logger.info(f"Creating milestone-level project: {repo_path} / {milestone_title}")

    client, repo_obj, github_repo_created, tracking_created, owner, repo_name = (
        _ensure_repo_for_create(
            session, user, repo_path, description, create_repo, private
        )
    )

    # Ensure milestone exists (create if needed)
    milestone_data, was_created = client.ensure_milestone(
        owner, repo_name, milestone_title, description=description
    )
    if not milestone_data:
        raise MilestoneCreationFailedError(milestone_title)

    # Auto-parent to repo project if no parent specified
    if parent_id is None:
        repo_project = (
            session.query(Project)
            .filter(Project.repo_id == repo_obj.id, Project.number.is_(None))
            .first()
        )
        if repo_project:
            parent_id = repo_project.id

    # Check if project already exists for this milestone
    existing_project = (
        session.query(Project)
        .filter(
            Project.repo_id == repo_obj.id, Project.number == milestone_data["number"]
        )
        .first()
    )
    if existing_project:
        # Update teams if provided
        existing_project.teams = teams  # type: ignore[assignment]
        if parent_id is not None:
            existing_project.parent_id = parent_id
        session.commit()
        session.refresh(existing_project)
        return ProjectCreationResult(
            project=existing_project,
            created=False,
            github_repo_created=github_repo_created,
            tracking_created=tracking_created,
            milestone_created=False,
        )

    # Inbound sync: add existing repo teams to project (for existing repos)
    teams_to_add, inbound_teams = get_inbound_teams(
        session, client, owner, repo_name, teams, github_repo_created
    )
    all_teams = list(teams) + teams_to_add

    # Use provided due_on if set, otherwise use milestone's due_on from GitHub
    effective_due_on = due_on if due_on is not None else milestone_data.get("due_on")

    project = create_project_with_retry(
        session,
        all_teams,
        repo_id=repo_obj.id,
        github_id=milestone_data["github_id"],
        number=milestone_data["number"],
        title=title_override or milestone_data["title"],
        description=milestone_data.get("description") or description,
        state=milestone_data.get("state", "open"),
        due_on=effective_due_on,
        parent_id=parent_id,
        owner_id=owner_id,
        doc_url=doc_url,
    )

    session.commit()
    session.refresh(project)

    # Outbound sync: grant teams access to repo on GitHub (after commit)
    sync_result = perform_outbound_sync(client, owner, repo_name, all_teams)

    return ProjectCreationResult(
        project=project,
        created=True,
        github_repo_created=github_repo_created,
        tracking_created=tracking_created,
        milestone_created=was_created,
        sync_result=sync_result,
        inbound_teams=inbound_teams,
    )
