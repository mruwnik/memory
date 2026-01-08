"""GitHub API client for fetching issues, PRs, comments, and project fields.

This package provides a unified GithubClient composed of specialized mixins:
- IssuesMixin: Issue/PR fetching, parsing, and mutations
- ProjectsMixin: GitHub Projects (v2) management
- TeamsMixin: Team management and membership operations

Example:
    from memory.common.github import GithubClient, GithubCredentials

    credentials = GithubCredentials(auth_type="pat", access_token="...")
    client = GithubClient(credentials)

    # Fetch issues
    for issue in client.fetch_issues("owner", "repo"):
        print(issue["title"])

    # List teams
    for team in client.list_teams("org"):
        print(team["name"])
"""

from .core import GithubClientCore
from .issues import IssuesMixin
from .projects import ProjectsMixin
from .teams import TeamsMixin
from .types import (
    GITHUB_API_URL,
    GITHUB_GRAPHQL_URL,
    GithubComment,
    GithubCredentials,
    GithubFileChange,
    GithubIssueData,
    GithubMilestoneData,
    GithubPRDataDict,
    GithubProjectData,
    GithubProjectFieldDef,
    GithubReview,
    GithubReviewComment,
    GithubTeamData,
    GithubTeamMember,
    compute_content_hash,
    parse_github_date,
    serialize_issue_data,
)


class GithubClient(IssuesMixin, ProjectsMixin, TeamsMixin, GithubClientCore):
    """Complete GitHub API client combining all functionality.

    Inherits from:
    - GithubClientCore: Authentication, GraphQL, rate limiting
    - IssuesMixin: Issue/PR fetching, parsing, mutations
    - ProjectsMixin: GitHub Projects (v2) management
    - TeamsMixin: Team management and membership
    """

    pass


__all__ = [
    # Main client
    "GithubClient",
    # Types
    "GithubCredentials",
    "GithubComment",
    "GithubReviewComment",
    "GithubReview",
    "GithubFileChange",
    "GithubPRDataDict",
    "GithubMilestoneData",
    "GithubProjectFieldDef",
    "GithubProjectData",
    "GithubTeamMember",
    "GithubTeamData",
    "GithubIssueData",
    # Utilities
    "parse_github_date",
    "compute_content_hash",
    "serialize_issue_data",
    # Constants
    "GITHUB_API_URL",
    "GITHUB_GRAPHQL_URL",
]
