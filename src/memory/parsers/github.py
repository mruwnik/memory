"""GitHub API client for fetching issues, PRs, comments, and project fields."""

import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generator, TypedDict

import requests

logger = logging.getLogger(__name__)

# GitHub REST API base URL
GITHUB_API_URL = "https://api.github.com"
GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"

# Rate limit handling
RATE_LIMIT_REMAINING_HEADER = "X-RateLimit-Remaining"
RATE_LIMIT_RESET_HEADER = "X-RateLimit-Reset"
MIN_RATE_LIMIT_REMAINING = 10


@dataclass
class GithubCredentials:
    """Credentials for GitHub API access."""

    auth_type: str  # 'pat' or 'app'
    access_token: str | None = None
    app_id: int | None = None
    installation_id: int | None = None
    private_key: str | None = None


class GithubComment(TypedDict):
    """A comment on an issue or PR."""

    id: int
    author: str
    body: str
    created_at: str
    updated_at: str


class GithubIssueData(TypedDict):
    """Parsed issue/PR data ready for storage."""

    kind: str  # 'issue' or 'pr'
    number: int
    title: str
    body: str
    state: str
    author: str
    labels: list[str]
    assignees: list[str]
    milestone: str | None
    created_at: datetime
    closed_at: datetime | None
    merged_at: datetime | None  # PRs only
    github_updated_at: datetime
    comment_count: int
    comments: list[GithubComment]
    diff_summary: str | None  # PRs only
    project_fields: dict[str, Any] | None
    content_hash: str


def parse_github_date(date_str: str | None) -> datetime | None:
    """Parse ISO date string from GitHub API to datetime."""
    if not date_str:
        return None
    # GitHub uses ISO format with Z suffix
    return datetime.fromisoformat(date_str.replace("Z", "+00:00"))


def compute_content_hash(body: str, comments: list[GithubComment]) -> str:
    """Compute SHA256 hash of issue/PR content for change detection."""
    content_parts = [body or ""]
    for comment in comments:
        content_parts.append(comment["body"])
    return hashlib.sha256("\n".join(content_parts).encode()).hexdigest()


