"""GitHub API types and data structures."""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypedDict

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
    open_issues: int
    closed_issues: int
    github_created_at: datetime
    github_updated_at: datetime
    closed_at: datetime | None


class GithubProjectFieldDef(TypedDict):
    """Definition of a field in a GitHub Project."""

    id: str
    name: str
    data_type: str  # TEXT, SINGLE_SELECT, NUMBER, DATE, ITERATION
    options: dict[str, str] | None  # option_name -> option_id (for SINGLE_SELECT)


class GithubProjectData(TypedDict):
    """GitHub Project (v2) data for storage."""

    node_id: str  # GraphQL node ID
    number: int  # Project number
    title: str
    short_description: str | None
    readme: str | None
    public: bool
    closed: bool
    owner_type: str  # 'organization' or 'user'
    owner_login: str  # org or user name
    url: str
    fields: list[GithubProjectFieldDef]
    github_created_at: datetime | None
    github_updated_at: datetime | None
    items_total_count: int  # Number of items in the project


class GithubTeamMember(TypedDict):
    """A member of a GitHub team."""

    login: str
    id: int
    node_id: str
    role: str  # 'member' or 'maintainer'


class GithubTeamData(TypedDict):
    """GitHub Team data for storage."""

    node_id: str  # GraphQL node ID
    github_id: int  # REST API ID
    slug: str  # URL-safe team name
    name: str  # Display name
    description: str | None
    privacy: str  # 'closed' or 'secret'
    permission: str | None  # 'pull', 'push', 'admin', 'maintain', 'triage'
    org_login: str
    parent_team_slug: str | None
    members_count: int
    repos_count: int
    github_created_at: datetime | None
    github_updated_at: datetime | None


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
