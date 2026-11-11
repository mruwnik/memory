"""GithubClient factory and repo-tracking helpers.

These helpers are MCP-agnostic: they look up GitHub credentials in the
database and return an authenticated `GithubClient`, optionally ensuring a
`GithubRepo` tracking row exists.
"""

import logging
from typing import Any, cast

from sqlalchemy import func

from memory.common.access_control import UserLike, has_admin_scope
from memory.common.db.models.sources import GithubAccount, GithubRepo
from memory.common.github import GithubClient, GithubCredentials


logger = logging.getLogger(__name__)


def account_credentials(account: GithubAccount) -> GithubCredentials:
    return GithubCredentials(
        auth_type=cast(str, account.auth_type),
        access_token=cast(str | None, account.access_token),
        app_id=cast(int | None, account.app_id),
        installation_id=cast(int | None, account.installation_id),
        private_key=cast(str | None, account.private_key),
    )


def account_client(account: GithubAccount) -> GithubClient:
    return GithubClient(account_credentials(account))


def repo_name_matches(owner: str, name: str) -> Any:
    """SQL condition matching a GithubRepo by exact owner/name, ignoring case.

    GitHub owner and repo names are case-insensitive. Plain equality on the
    lowered values, never LIKE, so ``%`` and ``_`` in user input match only
    themselves.
    """
    return (func.lower(GithubRepo.owner) == owner.lower()) & (
        func.lower(GithubRepo.name) == name.lower()
    )


def can_bind_repo(binder: UserLike, client: GithubClient, repo: GithubRepo) -> bool:
    """Whether ``binder`` may bind ``repo``, tracked by any account, to a project.

    Binding files every item the repo's sync stores under the project, so it
    needs one of: the binder's credential has admin scope, the binder's own GitHub
    account tracks the repo, or ``client`` (the binder's GitHub credentials) can
    read the repo on GitHub.
    """
    if has_admin_scope(binder):
        return True
    if repo.account is not None and repo.account.user_id == binder.id:
        return True
    return client.fetch_repository_info(cast(str, repo.owner), cast(str, repo.name)) is not None


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
        .filter(repo_name_matches(owner, name), GithubAccount.user_id == user_id)
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

        # Return None for repo since we're using a generic account
        # The caller will need to handle this case
        return account_client(account), None

    account = repo.account
    if not account or not account.active:
        raise ValueError(f"No active credentials for {repo_path}")

    return account_client(account), repo


def ensure_github_repo(
    session: Any,
    client: GithubClient,
    account_id: int,
    owner: str,
    repo_name: str,
    binder: UserLike,
    description: str | None = None,
    create_if_missing: bool = False,
    private: bool = True,
) -> tuple[GithubRepo | None, bool, bool]:
    """Ensure a GithubRepo tracking entry exists in the database.

    Optionally creates the repo on GitHub if it doesn't exist.

    Args:
        session: Database session
        client: Authenticated GitHub client
        account_id: ID of the GithubAccount to associate with
        owner: Repository owner (user or org)
        repo_name: Repository name
        binder: The caller, with its credential's scopes (see `can_bind_repo`)
        description: Optional description (used if creating on GitHub)
        create_if_missing: If True, creates the repo on GitHub if it doesn't exist
        private: Whether to create as private (default: True)

    Returns:
        Tuple of (GithubRepo, repo_was_created_on_github, tracking_entry_was_created)
        Returns (None, False, False) if the repo doesn't exist and create_if_missing=False,
        or if another user tracks it and ``binder`` can't bind it
        (see `can_bind_repo`).
    """
    # A repo is tracked once, globally; whoever binds it to a project must
    # show they may see it, since the tracker's sync then files into that project.
    existing_repo = (
        session.query(GithubRepo).filter(repo_name_matches(owner, repo_name)).first()
    )
    if existing_repo:
        if not can_bind_repo(binder, client, existing_repo):
            logger.warning(
                "User %s may not bind GithubRepo %s", binder.id, existing_repo.id
            )
            return None, False, False
        return existing_repo, False, False

    # Check if repo exists on GitHub
    github_repo_data = client.fetch_repository_info(owner, repo_name)
    github_repo_created = False

    if not github_repo_data:
        if not create_if_missing:
            return None, False, False

        # Create the repo on GitHub
        github_repo_data, github_repo_created = client.ensure_repository(
            owner, repo_name, description=description, private=private
        )
        if not github_repo_data:
            logger.error("Failed to create a GitHub repository for account %s", account_id)
            return None, False, False

    # Create tracking entry in our database
    new_repo = GithubRepo(
        account_id=account_id,
        github_id=github_repo_data.get("github_id"),
        owner=github_repo_data.get("owner", owner),
        name=github_repo_data.get("name", repo_name),
        track_issues=True,
        track_prs=True,
        track_comments=True,
        track_project_fields=True,
        active=True,
    )
    session.add(new_repo)
    session.flush()

    logger.info(
        f"Created GithubRepo tracking entry for {owner}/{repo_name} "
        f"(github_created={github_repo_created})"
    )

    return new_repo, github_repo_created, True
