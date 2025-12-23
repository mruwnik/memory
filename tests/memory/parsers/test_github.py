"""Tests for GitHub API client and parser."""

import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, patch, MagicMock
import requests

from memory.parsers.github import (
    GithubCredentials,
    GithubClient,
    GithubIssueData,
    GithubComment,
    parse_github_date,
    compute_content_hash,
)


# =============================================================================
# Tests for utility functions
# =============================================================================


@pytest.mark.parametrize(
    "date_str,expected",
    [
        ("2024-01-15T10:30:00Z", datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)),
        (
            "2024-06-20T14:45:30Z",
            datetime(2024, 6, 20, 14, 45, 30, tzinfo=timezone.utc),
        ),
        (None, None),
        ("", None),
    ],
)
def test_parse_github_date(date_str, expected):
    """Test parsing GitHub date strings."""
    result = parse_github_date(date_str)
    assert result == expected


def test_compute_content_hash_body_only():
    """Test content hash with body only."""
    hash1 = compute_content_hash("This is the body", [])
    hash2 = compute_content_hash("This is the body", [])
    hash3 = compute_content_hash("Different body", [])

    assert hash1 == hash2  # Same content = same hash
    assert hash1 != hash3  # Different content = different hash


def test_compute_content_hash_with_comments():
    """Test content hash includes comments."""
    comments = [
        GithubComment(
            id=1,
            author="user1",
            body="First comment",
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        ),
        GithubComment(
            id=2,
            author="user2",
            body="Second comment",
            created_at="2024-01-02T00:00:00Z",
            updated_at="2024-01-02T00:00:00Z",
        ),
    ]

    hash_with_comments = compute_content_hash("Body", comments)
    hash_without_comments = compute_content_hash("Body", [])

    assert hash_with_comments != hash_without_comments


def test_compute_content_hash_empty_body():
    """Test content hash with empty/None body."""
    hash1 = compute_content_hash("", [])
    hash2 = compute_content_hash(None, [])  # type: ignore

    # Both should produce valid hashes
    assert len(hash1) == 64  # SHA256 hex
    assert len(hash2) == 64


def test_compute_content_hash_comment_order_matters():
    """Test that comment order affects the hash."""
    comment1 = GithubComment(
        id=1, author="a", body="First", created_at="", updated_at=""
    )
    comment2 = GithubComment(
        id=2, author="b", body="Second", created_at="", updated_at=""
    )

    hash_order1 = compute_content_hash("Body", [comment1, comment2])
    hash_order2 = compute_content_hash("Body", [comment2, comment1])

    assert hash_order1 != hash_order2


# =============================================================================
# Tests for GithubClient initialization
# =============================================================================


def test_github_client_pat_auth():
    """Test client initialization with PAT authentication."""
    credentials = GithubCredentials(
        auth_type="pat",
        access_token="ghp_test_token",
    )

    with patch.object(requests.Session, "get"):
        client = GithubClient(credentials)

    assert "Bearer ghp_test_token" in client.session.headers["Authorization"]
    assert client.session.headers["Accept"] == "application/vnd.github+json"
    assert client.session.headers["X-GitHub-Api-Version"] == "2022-11-28"


# =============================================================================
# Tests for fetch_issues
# =============================================================================