class GithubClient:
    """Client for GitHub REST and GraphQL APIs."""

    def __init__(self, credentials: GithubCredentials):
        self.credentials = credentials
        self.session = requests.Session()
        self._setup_auth()

    def _setup_auth(self) -> None:
        if self.credentials.auth_type == "pat":
            self.session.headers["Authorization"] = (
                f"Bearer {self.credentials.access_token}"
            )
        elif self.credentials.auth_type == "app":
            # Generate JWT and get installation token
            token = self._get_installation_token()
            self.session.headers["Authorization"] = f"Bearer {token}"

        self.session.headers["Accept"] = "application/vnd.github+json"
        self.session.headers["X-GitHub-Api-Version"] = "2022-11-28"
        self.session.headers["User-Agent"] = "memory-kb-github-sync"

    def _get_installation_token(self) -> str:
        """Get installation access token for GitHub App."""
        try:
            import jwt
        except ImportError:
            raise ImportError("PyJWT is required for GitHub App authentication")

        if not self.credentials.app_id or not self.credentials.private_key:
            raise ValueError("app_id and private_key required for app auth")
        if not self.credentials.installation_id:
            raise ValueError("installation_id required for app auth")

        now = int(time.time())
        payload = {
            "iat": now - 60,
            "exp": now + 600,
            "iss": self.credentials.app_id,
        }
        jwt_token = jwt.encode(
            payload, self.credentials.private_key, algorithm="RS256"
        )

        response = requests.post(
            f"{GITHUB_API_URL}/app/installations/{self.credentials.installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {jwt_token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()["token"]

    def _handle_rate_limit(self, response: requests.Response) -> None:
        """Check rate limits and sleep if necessary."""
        remaining = int(response.headers.get(RATE_LIMIT_REMAINING_HEADER, 100))
        if remaining < MIN_RATE_LIMIT_REMAINING:
            reset_time = int(response.headers.get(RATE_LIMIT_RESET_HEADER, 0))
            sleep_time = max(reset_time - time.time(), 0) + 1
            logger.warning(f"Rate limit low ({remaining}), sleeping for {sleep_time}s")
            time.sleep(sleep_time)

    def fetch_issues(
        self,
        owner: str,
        repo: str,
        since: datetime | None = None,
        state: str = "all",
        labels: list[str] | None = None,
    ) -> Generator[GithubIssueData, None, None]:
        """Fetch issues from a repository with pagination."""
        params: dict[str, Any] = {
            "state": state,
            "sort": "updated",
            "direction": "desc",
            "per_page": 100,
        }
        if since:
            params["since"] = since.isoformat()
        if labels:
            params["labels"] = ",".join(labels)

        page = 1
        while True:
            params["page"] = page
            response = self.session.get(
                f"{GITHUB_API_URL}/repos/{owner}/{repo}/issues",
                params=params,
                timeout=30,
            )
            response.raise_for_status()
            self._handle_rate_limit(response)

            issues = response.json()
            if not issues:
                break

            for issue in issues:
                # Skip PRs (they're included in issues endpoint)
                if "pull_request" in issue:
                    continue

                yield self._parse_issue(owner, repo, issue)

            page += 1

    def fetch_prs(
        self,
        owner: str,
        repo: str,
        since: datetime | None = None,
        state: str = "all",
    ) -> Generator[GithubIssueData, None, None]:
        """Fetch pull requests from a repository with pagination."""
        params: dict[str, Any] = {
            "state": state,
            "sort": "updated",
            "direction": "desc",
            "per_page": 100,
        }

        page = 1
        while True:
            params["page"] = page
            response = self.session.get(
                f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls",
                params=params,
                timeout=30,
            )
            response.raise_for_status()
            self._handle_rate_limit(response)

            prs = response.json()
            if not prs:
                break

            for pr in prs:
                updated_at = parse_github_date(pr["updated_at"])
                if since and updated_at and updated_at < since:
                    return  # Stop if we've gone past our since date

                yield self._parse_pr(owner, repo, pr)

            page += 1

    def fetch_comments(
        self,
        owner: str,
        repo: str,
        issue_number: int,
    ) -> list[GithubComment]:
        """Fetch all comments for an issue/PR."""
        comments: list[GithubComment] = []
        page = 1

        while True:
            response = self.session.get(
                f"{GITHUB_API_URL}/repos/{owner}/{repo}/issues/{issue_number}/comments",
                params={"page": page, "per_page": 100},
                timeout=30,
            )
            response.raise_for_status()
            self._handle_rate_limit(response)

            page_comments = response.json()
            if not page_comments:
                break

            comments.extend(
                [
                    GithubComment(
                        id=c["id"],
                        author=c["user"]["login"] if c.get("user") else "ghost",
                        body=c.get("body", ""),
                        created_at=c["created_at"],
                        updated_at=c["updated_at"],
                    )
                    for c in page_comments
                ]
            )
            page += 1

        return comments

    def fetch_project_fields(
        self,
        owner: str,
        repo: str,
        issue_number: int,
    ) -> dict[str, Any] | None:
        """Fetch GitHub Projects v2 field values using GraphQL."""
        query = """
        query($owner: String!, $repo: String!, $number: Int!) {
          repository(owner: $owner, name: $repo) {
            issue(number: $number) {
              projectItems(first: 10) {
                nodes {
                  project { title }
                  fieldValues(first: 20) {
                    nodes {
                      ... on ProjectV2ItemFieldTextValue {
                        text
                        field { ... on ProjectV2Field { name } }
                      }
                      ... on ProjectV2ItemFieldNumberValue {
                        number
                        field { ... on ProjectV2Field { name } }
                      }
                      ... on ProjectV2ItemFieldDateValue {
                        date
                        field { ... on ProjectV2Field { name } }
                      }
                      ... on ProjectV2ItemFieldSingleSelectValue {
                        name
                        field { ... on ProjectV2SingleSelectField { name } }
                      }
                      ... on ProjectV2ItemFieldIterationValue {
                        title
                        field { ... on ProjectV2IterationField { name } }
                      }
                    }
                  }
                }
              }
            }
          }
        }
        """

        try:
            response = self.session.post(
                GITHUB_GRAPHQL_URL,
                json={
                    "query": query,
                    "variables": {"owner": owner, "repo": repo, "number": issue_number},
                },
                timeout=30,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            logger.warning(f"Failed to fetch project fields: {e}")
            return None

        data = response.json()
        if "errors" in data:
            logger.warning(f"GraphQL errors: {data['errors']}")
            return None

        # Parse project fields
        issue_data = (
            data.get("data", {}).get("repository", {}).get("issue", {})
        )
        if not issue_data:
            return None

        items = issue_data.get("projectItems", {}).get("nodes", [])
        if not items:
            return None

        fields: dict[str, Any] = {}
        for item in items:
            project_name = item.get("project", {}).get("title", "unknown")
            for field_value in item.get("fieldValues", {}).get("nodes", []):
                field_info = field_value.get("field", {})
                field_name = field_info.get("name") if field_info else None
                if not field_name:
                    continue

                # Extract value based on type
                value = (
                    field_value.get("text")
                    or field_value.get("number")
                    or field_value.get("date")
                    or field_value.get("name")  # Single select
                    or field_value.get("title")  # Iteration
                )

                if value is not None:
                    fields[f"{project_name}.{field_name}"] = value

        return fields if fields else None

    def fetch_pr_project_fields(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> dict[str, Any] | None:
        """Fetch GitHub Projects v2 field values for a PR using GraphQL."""
        query = """
        query($owner: String!, $repo: String!, $number: Int!) {
          repository(owner: $owner, name: $repo) {
            pullRequest(number: $number) {
              projectItems(first: 10) {
                nodes {
                  project { title }
                  fieldValues(first: 20) {
                    nodes {
                      ... on ProjectV2ItemFieldTextValue {
                        text
                        field { ... on ProjectV2Field { name } }
                      }
                      ... on ProjectV2ItemFieldNumberValue {
                        number
                        field { ... on ProjectV2Field { name } }
                      }
                      ... on ProjectV2ItemFieldDateValue {
                        date
                        field { ... on ProjectV2Field { name } }
                      }
                      ... on ProjectV2ItemFieldSingleSelectValue {
                        name
                        field { ... on ProjectV2SingleSelectField { name } }
                      }
                      ... on ProjectV2ItemFieldIterationValue {
                        title
                        field { ... on ProjectV2IterationField { name } }
                      }
                    }
                  }
                }
              }
            }
          }
        }
        """

        try:
            response = self.session.post(
                GITHUB_GRAPHQL_URL,
                json={
                    "query": query,
                    "variables": {"owner": owner, "repo": repo, "number": pr_number},
                },
                timeout=30,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            logger.warning(f"Failed to fetch PR project fields: {e}")
            return None

        data = response.json()
        if "errors" in data:
            logger.warning(f"GraphQL errors: {data['errors']}")
            return None

        # Parse project fields
        pr_data = (
            data.get("data", {}).get("repository", {}).get("pullRequest", {})
        )
        if not pr_data:
            return None

        items = pr_data.get("projectItems", {}).get("nodes", [])
        if not items:
            return None

        fields: dict[str, Any] = {}
        for item in items:
            project_name = item.get("project", {}).get("title", "unknown")
            for field_value in item.get("fieldValues", {}).get("nodes", []):
                field_info = field_value.get("field", {})
                field_name = field_info.get("name") if field_info else None
                if not field_name:
                    continue

                value = (
                    field_value.get("text")
                    or field_value.get("number")
                    or field_value.get("date")
                    or field_value.get("name")
                    or field_value.get("title")
                )

                if value is not None:
                    fields[f"{project_name}.{field_name}"] = value

        return fields if fields else None

    def _parse_issue(
        self, owner: str, repo: str, issue: dict[str, Any]
    ) -> GithubIssueData:
        """Parse raw issue data into structured format."""
        comments = self.fetch_comments(owner, repo, issue["number"])
        body = issue.get("body") or ""

        return GithubIssueData(
            kind="issue",
            number=issue["number"],
            title=issue["title"],
            body=body,
            state=issue["state"],
            author=issue["user"]["login"] if issue.get("user") else "ghost",
            labels=[label["name"] for label in issue.get("labels", [])],
            assignees=[a["login"] for a in issue.get("assignees", [])],
            milestone=(
                issue["milestone"]["title"] if issue.get("milestone") else None
            ),
            created_at=parse_github_date(issue["created_at"]),  # type: ignore
            closed_at=parse_github_date(issue.get("closed_at")),
            merged_at=None,
            github_updated_at=parse_github_date(issue["updated_at"]),  # type: ignore
            comment_count=len(comments),
            comments=comments,
            diff_summary=None,
            project_fields=None,  # Fetched separately if enabled
            content_hash=compute_content_hash(body, comments),
        )

    def _parse_pr(
        self, owner: str, repo: str, pr: dict[str, Any]
    ) -> GithubIssueData:
        """Parse raw PR data into structured format."""
        comments = self.fetch_comments(owner, repo, pr["number"])
        body = pr.get("body") or ""

        # Get diff summary (truncated)
        diff_summary = None
        if diff_url := pr.get("diff_url"):
            try:
                diff_response = self.session.get(diff_url, timeout=30)
                if diff_response.ok:
                    diff_summary = diff_response.text[:5000]  # Truncate large diffs
            except Exception as e:
                logger.warning(f"Failed to fetch diff: {e}")

        return GithubIssueData(
            kind="pr",
            number=pr["number"],
            title=pr["title"],
            body=body,
            state=pr["state"],
            author=pr["user"]["login"] if pr.get("user") else "ghost",
            labels=[label["name"] for label in pr.get("labels", [])],
            assignees=[a["login"] for a in pr.get("assignees", [])],
            milestone=pr["milestone"]["title"] if pr.get("milestone") else None,
            created_at=parse_github_date(pr["created_at"]),  # type: ignore
            closed_at=parse_github_date(pr.get("closed_at")),
            merged_at=parse_github_date(pr.get("merged_at")),
            github_updated_at=parse_github_date(pr["updated_at"]),  # type: ignore
            comment_count=len(comments),
            comments=comments,
            diff_summary=diff_summary,
            project_fields=None,  # Fetched separately if enabled
            content_hash=compute_content_hash(body, comments),
        )
