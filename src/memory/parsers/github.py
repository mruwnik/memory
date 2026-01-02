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


class GithubReviewComment(TypedDict):
    """A line-by-line code review comment on a PR."""

    id: int
    user: str
    body: str
    path: str
    line: int | None
    side: str  # "LEFT" or "RIGHT"
    diff_hunk: str
    created_at: str


class GithubReview(TypedDict):
    """A PR review (approval, request changes, etc.)."""

    id: int
    user: str
    state: str  # "approved", "changes_requested", "commented", "dismissed"
    body: str | None
    submitted_at: str


class GithubFileChange(TypedDict):
    """A file changed in a PR."""

    filename: str
    status: str  # "added", "modified", "removed", "renamed"
    additions: int
    deletions: int
    patch: str | None  # Diff patch for this file


class GithubPRDataDict(TypedDict):
    """PR-specific data for storage in GithubPRData model."""

    diff: str | None  # Full diff text
    files: list[GithubFileChange]
    additions: int
    deletions: int
    changed_files_count: int
    reviews: list[GithubReview]
    review_comments: list[GithubReviewComment]


class GithubMilestoneData(TypedDict):
    """Parsed milestone data ready for storage."""

    github_id: int
    number: int
    title: str
    description: str | None
    state: str
    due_on: datetime | None
    github_created_at: datetime
    github_updated_at: datetime
    closed_at: datetime | None


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
    milestone_number: int | None  # For FK lookup during sync
    created_at: datetime
    closed_at: datetime | None
    merged_at: datetime | None  # PRs only
    github_updated_at: datetime
    comment_count: int
    comments: list[GithubComment]
    diff_summary: str | None  # PRs only (truncated, for backward compat)
    project_fields: dict[str, Any] | None
    content_hash: str
    # PR-specific extended data (None for issues)
    pr_data: GithubPRDataDict | None


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

    def fetch_review_comments(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> list[GithubReviewComment]:
        """Fetch all line-by-line review comments for a PR."""
        comments: list[GithubReviewComment] = []
        page = 1

        while True:
            response = self.session.get(
                f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls/{pr_number}/comments",
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
                    GithubReviewComment(
                        id=c["id"],
                        user=c["user"]["login"] if c.get("user") else "ghost",
                        body=c.get("body", ""),
                        path=c.get("path", ""),
                        line=c.get("line"),
                        side=c.get("side", "RIGHT"),
                        diff_hunk=c.get("diff_hunk", ""),
                        created_at=c["created_at"],
                    )
                    for c in page_comments
                ]
            )
            page += 1

        return comments

    def fetch_reviews(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> list[GithubReview]:
        """Fetch all reviews (approvals, change requests) for a PR."""
        reviews: list[GithubReview] = []
        page = 1

        while True:
            response = self.session.get(
                f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
                params={"page": page, "per_page": 100},
                timeout=30,
            )
            response.raise_for_status()
            self._handle_rate_limit(response)

            page_reviews = response.json()
            if not page_reviews:
                break

            reviews.extend(
                [
                    GithubReview(
                        id=r["id"],
                        user=r["user"]["login"] if r.get("user") else "ghost",
                        state=r.get("state", "COMMENTED").lower(),
                        body=r.get("body"),
                        submitted_at=r.get("submitted_at", ""),
                    )
                    for r in page_reviews
                ]
            )
            page += 1

        return reviews

    def fetch_pr_files(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> list[GithubFileChange]:
        """Fetch list of files changed in a PR with patches."""
        files: list[GithubFileChange] = []
        page = 1

        while True:
            response = self.session.get(
                f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls/{pr_number}/files",
                params={"page": page, "per_page": 100},
                timeout=30,
            )
            response.raise_for_status()
            self._handle_rate_limit(response)

            page_files = response.json()
            if not page_files:
                break

            files.extend(
                [
                    GithubFileChange(
                        filename=f["filename"],
                        status=f.get("status", "modified"),
                        additions=f.get("additions", 0),
                        deletions=f.get("deletions", 0),
                        patch=f.get("patch"),  # May be None for binary files
                    )
                    for f in page_files
                ]
            )
            page += 1

        return files

    def fetch_pr_diff(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> str | None:
        """Fetch the full diff for a PR (not truncated)."""
        try:
            response = self.session.get(
                f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls/{pr_number}",
                headers={"Accept": "application/vnd.github.diff"},
                timeout=60,  # Longer timeout for large diffs
            )
            if response.ok:
                return response.text
        except Exception as e:
            logger.warning(f"Failed to fetch PR diff: {e}")
        return None

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

    def fetch_milestones(
        self,
        owner: str,
        repo: str,
        state: str = "all",
    ) -> Generator[GithubMilestoneData, None, None]:
        """Fetch all milestones for a repository.

        Args:
            owner: Repository owner
            repo: Repository name
            state: Filter by state: 'open', 'closed', or 'all' (default)

        Yields:
            GithubMilestoneData for each milestone
        """
        params: dict[str, Any] = {
            "state": state,
            "per_page": 100,
        }

        page = 1
        while True:
            params["page"] = page
            response = self.session.get(
                f"{GITHUB_API_URL}/repos/{owner}/{repo}/milestones",
                params=params,
                timeout=30,
            )
            response.raise_for_status()
            self._handle_rate_limit(response)

            milestones = response.json()
            if not milestones:
                break

            for ms in milestones:
                yield GithubMilestoneData(
                    github_id=ms["id"],
                    number=ms["number"],
                    title=ms["title"],
                    description=ms.get("description"),
                    state=ms["state"],
                    due_on=parse_github_date(ms.get("due_on")),
                    github_created_at=parse_github_date(ms["created_at"]),  # type: ignore
                    github_updated_at=parse_github_date(ms["updated_at"]),  # type: ignore
                    closed_at=parse_github_date(ms.get("closed_at")),
                )

            page += 1

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
            milestone_number=(
                issue["milestone"]["number"] if issue.get("milestone") else None
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
            pr_data=None,  # Issues don't have PR data
        )

    def _parse_pr(
        self, owner: str, repo: str, pr: dict[str, Any]
    ) -> GithubIssueData:
        """Parse raw PR data into structured format."""
        pr_number = pr["number"]
        comments = self.fetch_comments(owner, repo, pr_number)
        body = pr.get("body") or ""

        # Fetch PR-specific data
        review_comments = self.fetch_review_comments(owner, repo, pr_number)
        reviews = self.fetch_reviews(owner, repo, pr_number)
        files = self.fetch_pr_files(owner, repo, pr_number)
        full_diff = self.fetch_pr_diff(owner, repo, pr_number)

        # Calculate stats from files
        additions = sum(f["additions"] for f in files)
        deletions = sum(f["deletions"] for f in files)

        # Get diff summary (truncated, for backward compatibility)
        diff_summary = full_diff[:5000] if full_diff else None

        # Build PR data dict
        pr_data = GithubPRDataDict(
            diff=full_diff,
            files=files,
            additions=additions,
            deletions=deletions,
            changed_files_count=len(files),
            reviews=reviews,
            review_comments=review_comments,
        )

        return GithubIssueData(
            kind="pr",
            number=pr_number,
            title=pr["title"],
            body=body,
            state=pr["state"],
            author=pr["user"]["login"] if pr.get("user") else "ghost",
            labels=[label["name"] for label in pr.get("labels", [])],
            assignees=[a["login"] for a in pr.get("assignees", [])],
            milestone_number=pr["milestone"]["number"] if pr.get("milestone") else None,
            created_at=parse_github_date(pr["created_at"]),  # type: ignore
            closed_at=parse_github_date(pr.get("closed_at")),
            merged_at=parse_github_date(pr.get("merged_at")),
            github_updated_at=parse_github_date(pr["updated_at"]),  # type: ignore
            comment_count=len(comments),
            comments=comments,
            diff_summary=diff_summary,
            project_fields=None,  # Fetched separately if enabled
            content_hash=compute_content_hash(body, comments),
            pr_data=pr_data,
        )