def test_fetch_issues_basic():
    """Test fetching issues from repository."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    def mock_get(url, **kwargs):
        """Route mock responses based on URL."""
        response = Mock()
        response.headers = {"X-RateLimit-Remaining": "4999"}
        response.raise_for_status = Mock()

        page = kwargs.get("params", {}).get("page", 1)

        if "/repos/" in url and "/issues" in url and "/comments" not in url:
            # Issues endpoint
            if page == 1:
                response.json.return_value = [
                    {
                        "number": 1,
                        "title": "Test Issue",
                        "body": "Issue body",
                        "state": "open",
                        "user": {"login": "testuser"},
                        "labels": [{"name": "bug"}],
                        "assignees": [{"login": "dev1"}],
                        "milestone": {"title": "v1.0"},
                        "created_at": "2024-01-01T00:00:00Z",
                        "updated_at": "2024-01-02T00:00:00Z",
                        "closed_at": None,
                        "comments": 2,
                        # Note: Do NOT include "pull_request" key for real issues
                        # The API checks `if "pull_request" in issue` to skip PRs
                    }
                ]
            else:
                response.json.return_value = []
        elif "/comments" in url:
            # Comments endpoint
            if page == 1:
                response.json.return_value = [
                    {
                        "id": 100,
                        "user": {"login": "commenter"},
                        "body": "A comment",
                        "created_at": "2024-01-01T12:00:00Z",
                        "updated_at": "2024-01-01T12:00:00Z",
                    }
                ]
            else:
                response.json.return_value = []
        else:
            response.json.return_value = []

        return response

    with patch.object(requests.Session, "get", side_effect=mock_get):
        client = GithubClient(credentials)
        issues = list(client.fetch_issues("owner", "repo"))

    assert len(issues) == 1
    issue = issues[0]
    assert issue["number"] == 1
    assert issue["title"] == "Test Issue"
    assert issue["kind"] == "issue"
    assert issue["state"] == "open"
    assert issue["author"] == "testuser"
    assert issue["labels"] == ["bug"]
    assert issue["assignees"] == ["dev1"]
    assert issue["milestone"] == "v1.0"
    assert len(issue["comments"]) == 1


def test_fetch_issues_skips_prs():
    """Test that PRs in issue list are skipped."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    def mock_get(url, **kwargs):
        """Route mock responses based on URL."""
        response = Mock()
        response.headers = {"X-RateLimit-Remaining": "4999"}
        response.raise_for_status = Mock()

        page = kwargs.get("params", {}).get("page", 1)

        if "/repos/" in url and "/issues" in url and "/comments" not in url:
            if page == 1:
                response.json.return_value = [
                    {
                        "number": 1,
                        "title": "Issue",
                        "body": "Body",
                        "state": "open",
                        "user": {"login": "user"},
                        "labels": [],
                        "assignees": [],
                        "milestone": None,
                        "created_at": "2024-01-01T00:00:00Z",
                        "updated_at": "2024-01-01T00:00:00Z",
                        "closed_at": None,
                        "comments": 0,
                        # Real issues don't have "pull_request" key
                    },
                    {
                        "number": 2,
                        "title": "PR posing as issue",
                        "body": "Body",
                        "state": "open",
                        "user": {"login": "user"},
                        "labels": [],
                        "assignees": [],
                        "milestone": None,
                        "created_at": "2024-01-01T00:00:00Z",
                        "updated_at": "2024-01-01T00:00:00Z",
                        "closed_at": None,
                        "comments": 0,
                        "pull_request": {"url": "https://..."},  # PRs have this key
                    },
                ]
            else:
                response.json.return_value = []
        elif "/comments" in url:
            response.json.return_value = []
        else:
            response.json.return_value = []

        return response

    with patch.object(requests.Session, "get", side_effect=mock_get):
        client = GithubClient(credentials)
        issues = list(client.fetch_issues("owner", "repo"))

    assert len(issues) == 1
    assert issues[0]["number"] == 1


