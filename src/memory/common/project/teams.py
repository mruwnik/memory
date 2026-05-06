"""Team-repo sync helpers (GitHub side <-> local Team rows)."""

import logging
from typing import TypedDict

from sqlalchemy.orm import Session

from memory.common.db.models import Team
from memory.common.github import GithubClient


logger = logging.getLogger(__name__)


class SyncResult(TypedDict):
    """Result of a team-repo sync operation."""

    synced: list[str]
    skipped: list[str]
    failed: list[str]


VALID_GITHUB_PERMISSIONS = {"pull", "triage", "push", "maintain", "admin"}


def sync_repo_teams_outbound(
    client: GithubClient,
    repo_owner: str,
    repo_name: str,
    teams: list[Team],
    permission: str = "push",
) -> SyncResult:
    """Grant GitHub repo access to teams with github_team_id.

    For each team that has GitHub integration configured (github_team_id,
    github_team_slug, github_org), grants that GitHub team access to the
    specified repository.

    Args:
        client: Authenticated GitHub client
        repo_owner: Repository owner
        repo_name: Repository name
        teams: List of Team models to sync
        permission: GitHub permission level ("pull", "triage", "push", "maintain", "admin")

    Returns:
        SyncResult with:
        - synced: list of team slugs that were successfully synced
        - skipped: list of team names that were skipped (no GitHub integration)
        - failed: list of team slugs that failed to sync

    Raises:
        ValueError: If permission is not a valid GitHub permission level
    """
    if permission not in VALID_GITHUB_PERMISSIONS:
        raise ValueError(
            f"Invalid permission '{permission}'. Must be one of: {VALID_GITHUB_PERMISSIONS}"
        )

    synced: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []

    for team in teams:
        # Skip teams without GitHub integration
        if not team.github_team_id or not team.github_team_slug or not team.github_org:
            skipped.append(team.name)
            continue

        # Skip if team is in a different org than the repo owner
        # (can't grant cross-org access)
        if team.github_org.lower() != repo_owner.lower():
            logger.info(
                f"Skipping team {team.github_team_slug} (org '{team.github_org.lower()}') "
                f"- repo owner is '{repo_owner.lower()}'"
            )
            skipped.append(team.name)
            continue

        success = client.add_team_to_repo(
            org=team.github_org,
            team_slug=team.github_team_slug,
            owner=repo_owner,
            repo=repo_name,
            permission=permission,
        )
        if success:
            synced.append(team.github_team_slug)
        else:
            failed.append(team.github_team_slug)

    return {
        "synced": synced,
        "skipped": skipped,
        "failed": failed,
    }


def sync_repo_teams_inbound(
    session: Session,
    client: GithubClient,
    repo_owner: str,
    repo_name: str,
) -> list[Team]:
    """Fetch repo teams from GitHub and return matching Team records.

    Queries GitHub for all teams that have access to the repository,
    then finds matching Team records in our database by github_team_id.

    Args:
        session: Database session
        client: Authenticated GitHub client
        repo_owner: Repository owner
        repo_name: Repository name

    Returns:
        List of Team records that match GitHub teams with repo access.
        Teams are matched by github_team_id. Returns empty list on API errors.

    Note:
        If get_repo_teams encounters a pagination failure, it may return
        partial results. This function will process whatever teams are
        returned, which may be incomplete. Check logs for warnings about
        pagination failures if results seem incomplete.
    """
    # Fetch teams from GitHub
    try:
        github_teams = client.get_repo_teams(repo_owner, repo_name)
    except Exception as e:
        logger.warning(
            f"Failed to fetch GitHub teams for {repo_owner}/{repo_name}: "
            f"{type(e).__name__}: {e}"
        )
        return []
    if not github_teams:
        return []

    # Extract GitHub team IDs
    github_team_ids = [t["id"] for t in github_teams if t.get("id")]
    if not github_team_ids:
        return []

    # Find matching Team records
    matching_teams = (
        session.query(Team)
        .filter(Team.github_team_id.in_(github_team_ids))
        .all()
    )

    if matching_teams:
        logger.info(
            f"Found {len(matching_teams)} matching teams for {repo_owner}/{repo_name}: "
            f"{[t.name for t in matching_teams]}"
        )

    return matching_teams
