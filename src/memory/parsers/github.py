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


def serialize_issue_data(data: GithubIssueData) -> dict[str, Any]:
    """Serialize GithubIssueData for Celery task passing.

    Converts datetime objects to ISO format strings for JSON serialization.
    """
    return {
        **data,
        "created_at": data["created_at"].isoformat() if data["created_at"] else None,
        "closed_at": data["closed_at"].isoformat() if data["closed_at"] else None,
        "merged_at": data["merged_at"].isoformat() if data["merged_at"] else None,
        "github_updated_at": (
            data["github_updated_at"].isoformat()
            if data["github_updated_at"]
            else None
        ),
        "comments": [
            {
                "id": c["id"],
                "author": c["author"],
                "body": c["body"],
                "created_at": c["created_at"],
                "updated_at": c["updated_at"],
            }
            for c in data["comments"]
        ],
        "pr_data": data.get("pr_data"),
    }


class GithubClient:
    """Client for GitHub REST and GraphQL APIs."""

    def __init__(self, credentials: GithubCredentials):
        self.credentials = credentials
        self.session = requests.Session()
        self._setup_auth()

    # =========================================================================
    # Helper Methods
    # =========================================================================

    @staticmethod
    def _extract_nested(data: dict[str, Any] | None, *keys: str, default: Any = None) -> Any:
        """Safely extract a value from nested dicts.

        Example:
            _extract_nested(data, "repository", "issue", "id")
            is equivalent to data.get("repository", {}).get("issue", {}).get("id")
        """
        result = data
        for key in keys:
            if result is None or not isinstance(result, dict):
                return default
            result = result.get(key)
        return result if result is not None else default

    def _graphql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        operation_name: str | None = None,
        timeout: int = 30,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None]:
        """Execute a GraphQL query/mutation and return (data, errors).

        Args:
            query: The GraphQL query or mutation string
            variables: Variables to pass to the query
            operation_name: Optional operation name for logging
            timeout: Request timeout in seconds

        Returns:
            Tuple of (data, errors) where:
            - data is the "data" field from response, or None on HTTP error
            - errors is the "errors" field from response, or None if no errors
        """
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        try:
            response = self.session.post(
                GITHUB_GRAPHQL_URL,
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            self._handle_rate_limit(response)
        except requests.RequestException as e:
            op = operation_name or "GraphQL request"
            logger.warning(f"Failed {op}: {e}")
            return None, None

        result = response.json()
        data = result.get("data")
        errors = result.get("errors")

        if errors:
            op = operation_name or "GraphQL request"
            if data:
                # Partial success - some data returned but with errors
                logger.warning(f"{op} partial success with errors: {errors}")
            else:
                logger.warning(f"{op} failed: {errors}")

        return data, errors

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

    # GraphQL fragment for fetching project item field VALUES (actual data)
    _PROJECT_ITEM_VALUES_FRAGMENT = """
    fragment ProjectFieldValues on ProjectV2ItemConnection {
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
    """

    def _parse_project_items(self, items: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Parse project items into a field values dict.

        Args:
            items: List of project item nodes from GraphQL response

        Returns:
            Dict mapping "ProjectName.FieldName" to values, or None if empty
        """
        if not items:
            return None

        fields: dict[str, Any] = {}
        for item in items:
            project_name = self._extract_nested(item, "project", "title", default="unknown")
            for field_value in self._extract_nested(item, "fieldValues", "nodes", default=[]):
                field_name = self._extract_nested(field_value, "field", "name")
                if not field_name:
                    continue

                # Extract value based on type (order matters for 'or' chain)
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

    def _fetch_item_project_fields(
        self,
        owner: str,
        repo: str,
        number: int,
        kind: str,
    ) -> dict[str, Any] | None:
        """Fetch GitHub Projects v2 field values for an issue or PR.

        Args:
            owner: Repository owner
            repo: Repository name
            number: Issue or PR number
            kind: "issue" or "pullRequest"

        Returns:
            Dict mapping "ProjectName.FieldName" to values, or None if not found
        """
        query = f"""
        query($owner: String!, $repo: String!, $number: Int!) {{
          repository(owner: $owner, name: $repo) {{
            {kind}(number: $number) {{
              projectItems(first: 10) {{
                ...ProjectFieldValues
              }}
            }}
          }}
        }}
        {self._PROJECT_ITEM_VALUES_FRAGMENT}
        """

        data, errors = self._graphql(
            query,
            {"owner": owner, "repo": repo, "number": number},
            operation_name=f"fetch_{kind}_project_fields",
        )
        if errors or data is None:
            return None

        items = self._extract_nested(data, "repository", kind, "projectItems", "nodes", default=[])
        return self._parse_project_items(items)

    def fetch_project_fields(
        self,
        owner: str,
        repo: str,
        issue_number: int,
    ) -> dict[str, Any] | None:
        """Fetch GitHub Projects v2 field values for an issue."""
        return self._fetch_item_project_fields(owner, repo, issue_number, "issue")

    def fetch_pr_project_fields(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> dict[str, Any] | None:
        """Fetch GitHub Projects v2 field values for a PR."""
        return self._fetch_item_project_fields(owner, repo, pr_number, "pullRequest")

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

    # =========================================================================
    # GraphQL Methods for Issue Creation/Update
    # =========================================================================

    def get_repository_id(self, owner: str, repo: str) -> str | None:
        """Get the GraphQL node ID for a repository."""
        query = """
        query($owner: String!, $repo: String!) {
          repository(owner: $owner, name: $repo) { id }
        }
        """
        data, errors = self._graphql(
            query, {"owner": owner, "repo": repo}, operation_name="get_repository_id"
        )
        if errors or data is None:
            return None
        return self._extract_nested(data, "repository", "id")

    def get_issue_node_id(self, owner: str, repo: str, number: int) -> str | None:
        """Get the GraphQL node ID for an issue (needed for mutations)."""
        query = """
        query($owner: String!, $repo: String!, $number: Int!) {
          repository(owner: $owner, name: $repo) {
            issue(number: $number) { id }
          }
        }
        """
        data, errors = self._graphql(
            query,
            {"owner": owner, "repo": repo, "number": number},
            operation_name="get_issue_node_id",
        )
        if errors or data is None:
            return None
        return self._extract_nested(data, "repository", "issue", "id")

    def get_label_ids(self, owner: str, repo: str, label_names: list[str]) -> list[str]:
        """Resolve label names to GraphQL node IDs."""
        if not label_names:
            return []

        query = """
        query($owner: String!, $repo: String!) {
          repository(owner: $owner, name: $repo) {
            labels(first: 100) {
              nodes { id, name }
            }
          }
        }
        """
        data, errors = self._graphql(
            query, {"owner": owner, "repo": repo}, operation_name="get_label_ids"
        )
        if errors or data is None:
            return []

        labels = self._extract_nested(data, "repository", "labels", "nodes", default=[])
        label_map = {label["name"]: label["id"] for label in labels}
        return [label_map[name] for name in label_names if name in label_map]

    def get_user_id(self, username: str) -> str | None:
        """Get the GraphQL node ID for a user."""
        query = """
        query($login: String!) {
          user(login: $login) { id }
        }
        """
        data, errors = self._graphql(
            query, {"login": username}, operation_name=f"get_user_id({username})"
        )
        if errors or data is None:
            return None
        return self._extract_nested(data, "user", "id")

    def get_user_ids(self, usernames: list[str]) -> list[str]:
        """Resolve usernames to GraphQL node IDs."""
        if not usernames:
            return []
        return [uid for u in usernames if (uid := self.get_user_id(u))]

    def get_milestone_node_id(
        self, owner: str, repo: str, milestone_number: int
    ) -> str | None:
        """Get the GraphQL node ID for a milestone."""
        query = """
        query($owner: String!, $repo: String!, $number: Int!) {
          repository(owner: $owner, name: $repo) {
            milestone(number: $number) { id }
          }
        }
        """
        data, errors = self._graphql(
            query,
            {"owner": owner, "repo": repo, "number": milestone_number},
            operation_name="get_milestone_node_id",
        )
        if errors or data is None:
            return None
        return self._extract_nested(data, "repository", "milestone", "id")

    def fetch_issue_graphql(
        self, owner: str, repo: str, number: int
    ) -> GithubIssueData | None:
        """Fetch complete issue data via GraphQL.

        Fetches issue metadata and comments in a single query, returning
        data in GithubIssueData format ready for database sync.
        """
        query = """
        query($owner: String!, $repo: String!, $number: Int!) {
          repository(owner: $owner, name: $repo) {
            issue(number: $number) {
              number
              title
              body
              state
              author { login }
              labels(first: 100) { nodes { name } }
              assignees(first: 50) { nodes { login } }
              milestone { number }
              createdAt
              closedAt
              updatedAt
              comments(first: 100) {
                nodes {
                  databaseId
                  author { login }
                  body
                  createdAt
                  updatedAt
                }
              }
            }
          }
        }
        """
        data, errors = self._graphql(
            query,
            {"owner": owner, "repo": repo, "number": number},
            operation_name=f"fetch_issue({owner}/{repo}#{number})",
        )
        if errors or data is None:
            return None

        issue = self._extract_nested(data, "repository", "issue")
        if issue is None:
            return None

        # Parse comments
        raw_comments = self._extract_nested(issue, "comments", "nodes", default=[])
        comments = [
            GithubComment(
                id=c.get("databaseId", 0),
                author=self._extract_nested(c, "author", "login", default="ghost"),
                body=c.get("body", ""),
                created_at=c.get("createdAt", ""),
                updated_at=c.get("updatedAt", ""),
            )
            for c in raw_comments
        ]

        body = issue.get("body") or ""
        return GithubIssueData(
            kind="issue",
            number=issue["number"],
            title=issue["title"],
            body=body,
            state=issue["state"].lower(),  # GraphQL returns OPEN/CLOSED
            author=self._extract_nested(issue, "author", "login", default="ghost"),
            labels=[
                label["name"]
                for label in self._extract_nested(issue, "labels", "nodes", default=[])
            ],
            assignees=[
                a["login"]
                for a in self._extract_nested(issue, "assignees", "nodes", default=[])
            ],
            milestone_number=self._extract_nested(issue, "milestone", "number"),
            created_at=parse_github_date(issue["createdAt"]),  # type: ignore
            closed_at=parse_github_date(issue.get("closedAt")),
            merged_at=None,  # Issues don't have merged_at
            github_updated_at=parse_github_date(issue["updatedAt"]),  # type: ignore
            comment_count=len(comments),
            comments=comments,
            diff_summary=None,  # Issues don't have diff
            project_fields=None,  # Fetched separately if enabled
            content_hash=compute_content_hash(body, comments),
            pr_data=None,  # Issues don't have PR data
        )

    def create_issue_graphql(
        self,
        repository_id: str,
        title: str,
        body: str | None = None,
        label_ids: list[str] | None = None,
        assignee_ids: list[str] | None = None,
        milestone_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Create a new issue using GraphQL mutation.

        Returns dict with 'id', 'number', 'url' on success, None on failure.
        """
        mutation = """
        mutation CreateIssue($input: CreateIssueInput!) {
          createIssue(input: $input) {
            issue { id, number, url, title, state }
          }
        }
        """
        input_data: dict[str, Any] = {"repositoryId": repository_id, "title": title}
        if body is not None:
            input_data["body"] = body
        if label_ids:
            input_data["labelIds"] = label_ids
        if assignee_ids:
            input_data["assigneeIds"] = assignee_ids
        if milestone_id:
            input_data["milestoneId"] = milestone_id

        data, errors = self._graphql(
            mutation, {"input": input_data}, operation_name="create_issue"
        )
        if errors or data is None:
            return None
        return self._extract_nested(data, "createIssue", "issue")

    def update_issue_graphql(
        self,
        issue_id: str,
        title: str | None = None,
        body: str | None = None,
        state: str | None = None,
        label_ids: list[str] | None = None,
        assignee_ids: list[str] | None = None,
        milestone_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Update an existing issue using GraphQL mutation.

        Args:
            issue_id: GraphQL node ID of the issue
            title: New title (optional)
            body: New body (optional)
            state: New state - "OPEN" or "CLOSED" (optional)
            label_ids: New label IDs (replaces existing)
            assignee_ids: New assignee IDs (replaces existing)
            milestone_id: New milestone ID (optional)

        Returns dict with 'id', 'number', 'url' on success, None on failure.
        """
        mutation = """
        mutation UpdateIssue($input: UpdateIssueInput!) {
          updateIssue(input: $input) {
            issue { id, number, url, title, state }
          }
        }
        """
        input_data: dict[str, Any] = {"id": issue_id}
        if title is not None:
            input_data["title"] = title
        if body is not None:
            input_data["body"] = body
        if state is not None:
            input_data["state"] = state.upper()
        if label_ids is not None:
            input_data["labelIds"] = label_ids
        if assignee_ids is not None:
            input_data["assigneeIds"] = assignee_ids
        if milestone_id is not None:
            input_data["milestoneId"] = milestone_id

        data, errors = self._graphql(
            mutation, {"input": input_data}, operation_name="update_issue"
        )
        if errors or data is None:
            return None
        return self._extract_nested(data, "updateIssue", "issue")

    # =========================================================================
    # GraphQL Methods for Project Management
    # =========================================================================

    # GraphQL fragment for fetching project field DEFINITIONS (schema/metadata)
    _PROJECT_FIELD_DEFS_FRAGMENT = """
    projectsV2(first: 20, query: $projectName) {
      nodes {
        id
        title
        fields(first: 30) {
          nodes {
            ... on ProjectV2Field { id, name }
            ... on ProjectV2SingleSelectField {
              id
              name
              options { id, name }
            }
            ... on ProjectV2IterationField { id, name }
          }
        }
      }
    }
    """

    def _parse_project_fields(self, projects: list[dict[str, Any]], project_name: str) -> dict[str, Any] | None:
        """Parse project list to find matching project and extract fields."""
        for project in projects:
            if project.get("title") == project_name:
                fields: dict[str, Any] = {}
                for field in self._extract_nested(project, "fields", "nodes", default=[]):
                    field_name = field.get("name")
                    if not field_name:
                        continue
                    field_info: dict[str, Any] = {"id": field["id"]}
                    if "options" in field:
                        field_info["options"] = {
                            opt["name"]: opt["id"] for opt in field["options"]
                        }
                    fields[field_name] = field_info
                return {"id": project["id"], "fields": fields}
        return None

    def find_project_by_name(
        self, owner: str, project_name: str, is_org: bool = True
    ) -> dict[str, Any] | None:
        """Find a project by name and return its ID and field definitions.

        Returns:
            {
                "id": "project_node_id",
                "fields": {
                    "Status": {"id": "field_id", "options": {"Todo": "option_id", ...}},
                    "Priority": {"id": "field_id", "options": {...}},
                    ...
                }
            }
            or None if not found
        """
        entity_type = "organization" if is_org else "user"
        query = f"""
        query($owner: String!, $projectName: String!) {{
          {entity_type}(login: $owner) {{
            {self._PROJECT_FIELD_DEFS_FRAGMENT}
          }}
        }}
        """
        data, errors = self._graphql(
            query,
            {"owner": owner, "projectName": project_name},
            operation_name=f"find_project({project_name})",
        )
        if errors:
            logger.warning(f"Error finding project '{project_name}' in {entity_type} '{owner}': {errors}")
            return None
        if data is None:
            return None

        projects = self._extract_nested(data, entity_type, "projectsV2", "nodes", default=[])
        result = self._parse_project_fields(projects, project_name)
        if result is None:
            available = [p.get("title") for p in projects]
            logger.info(f"Project '{project_name}' not found in {entity_type} '{owner}'. Available: {available}")
        return result

    def add_issue_to_project(self, project_id: str, content_id: str) -> str | None:
        """Add an issue to a project.

        Args:
            project_id: GraphQL node ID of the project
            content_id: GraphQL node ID of the issue

        Returns:
            Project item ID on success, None on failure
        """
        mutation = """
        mutation($projectId: ID!, $contentId: ID!) {
          addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
            item { id }
          }
        }
        """
        data, errors = self._graphql(
            mutation,
            {"projectId": project_id, "contentId": content_id},
            operation_name="add_issue_to_project",
        )
        if errors:
            logger.warning(f"Failed to add issue to project: {errors}")
            return None
        if data is None:
            return None
        return self._extract_nested(data, "addProjectV2ItemById", "item", "id")

    def get_project_item_id(
        self, owner: str, repo: str, number: int, project_id: str
    ) -> str | None:
        """Get the project item ID for an issue already in a project."""
        query = """
        query($owner: String!, $repo: String!, $number: Int!) {
          repository(owner: $owner, name: $repo) {
            issue(number: $number) {
              projectItems(first: 20) {
                nodes {
                  id
                  project { id }
                }
              }
            }
          }
        }
        """
        data, errors = self._graphql(
            query,
            {"owner": owner, "repo": repo, "number": number},
            operation_name="get_project_item_id",
        )
        if errors or data is None:
            return None

        items = self._extract_nested(
            data, "repository", "issue", "projectItems", "nodes", default=[]
        )
        for item in items:
            if self._extract_nested(item, "project", "id") == project_id:
                return item.get("id")
        return None

    def update_project_field_value(
        self,
        project_id: str,
        item_id: str,
        field_id: str,
        value: str,
        value_type: str = "singleSelectOptionId",
    ) -> bool:
        """Update a field value for a project item.

        Args:
            project_id: GraphQL node ID of the project
            item_id: GraphQL node ID of the project item
            field_id: GraphQL node ID of the field
            value: The value to set (option ID for single-select, text for text fields)
            value_type: Type of value - "singleSelectOptionId", "text", "number", "date"

        Returns:
            True on success, False on failure
        """
        mutation = """
        mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: ProjectV2FieldValue!) {
          updateProjectV2ItemFieldValue(
            input: {projectId: $projectId, itemId: $itemId, fieldId: $fieldId, value: $value}
          ) {
            projectV2Item { id }
          }
        }
        """
        data, errors = self._graphql(
            mutation,
            {
                "projectId": project_id,
                "itemId": item_id,
                "fieldId": field_id,
                "value": {value_type: value},
            },
            operation_name="update_project_field_value",
        )
        return data is not None and errors is None

    def get_authenticated_user(self) -> dict[str, Any]:
        """Get authenticated user info for validation."""
        response = self.session.get(f"{GITHUB_API_URL}/user", timeout=30)
        response.raise_for_status()
        return response.json()

    def list_repos(
        self,
        per_page: int = 100,
        sort: str = "updated",
        max_repos: int = 500,
    ) -> Generator[dict[str, Any], None, None]:
        """List repositories accessible to the authenticated user/app.

        For PAT auth: Lists repos the user has access to.
        For App auth: Lists repos the app installation has access to.

        Args:
            per_page: Number of repos per API request (max 100).
            sort: Sort order for repos (updated, created, pushed, full_name).
            max_repos: Maximum total repos to return (default 500, prevents runaway pagination).

        Yields:
            Dict with repo info: owner, name, full_name, description, private, html_url
        """
        if self.credentials.auth_type == "app":
            url = f"{GITHUB_API_URL}/installation/repositories"
            params: dict[str, Any] = {"per_page": per_page}
        else:
            url = f"{GITHUB_API_URL}/user/repos"
            params = {
                "per_page": per_page,
                "sort": sort,
                "affiliation": "owner,collaborator,organization_member",
            }

        page = 1
        repos_yielded = 0
        max_pages = (max_repos // per_page) + 1  # Upper bound on pages

        while page <= max_pages:
            params["page"] = page
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            self._handle_rate_limit(response)

            data = response.json()
            repos = data.get("repositories", data) if isinstance(data, dict) else data

            if not repos:
                break

            for repo in repos:
                if repos_yielded >= max_repos:
                    return
                yield {
                    "owner": repo["owner"]["login"],
                    "name": repo["name"],
                    "full_name": repo["full_name"],
                    "description": repo.get("description"),
                    "private": repo.get("private", False),
                    "html_url": repo.get("html_url"),
                }
                repos_yielded += 1

            # If we got fewer repos than requested, we've reached the end
            if len(repos) < per_page:
                break

            page += 1