def test_fetch_issues_with_since_filter():
    """Test fetching issues with since parameter."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = Mock()
    mock_response.json.return_value = []
    mock_response.headers = {"X-RateLimit-Remaining": "4999"}
    mock_response.raise_for_status = Mock()

    with patch.object(requests.Session, "get") as mock_get:
        mock_get.return_value = mock_response

        client = GithubClient(credentials)
        since = datetime(2024, 1, 15, tzinfo=timezone.utc)
        list(client.fetch_issues("owner", "repo", since=since))

    # Verify since was passed to API
    call_args = mock_get.call_args
    assert "since" in call_args.kwargs.get("params", {})


def test_fetch_issues_with_state_filter():
    """Test fetching issues with state filter."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = Mock()
    mock_response.json.return_value = []
    mock_response.headers = {"X-RateLimit-Remaining": "4999"}
    mock_response.raise_for_status = Mock()

    with patch.object(requests.Session, "get") as mock_get:
        mock_get.return_value = mock_response

        client = GithubClient(credentials)
        list(client.fetch_issues("owner", "repo", state="closed"))

    call_args = mock_get.call_args
    assert call_args.kwargs.get("params", {}).get("state") == "closed"


def test_fetch_issues_with_labels_filter():
    """Test fetching issues with labels filter."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = Mock()
    mock_response.json.return_value = []
    mock_response.headers = {"X-RateLimit-Remaining": "4999"}
    mock_response.raise_for_status = Mock()

    with patch.object(requests.Session, "get") as mock_get:
        mock_get.return_value = mock_response

        client = GithubClient(credentials)
        list(client.fetch_issues("owner", "repo", labels=["bug", "critical"]))

    call_args = mock_get.call_args
    assert call_args.kwargs.get("params", {}).get("labels") == "bug,critical"


# =============================================================================
# Tests for fetch_prs
# =============================================================================


def test_fetch_prs_basic():
    """Test fetching PRs from repository."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    def mock_get(url, **kwargs):
        """Route mock responses based on URL."""
        response = Mock()
        response.headers = {"X-RateLimit-Remaining": "4999"}
        response.raise_for_status = Mock()

        page = kwargs.get("params", {}).get("page", 1)

        if "/pulls" in url and "/comments" not in url:
            if page == 1:
                response.json.return_value = [
                    {
                        "number": 10,
                        "title": "Add feature",
                        "body": "PR body",
                        "state": "open",
                        "user": {"login": "contributor"},
                        "labels": [{"name": "enhancement"}],
                        "assignees": [{"login": "reviewer"}],
                        "milestone": None,
                        "created_at": "2024-01-05T00:00:00Z",
                        "updated_at": "2024-01-06T00:00:00Z",
                        "closed_at": None,
                        "merged_at": None,
                        "diff_url": "https://github.com/owner/repo/pull/10.diff",
                        "comments": 0,
                    }
                ]
            else:
                response.json.return_value = []
        elif ".diff" in url:
            response.ok = True
            response.text = "+100 lines added\n-50 lines removed"
        elif "/comments" in url:
            response.json.return_value = []
        else:
            response.json.return_value = []

        return response

    with patch.object(requests.Session, "get", side_effect=mock_get):
        client = GithubClient(credentials)
        prs = list(client.fetch_prs("owner", "repo"))

    assert len(prs) == 1
    pr = prs[0]
    assert pr["number"] == 10
    assert pr["title"] == "Add feature"
    assert pr["kind"] == "pr"
    assert pr["diff_summary"] is not None
    assert "100 lines added" in pr["diff_summary"]


