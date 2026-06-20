"""GitHub team management mixin."""

from __future__ import annotations
import logging
from typing import Any, Generator, TYPE_CHECKING

import requests

from .errors import describe_github_error, is_already_member_error, parse_json_body
from .types import (
    GITHUB_API_URL,
    GithubTeamData,
    GithubTeamMember,
    parse_github_date,
)

if TYPE_CHECKING:
    from .core import GithubClientCore

logger = logging.getLogger(__name__)


class TeamsMixin(GithubClientCore if TYPE_CHECKING else object):
    """Mixin providing GitHub team management methods."""

    # GraphQL fragment for team data
    _TEAM_FRAGMENT = """
        id
        databaseId
        slug
        name
        description
        privacy
        parentTeam { slug }
        members { totalCount }
        repositories { totalCount }
        createdAt
        updatedAt
    """

    def list_teams(
        self,
        org: str,
        per_page: int = 100,
    ) -> Generator[GithubTeamData, None, None]:
        """List all teams in an organization using GraphQL.

        Args:
            org: Organization login name
            per_page: Number of teams per API request (max 100)

        Yields:
            GithubTeamData for each team
        """
        query = f"""
        query($org: String!, $cursor: String) {{
          organization(login: $org) {{
            teams(first: 100, after: $cursor) {{
              pageInfo {{
                hasNextPage
                endCursor
              }}
              nodes {{
                {self._TEAM_FRAGMENT}
              }}
            }}
          }}
        }}
        """

        cursor = None
        while True:
            data, errors = self._graphql(
                query,
                {"org": org, "cursor": cursor},
                operation_name=f"list_teams({org})",
            )

            if errors:
                logger.warning(f"Error listing teams for {org}: {errors}")
                return

            if data is None:
                return

            org_data = data.get("organization")
            if not org_data:
                logger.warning(f"Organization '{org}' not found")
                return

            teams_data = org_data.get("teams", {})
            teams = teams_data.get("nodes", [])

            for team in teams:
                yield GithubTeamData(
                    node_id=team["id"],
                    github_id=team["databaseId"],
                    slug=team["slug"],
                    name=team["name"],
                    description=team.get("description"),
                    privacy=team.get("privacy", "VISIBLE").lower(),
                    permission=None,  # Not available in GraphQL teams query
                    org_login=org,
                    parent_team_slug=(
                        team["parentTeam"]["slug"] if team.get("parentTeam") else None
                    ),
                    members_count=team.get("members", {}).get("totalCount", 0),
                    repos_count=team.get("repositories", {}).get("totalCount", 0),
                    github_created_at=parse_github_date(team.get("createdAt")),
                    github_updated_at=parse_github_date(team.get("updatedAt")),
                )

            page_info = teams_data.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

    def fetch_team(
        self,
        org: str,
        team_slug: str,
    ) -> GithubTeamData | None:
        """Fetch a single team by org and slug using GraphQL.

        Args:
            org: Organization login name
            team_slug: Team slug (URL-safe name)

        Returns:
            GithubTeamData or None if not found
        """
        query = f"""
        query($org: String!, $teamSlug: String!) {{
          organization(login: $org) {{
            team(slug: $teamSlug) {{
              {self._TEAM_FRAGMENT}
            }}
          }}
        }}
        """

        data, errors = self._graphql(
            query,
            {"org": org, "teamSlug": team_slug},
            operation_name=f"fetch_team({org}/{team_slug})",
        )

        if errors:
            logger.warning(f"Error fetching team {org}/{team_slug}: {errors}")
            return None

        if data is None:
            return None

        org_data = data.get("organization")
        if not org_data:
            logger.warning(f"Organization '{org}' not found")
            return None

        team = org_data.get("team")
        if not team:
            return None

        return GithubTeamData(
            node_id=team["id"],
            github_id=team["databaseId"],
            slug=team["slug"],
            name=team["name"],
            description=team.get("description"),
            privacy=team.get("privacy", "VISIBLE").lower(),
            permission=None,  # Not available in GraphQL teams query
            org_login=org,
            parent_team_slug=(
                team["parentTeam"]["slug"] if team.get("parentTeam") else None
            ),
            members_count=team.get("members", {}).get("totalCount", 0),
            repos_count=team.get("repositories", {}).get("totalCount", 0),
            github_created_at=parse_github_date(team.get("createdAt")),
            github_updated_at=parse_github_date(team.get("updatedAt")),
        )

    def get_team_members(
        self,
        org: str,
        team_slug: str,
        role: str = "all",
    ) -> list[GithubTeamMember]:
        """Get members of a team using GraphQL.

        Args:
            org: Organization login name
            team_slug: Team slug
            role: Filter by role: 'member', 'maintainer', or 'all'

        Returns:
            List of team members
        """
        query = """
        query($org: String!, $teamSlug: String!, $cursor: String) {
          organization(login: $org) {
            team(slug: $teamSlug) {
              members(first: 100, after: $cursor) {
                pageInfo {
                  hasNextPage
                  endCursor
                }
                edges {
                  role
                  node {
                    login
                    databaseId
                    id
                  }
                }
              }
            }
          }
        }
        """

        members: list[GithubTeamMember] = []
        cursor = None

        while True:
            data, errors = self._graphql(
                query,
                {"org": org, "teamSlug": team_slug, "cursor": cursor},
                operation_name=f"get_team_members({org}/{team_slug})",
            )

            if errors:
                logger.warning(
                    f"Error fetching team members for {org}/{team_slug}: {errors}"
                )
                return []

            if data is None:
                return []

            org_data = data.get("organization")
            if not org_data:
                logger.warning(f"Organization '{org}' not found")
                return []

            team = org_data.get("team")
            if not team:
                logger.warning(f"Team {org}/{team_slug} not found")
                return []

            members_data = team.get("members", {})
            edges = members_data.get("edges", [])

            for edge in edges:
                member_role = edge.get("role", "MEMBER").lower()
                # Filter by role if specified
                if role != "all" and member_role != role.lower():
                    continue

                node = edge.get("node", {})
                members.append(
                    GithubTeamMember(
                        login=node["login"],
                        id=node["databaseId"],
                        node_id=node["id"],
                        role=member_role,
                    )
                )

            page_info = members_data.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        return members

    def check_org_membership(self, org: str, username: str) -> str | None:
        """Check if a user is a member of an organization.

        Args:
            org: Organization login name
            username: GitHub username to check

        Returns:
            Membership state: 'active' or 'pending'; None only when GitHub
            confirms the user is not a member (404).

        Raises:
            requests.RequestException: if membership could not be determined
            (network error or non-404 HTTP status). Callers must not treat a
            failure here as "not a member".
        """
        response = self.session.get(
            f"{GITHUB_API_URL}/orgs/{org}/memberships/{username}",
            timeout=30,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json().get("state")

    def invite_to_org(
        self,
        org: str,
        username: str,
        role: str = "direct_member",
        team_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Invite a user to an organization.

        Args:
            org: Organization login name
            username: GitHub username to invite
            role: Role in org: 'admin', 'direct_member', 'billing_manager'
            team_ids: Optional list of team IDs to add user to upon acceptance

        Returns:
            Invitation data on success, or a dict with an ``error`` key
            describing the failure (e.g. a missing OAuth scope).
        """
        # First get the user's ID
        try:
            user_response = self.session.get(
                f"{GITHUB_API_URL}/users/{username}",
                timeout=30,
            )
        except requests.RequestException as e:
            logger.warning(f"Failed to look up {username}: {e}")
            return {"error": f"Looking up GitHub user '{username}' failed: {e}"}

        if user_response.status_code == 404:
            logger.warning(f"User '{username}' not found")
            return {"error": f"GitHub user '{username}' not found"}
        if not user_response.ok:
            message = describe_github_error(
                user_response, f"Looking up GitHub user '{username}'"
            )
            logger.warning(message)
            return {"error": message, "status_code": user_response.status_code}
        invitee_id = parse_json_body(user_response).get("id")
        if invitee_id is None:
            return {"error": f"GitHub user lookup for '{username}' returned no id"}

        # Create invitation
        payload: dict[str, Any] = {
            "invitee_id": invitee_id,
            "role": role,
        }
        if team_ids:
            payload["team_ids"] = team_ids

        try:
            response = self.session.post(
                f"{GITHUB_API_URL}/orgs/{org}/invitations",
                json=payload,
                timeout=30,
            )
        except requests.RequestException as e:
            logger.warning(f"Failed to invite {username} to {org}: {e}")
            return {"error": f"Inviting '{username}' to {org} failed: {e}"}

        if response.status_code == 422:
            # 422 covers both "already a member / already invited" (a no-op
            # success) and genuine rejections like "over invitation rate
            # limit" — distinguish them rather than reporting every 422 as a
            # successful invite.
            error_data = parse_json_body(response)
            logger.info(f"Invitation issue for {username} to {org}: {error_data}")
            if is_already_member_error(error_data):
                return {"status": "already_invited_or_member", "details": error_data}
            detail = error_data.get("message") or "validation failed"
            message = f"Inviting '{username}' to {org} failed: {detail} (HTTP 422)."
            logger.warning(message)
            return {"error": message, "status_code": 422, "details": error_data}
        if not response.ok:
            message = describe_github_error(response, f"Inviting '{username}' to {org}")
            logger.warning(message)
            return {"error": message, "status_code": response.status_code}
        self._handle_rate_limit(response)
        return parse_json_body(response)

    def add_team_member(
        self,
        org: str,
        team_slug: str,
        username: str,
        role: str = "member",
    ) -> dict[str, Any]:
        """Add a user to a team.

        If the user is not in the organization, they will be invited first.

        Args:
            org: Organization login name
            team_slug: Team slug
            username: GitHub username to add
            role: Team role: 'member' or 'maintainer'

        Returns:
            Dict with 'success', 'action' ('added', 'invited', 'already_member'),
            and optional 'invitation' data
        """
        # Check current org membership. A failure here means "unknown", not
        # "not a member" — don't fall through to inviting someone who may
        # already be in the org.
        try:
            membership_state = self.check_org_membership(org, username)
        except requests.RequestException as e:
            action = f"Checking org membership for '{username}' in {org}"
            response = getattr(e, "response", None)
            error = (
                describe_github_error(response, action)
                if response is not None
                else f"{action} failed: {e}"
            )
            return {
                "success": False,
                "action": None,
                "org_membership": None,
                "error": error,
            }

        result: dict[str, Any] = {
            "success": False,
            "action": None,
            "org_membership": membership_state,
        }

        # If not in org, invite them (with this team)
        if membership_state is None:
            # Get team ID for the invitation
            team = self.fetch_team(org, team_slug)
            if not team:
                result["error"] = f"Team {org}/{team_slug} not found"
                return result

            invitation = self.invite_to_org(
                org, username, role="direct_member", team_ids=[team["github_id"]]
            )
            if invitation and not invitation.get("error"):
                result["success"] = True
                result["action"] = "invited"
                result["invitation"] = invitation
                return result
            result["error"] = (invitation or {}).get(
                "error", "Failed to send org invitation"
            )
            return result

        # If pending invitation, we can still try to add to team
        if membership_state == "pending":
            result["note"] = "User has pending org invitation"

        # Add to team
        try:
            response = self.session.put(
                f"{GITHUB_API_URL}/orgs/{org}/teams/{team_slug}/memberships/{username}",
                json={"role": role},
                timeout=30,
            )
        except requests.RequestException as e:
            result["error"] = f"Adding '{username}' to {org}/{team_slug} failed: {e}"
            return result

        if response.status_code == 404:
            result["error"] = f"Team {org}/{team_slug} not found"
            return result
        if not response.ok:
            result["error"] = describe_github_error(
                response, f"Adding '{username}' to {org}/{team_slug}"
            )
            return result
        self._handle_rate_limit(response)

        membership_data = response.json()
        state = membership_data.get("state", "unknown")

        if state == "active":
            result["success"] = True
            result["action"] = "added"
        elif state == "pending":
            result["success"] = True
            result["action"] = "pending"
            result["note"] = "User must accept team invitation"

        result["membership"] = membership_data
        return result

    def remove_team_member(
        self,
        org: str,
        team_slug: str,
        username: str,
    ) -> bool:
        """Remove a user from a team.

        Args:
            org: Organization login name
            team_slug: Team slug
            username: GitHub username to remove

        Returns:
            True if removed successfully, False otherwise
        """
        try:
            response = self.session.delete(
                f"{GITHUB_API_URL}/orgs/{org}/teams/{team_slug}/memberships/{username}",
                timeout=30,
            )
            if response.status_code == 204:
                return True
            if response.status_code == 404:
                logger.info(f"{username} not in team {org}/{team_slug}")
                return True  # Not in team is success for removal
            response.raise_for_status()
            return True
        except Exception as e:
            logger.warning(f"Failed to remove {username} from {org}/{team_slug}: {e}")
            return False

    def get_repo_teams(
        self,
        owner: str,
        repo: str,
    ) -> list[dict[str, Any]]:
        """Get teams with access to a repository.

        Uses REST API: GET /repos/{owner}/{repo}/teams

        Args:
            owner: Repository owner (user or org)
            repo: Repository name

        Returns:
            List of team dicts with keys: slug, name, permission, etc.
            Empty list if repo not found or no teams have access.
        """
        teams: list[dict[str, Any]] = []
        page = 1
        per_page = 100

        while True:
            try:
                response = self.session.get(
                    f"{GITHUB_API_URL}/repos/{owner}/{repo}/teams",
                    params={"page": page, "per_page": per_page},
                    timeout=30,
                )
                if response.status_code == 404:
                    logger.warning(f"Repository {owner}/{repo} not found")
                    return []
                response.raise_for_status()
                self._handle_rate_limit(response)

                page_teams = response.json()
                if not page_teams:
                    break

                for team in page_teams:
                    teams.append({
                        "id": team.get("id"),
                        "node_id": team.get("node_id"),
                        "slug": team.get("slug"),
                        "name": team.get("name"),
                        "description": team.get("description"),
                        "permission": team.get("permission"),
                        "privacy": team.get("privacy"),
                    })

                if len(page_teams) < per_page:
                    break
                page += 1

            except Exception as e:
                logger.warning(
                    f"Failed to get teams for {owner}/{repo} (page {page}): "
                    f"{type(e).__name__}: {e}"
                )
                # Return partial results on pagination failure. Callers should be aware
                # that on multi-page results, partial data may be returned if pagination
                # fails mid-way. Check logs for pagination failure warnings if results
                # seem incomplete.
                return teams

        return teams

    def add_team_to_repo(
        self,
        org: str,
        team_slug: str,
        owner: str,
        repo: str,
        permission: str = "push",
    ) -> bool:
        """Grant a team access to a repository.

        Uses REST API: PUT /orgs/{org}/teams/{team_slug}/repos/{owner}/{repo}

        Args:
            org: Organization login name (must match team's org)
            team_slug: Team slug (URL-safe name)
            owner: Repository owner
            repo: Repository name
            permission: Access level - "pull", "triage", "push", "maintain", "admin"

        Returns:
            True if access was granted, False on failure
        """
        try:
            response = self.session.put(
                f"{GITHUB_API_URL}/orgs/{org}/teams/{team_slug}/repos/{owner}/{repo}",
                json={"permission": permission},
                timeout=30,
            )
            # Track rate limits before checking status to ensure we always update
            self._handle_rate_limit(response)
            if response.status_code == 204:
                logger.info(f"Granted {team_slug} {permission} access to {owner}/{repo}")
                return True
            if response.status_code == 404:
                logger.warning(
                    f"Team {org}/{team_slug} or repo {owner}/{repo} not found "
                    f"(or insufficient permissions to grant access)"
                )
                return False
            response.raise_for_status()
            return True
        except Exception as e:
            logger.warning(
                f"Failed to grant {team_slug} access to {owner}/{repo}: {e}"
            )
            return False

    def remove_team_from_repo(
        self,
        org: str,
        team_slug: str,
        owner: str,
        repo: str,
    ) -> bool:
        """Revoke a team's access to a repository.

        Uses REST API: DELETE /orgs/{org}/teams/{team_slug}/repos/{owner}/{repo}

        Note: This method is provided for completeness but is not currently used
        by the project sync logic. It will be used when implementing team removal
        from projects (i.e., when teams are removed from a project, their GitHub
        repo access should also be revoked).

        Args:
            org: Organization login name
            team_slug: Team slug
            owner: Repository owner
            repo: Repository name

        Returns:
            True if access was revoked (or team didn't have access), False on error
        """
        try:
            response = self.session.delete(
                f"{GITHUB_API_URL}/orgs/{org}/teams/{team_slug}/repos/{owner}/{repo}",
                timeout=30,
            )
            # Track rate limits before checking status to ensure we always update
            self._handle_rate_limit(response)
            if response.status_code == 204:
                logger.info(f"Revoked {team_slug} access to {owner}/{repo}")
                return True
            if response.status_code == 404:
                # Team or repo not found, or team didn't have access - still a success
                logger.info(f"{team_slug} did not have access to {owner}/{repo}")
                return True
            response.raise_for_status()
            return True
        except Exception as e:
            logger.warning(
                f"Failed to revoke {team_slug} access to {owner}/{repo}: {e}"
            )
            return False

    def create_team(
        self,
        org: str,
        name: str,
        description: str | None = None,
        privacy: str = "closed",
    ) -> dict[str, Any] | None:
        """Create a new team in a GitHub organization.

        Uses REST API: POST /orgs/{org}/teams

        Args:
            org: Organization login name
            name: Team name (will be slugified for the URL-safe slug)
            description: Optional team description
            privacy: "closed" (visible to org members) or "secret" (only to team members)

        Returns:
            Team data dict with keys: id, slug, name, description, privacy
            or None if creation failed
        """
        payload: dict[str, Any] = {
            "name": name,
            "privacy": privacy,
        }
        if description:
            payload["description"] = description

        try:
            response = self.session.post(
                f"{GITHUB_API_URL}/orgs/{org}/teams",
                json=payload,
                timeout=30,
            )
            if response.status_code == 422:
                # Team may already exist
                error_data = response.json()
                logger.info(f"Team creation issue for {org}/{name}: {error_data}")
                return None
            response.raise_for_status()
            self._handle_rate_limit(response)

            data = response.json()
            return {
                "id": data["id"],
                "node_id": data.get("node_id"),
                "slug": data["slug"],
                "name": data["name"],
                "description": data.get("description"),
                "privacy": data.get("privacy"),
            }
        except Exception as e:
            logger.warning(f"Failed to create team {name} in {org}: {e}")
            return None
