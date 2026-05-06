"""GithubClient factory and repo-tracking helpers.

These helpers are MCP-agnostic: they look up GitHub credentials in the
database and return an authenticated `GithubClient`, optionally ensuring a
`GithubRepo` tracking row exists.
"""

import logging
from typing import Any

from memory.common.db.models import GithubTeam
from memory.common.db.models.sources import GithubAccount, GithubRepo
from memory.common.github import GithubClient, GithubCredentials


logger = logging.getLogger(__name__)


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


def ensure_github_repo(
    session: Any,
    client: GithubClient,
    account_id: int,
    owner: str,
    repo_name: str,
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
        description: Optional description (used if creating on GitHub)
        create_if_missing: If True, creates the repo on GitHub if it doesn't exist
        private: Whether to create as private (default: True)

    Returns:
        Tuple of (GithubRepo, repo_was_created_on_github, tracking_entry_was_created)
        Returns (None, False, False) if the repo doesn't exist and create_if_missing=False
    """
    # Check if we already have a tracking entry (globally, not per-account)
    # A repo should only be tracked once - access is controlled at project level
    existing_repo = (
        session.query(GithubRepo)
        .filter(
            GithubRepo.owner.ilike(owner),
            GithubRepo.name.ilike(repo_name),
        )
        .first()
    )
    if existing_repo:
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
            logger.error(f"Failed to create repository '{owner}/{repo_name}' on GitHub")
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