def test_fetch_prs_merged():
    """Test fetching merged PR."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = Mock()
    mock_response.json.return_value = [
        {
            "number": 20,
            "title": "Merged PR",
            "body": "Body",
            "state": "closed",
            "user": {"login": "user"},
            "labels": [],
            "assignees": [],
            "milestone": None,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-10T00:00:00Z",
            "closed_at": "2024-01-10T00:00:00Z",
            "merged_at": "2024-01-10T00:00:00Z",
            "additions": 10,
            "deletions": 5,
            "comments": 0,
        }
    ]
    mock_response.headers = {"X-RateLimit-Remaining": "4999"}
    mock_response.raise_for_status = Mock()

    mock_empty = Mock()
    mock_empty.json.return_value = []
    mock_empty.headers = {"X-RateLimit-Remaining": "4998"}
    mock_empty.raise_for_status = Mock()

    with patch.object(requests.Session, "get") as mock_get:
        mock_get.side_effect = [mock_response, mock_empty, mock_empty]

        client = GithubClient(credentials)
        prs = list(client.fetch_prs("owner", "repo"))

    pr = prs[0]
    assert pr["state"] == "closed"
    assert pr["merged_at"] == datetime(2024, 1, 10, 0, 0, 0, tzinfo=timezone.utc)


def test_fetch_prs_stops_at_since():
    """Test that PR fetching stops when reaching older items."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = Mock()
    mock_response.json.return_value = [
        {
            "number": 30,
            "title": "Recent PR",
            "body": "Body",
            "state": "open",
            "user": {"login": "user"},
            "labels": [],
            "assignees": [],
            "milestone": None,
            "created_at": "2024-01-20T00:00:00Z",
            "updated_at": "2024-01-20T00:00:00Z",
            "closed_at": None,
            "merged_at": None,
            "additions": 1,
            "deletions": 1,
            "comments": 0,
        },
        {
            "number": 29,
            "title": "Old PR",
            "body": "Body",
            "state": "open",
            "user": {"login": "user"},
            "labels": [],
            "assignees": [],
            "milestone": None,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",  # Older than since
            "closed_at": None,
            "merged_at": None,
            "additions": 1,
            "deletions": 1,
            "comments": 0,
        },
    ]
    mock_response.headers = {"X-RateLimit-Remaining": "4999"}
    mock_response.raise_for_status = Mock()

    mock_empty = Mock()
    mock_empty.json.return_value = []
    mock_empty.headers = {"X-RateLimit-Remaining": "4998"}
    mock_empty.raise_for_status = Mock()

    with patch.object(requests.Session, "get") as mock_get:
        mock_get.side_effect = [mock_response, mock_empty]

        client = GithubClient(credentials)
        since = datetime(2024, 1, 15, tzinfo=timezone.utc)
        prs = list(client.fetch_prs("owner", "repo", since=since))

    # Should only get the recent PR, stop at the old one
    assert len(prs) == 1
    assert prs[0]["number"] == 30


# =============================================================================
# Tests for fetch_comments
# =============================================================================


def test_fetch_comments_pagination():
    """Test comment fetching with pagination."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    # First page of comments
    mock_page1 = Mock()
    mock_page1.json.return_value = [
        {
            "id": 1,
            "user": {"login": "user1"},
            "body": "Comment 1",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        }
    ]
    mock_page1.headers = {"X-RateLimit-Remaining": "4999"}
    mock_page1.raise_for_status = Mock()

    # Second page of comments
    mock_page2 = Mock()
    mock_page2.json.return_value = [
        {
            "id": 2,
            "user": {"login": "user2"},
            "body": "Comment 2",
            "created_at": "2024-01-02T00:00:00Z",
            "updated_at": "2024-01-02T00:00:00Z",
        }
    ]
    mock_page2.headers = {"X-RateLimit-Remaining": "4998"}
    mock_page2.raise_for_status = Mock()

    # Empty page to stop
    mock_empty = Mock()
    mock_empty.json.return_value = []
    mock_empty.headers = {"X-RateLimit-Remaining": "4997"}
    mock_empty.raise_for_status = Mock()

    with patch.object(requests.Session, "get") as mock_get:
        mock_get.side_effect = [mock_page1, mock_page2, mock_empty]

        client = GithubClient(credentials)
        comments = client.fetch_comments("owner", "repo", 1)

    assert len(comments) == 2
    assert comments[0]["author"] == "user1"
    assert comments[1]["author"] == "user2"


def test_fetch_comments_handles_ghost_user():
    """Test comment with deleted/ghost user."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = Mock()
    mock_response.json.return_value = [
        {
            "id": 1,
            "user": None,  # Deleted user
            "body": "Comment from ghost",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        }
    ]
    mock_response.headers = {"X-RateLimit-Remaining": "4999"}
    mock_response.raise_for_status = Mock()

    mock_empty = Mock()
    mock_empty.json.return_value = []
    mock_empty.headers = {"X-RateLimit-Remaining": "4998"}
    mock_empty.raise_for_status = Mock()

    with patch.object(requests.Session, "get") as mock_get:
        mock_get.side_effect = [mock_response, mock_empty]

        client = GithubClient(credentials)
        comments = client.fetch_comments("owner", "repo", 1)

    assert len(comments) == 1
    assert comments[0]["author"] == "ghost"


# =============================================================================
# Tests for rate limiting
# =============================================================================


def test_rate_limit_handling():
    """Test rate limit detection and backoff."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = Mock()
    mock_response.json.return_value = []
    mock_response.headers = {
        "X-RateLimit-Remaining": "0",
        "X-RateLimit-Reset": str(int(datetime.now(timezone.utc).timestamp()) + 1),
    }
    mock_response.raise_for_status = Mock()

    with patch.object(requests.Session, "get") as mock_get:
        mock_get.return_value = mock_response
        with patch("time.sleep") as mock_sleep:
            client = GithubClient(credentials)
            list(client.fetch_issues("owner", "repo"))

            # Should have waited due to rate limit
            mock_sleep.assert_called()


# =============================================================================
# Tests for project fields
# =============================================================================


def test_fetch_project_fields():
    """Test fetching GitHub Projects v2 fields."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = Mock()
    mock_response.json.return_value = {
        "data": {
            "repository": {
                "issue": {
                    "projectItems": {
                        "nodes": [
                            {
                                "project": {"title": "Sprint Board"},
                                "fieldValues": {
                                    "nodes": [
                                        {"field": {"name": "Status"}, "name": "In Progress"},
                                        {"field": {"name": "Priority"}, "text": "High"},
                                    ]
                                },
                            }
                        ]
                    }
                }
            }
        }
    }
    mock_response.headers = {"X-RateLimit-Remaining": "4999"}
    mock_response.raise_for_status = Mock()

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_response

        client = GithubClient(credentials)
        fields = client.fetch_project_fields("owner", "repo", 1)

    assert fields is not None
    # Fields are prefixed with project name
    assert "Sprint Board.Status" in fields
    assert fields["Sprint Board.Status"] == "In Progress"
    assert "Sprint Board.Priority" in fields
    assert fields["Sprint Board.Priority"] == "High"


def test_fetch_project_fields_not_in_project():
    """Test fetching project fields for issue not in any project."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = Mock()
    mock_response.json.return_value = {
        "data": {"repository": {"issue": {"projectItems": {"nodes": []}}}}
    }
    mock_response.headers = {"X-RateLimit-Remaining": "4999"}
    mock_response.raise_for_status = Mock()

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_response

        client = GithubClient(credentials)
        fields = client.fetch_project_fields("owner", "repo", 1)

    assert fields is None


def test_fetch_project_fields_graphql_error():
    """Test handling GraphQL errors gracefully."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = Mock()
    mock_response.json.return_value = {
        "errors": [{"message": "Something went wrong"}],
        "data": None,
    }
    mock_response.headers = {"X-RateLimit-Remaining": "4999"}
    mock_response.raise_for_status = Mock()

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_response

        client = GithubClient(credentials)
        fields = client.fetch_project_fields("owner", "repo", 1)

    assert fields is None


# =============================================================================
# Tests for error handling
# =============================================================================


def test_fetch_issues_handles_api_error():
    """Test graceful handling of API errors."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = Mock()
    mock_response.raise_for_status.side_effect = requests.HTTPError("404 Not Found")

    with patch.object(requests.Session, "get") as mock_get:
        mock_get.return_value = mock_response

        client = GithubClient(credentials)

        with pytest.raises(requests.HTTPError):
            list(client.fetch_issues("owner", "nonexistent"))
