"""Tests for GitHub API client and parser."""

import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, patch
import requests

from memory.parsers.github import (
    GithubCredentials,
    GithubClient,
    GithubComment,
    parse_github_date,
    compute_content_hash,
)


# =============================================================================
# Helper for creating mock GraphQL responses
# =============================================================================


def mock_graphql_response(data=None, errors=None):
    """Create a mock response for GraphQL requests."""
    response = Mock()
    response.json.return_value = {"data": data, "errors": errors} if errors else {"data": data}
    response.headers = {"X-RateLimit-Remaining": "4999"}
    response.raise_for_status = Mock()
    return response


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
                        "milestone": {"title": "v1.0", "number": 1},
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
    assert issue["milestone_number"] == 1
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

        # PR list endpoint
        if "/pulls" in url and "/comments" not in url and "/reviews" not in url and "/files" not in url:
            # Check if this is the diff request
            if kwargs.get("headers", {}).get("Accept") == "application/vnd.github.diff":
                response.ok = True
                response.text = "+100 lines added\n-50 lines removed"
                return response
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
        elif "/pulls/" in url and "/comments" in url:
            # Review comments endpoint
            response.json.return_value = []
        elif "/pulls/" in url and "/reviews" in url:
            # Reviews endpoint
            response.json.return_value = []
        elif "/pulls/" in url and "/files" in url:
            # Files endpoint
            if page == 1:
                response.json.return_value = [
                    {"filename": "test.py", "status": "added", "additions": 100, "deletions": 50, "patch": "+code"}
                ]
            else:
                response.json.return_value = []
        elif "/issues/" in url and "/comments" in url:
            # Regular comments endpoint
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
    # Verify pr_data is populated
    assert pr["pr_data"] is not None
    assert pr["pr_data"]["additions"] == 100
    assert pr["pr_data"]["deletions"] == 50


def test_fetch_prs_merged():
    """Test fetching merged PR."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    def mock_get(url, **kwargs):
        """Route mock responses based on URL."""
        response = Mock()
        response.headers = {"X-RateLimit-Remaining": "4999"}
        response.raise_for_status = Mock()

        page = kwargs.get("params", {}).get("page", 1)

        # PR list endpoint
        if "/pulls" in url and "/comments" not in url and "/reviews" not in url and "/files" not in url:
            if kwargs.get("headers", {}).get("Accept") == "application/vnd.github.diff":
                response.ok = True
                response.text = ""
                return response
            if page == 1:
                response.json.return_value = [
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
            else:
                response.json.return_value = []
        elif "/pulls/" in url and "/comments" in url:
            response.json.return_value = []
        elif "/pulls/" in url and "/reviews" in url:
            response.json.return_value = []
        elif "/pulls/" in url and "/files" in url:
            response.json.return_value = []
        elif "/issues/" in url and "/comments" in url:
            response.json.return_value = []
        else:
            response.json.return_value = []

        return response

    with patch.object(requests.Session, "get", side_effect=mock_get):
        client = GithubClient(credentials)
        prs = list(client.fetch_prs("owner", "repo"))

    pr = prs[0]
    assert pr["state"] == "closed"
    assert pr["merged_at"] == datetime(2024, 1, 10, 0, 0, 0, tzinfo=timezone.utc)


def test_fetch_prs_stops_at_since():
    """Test that PR fetching stops when reaching older items."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    def mock_get(url, **kwargs):
        """Route mock responses based on URL."""
        response = Mock()
        response.headers = {"X-RateLimit-Remaining": "4999"}
        response.raise_for_status = Mock()

        page = kwargs.get("params", {}).get("page", 1)

        # PR list endpoint
        if "/pulls" in url and "/comments" not in url and "/reviews" not in url and "/files" not in url:
            if kwargs.get("headers", {}).get("Accept") == "application/vnd.github.diff":
                response.ok = True
                response.text = ""
                return response
            if page == 1:
                response.json.return_value = [
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
            else:
                response.json.return_value = []
        elif "/pulls/" in url and "/comments" in url:
            response.json.return_value = []
        elif "/pulls/" in url and "/reviews" in url:
            response.json.return_value = []
        elif "/pulls/" in url and "/files" in url:
            response.json.return_value = []
        elif "/issues/" in url and "/comments" in url:
            response.json.return_value = []
        else:
            response.json.return_value = []

        return response

    with patch.object(requests.Session, "get", side_effect=mock_get):
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


# =============================================================================
# Tests for fetch_review_comments
# =============================================================================


def test_fetch_review_comments_basic():
    """Test fetching PR review comments."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = Mock()
    mock_response.json.return_value = [
        {
            "id": 1001,
            "user": {"login": "reviewer1"},
            "body": "This needs a test",
            "path": "src/main.py",
            "line": 42,
            "side": "RIGHT",
            "diff_hunk": "@@ -40,3 +40,5 @@",
            "created_at": "2024-01-01T12:00:00Z",
        },
        {
            "id": 1002,
            "user": {"login": "reviewer2"},
            "body": "Good refactoring",
            "path": "src/utils.py",
            "line": 10,
            "side": "LEFT",
            "diff_hunk": "@@ -8,5 +8,5 @@",
            "created_at": "2024-01-02T10:00:00Z",
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
        comments = client.fetch_review_comments("owner", "repo", 10)

    assert len(comments) == 2
    assert comments[0]["user"] == "reviewer1"
    assert comments[0]["body"] == "This needs a test"
    assert comments[0]["path"] == "src/main.py"
    assert comments[0]["line"] == 42
    assert comments[0]["side"] == "RIGHT"
    assert comments[0]["diff_hunk"] == "@@ -40,3 +40,5 @@"
    assert comments[1]["user"] == "reviewer2"


def test_fetch_review_comments_ghost_user():
    """Test review comments with deleted user."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = Mock()
    mock_response.json.return_value = [
        {
            "id": 1001,
            "user": None,  # Deleted user
            "body": "Legacy comment",
            "path": "file.py",
            "line": None,  # Line might be None for outdated comments
            "side": "RIGHT",
            "diff_hunk": "",
            "created_at": "2024-01-01T00:00:00Z",
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
        comments = client.fetch_review_comments("owner", "repo", 10)

    assert len(comments) == 1
    assert comments[0]["user"] == "ghost"
    assert comments[0]["line"] is None


def test_fetch_review_comments_pagination():
    """Test review comment fetching with pagination."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_page1 = Mock()
    mock_page1.json.return_value = [
        {
            "id": i,
            "user": {"login": f"user{i}"},
            "body": f"Comment {i}",
            "path": "file.py",
            "line": i,
            "side": "RIGHT",
            "diff_hunk": "",
            "created_at": "2024-01-01T00:00:00Z",
        }
        for i in range(100)
    ]
    mock_page1.headers = {"X-RateLimit-Remaining": "4999"}
    mock_page1.raise_for_status = Mock()

    mock_page2 = Mock()
    mock_page2.json.return_value = [
        {
            "id": 100,
            "user": {"login": "user100"},
            "body": "Final comment",
            "path": "file.py",
            "line": 100,
            "side": "RIGHT",
            "diff_hunk": "",
            "created_at": "2024-01-01T00:00:00Z",
        }
    ]
    mock_page2.headers = {"X-RateLimit-Remaining": "4998"}
    mock_page2.raise_for_status = Mock()

    mock_empty = Mock()
    mock_empty.json.return_value = []
    mock_empty.headers = {"X-RateLimit-Remaining": "4997"}
    mock_empty.raise_for_status = Mock()

    with patch.object(requests.Session, "get") as mock_get:
        mock_get.side_effect = [mock_page1, mock_page2, mock_empty]

        client = GithubClient(credentials)
        comments = client.fetch_review_comments("owner", "repo", 10)

    assert len(comments) == 101


# =============================================================================
# Tests for fetch_reviews
# =============================================================================


def test_fetch_reviews_basic():
    """Test fetching PR reviews."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = Mock()
    mock_response.json.return_value = [
        {
            "id": 2001,
            "user": {"login": "lead_dev"},
            "state": "APPROVED",
            "body": "LGTM!",
            "submitted_at": "2024-01-05T15:00:00Z",
        },
        {
            "id": 2002,
            "user": {"login": "qa_engineer"},
            "state": "CHANGES_REQUESTED",
            "body": "Please add tests",
            "submitted_at": "2024-01-04T10:00:00Z",
        },
        {
            "id": 2003,
            "user": {"login": "observer"},
            "state": "COMMENTED",
            "body": None,  # Some reviews have no body
            "submitted_at": "2024-01-03T08:00:00Z",
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
        reviews = client.fetch_reviews("owner", "repo", 10)

    assert len(reviews) == 3
    assert reviews[0]["user"] == "lead_dev"
    assert reviews[0]["state"] == "approved"  # Lowercased
    assert reviews[0]["body"] == "LGTM!"
    assert reviews[1]["state"] == "changes_requested"
    assert reviews[2]["body"] is None


def test_fetch_reviews_ghost_user():
    """Test reviews with deleted user."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = Mock()
    mock_response.json.return_value = [
        {
            "id": 2001,
            "user": None,
            "state": "APPROVED",
            "body": "Approved by former employee",
            "submitted_at": "2024-01-01T00:00:00Z",
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
        reviews = client.fetch_reviews("owner", "repo", 10)

    assert len(reviews) == 1
    assert reviews[0]["user"] == "ghost"


# =============================================================================
# Tests for fetch_pr_files
# =============================================================================


def test_fetch_pr_files_basic():
    """Test fetching PR file changes."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = Mock()
    mock_response.json.return_value = [
        {
            "filename": "src/main.py",
            "status": "modified",
            "additions": 10,
            "deletions": 5,
            "patch": "@@ -1,5 +1,10 @@\n+new code\n-old code",
        },
        {
            "filename": "src/new_feature.py",
            "status": "added",
            "additions": 100,
            "deletions": 0,
            "patch": "@@ -0,0 +1,100 @@\n+entire new file",
        },
        {
            "filename": "old_file.py",
            "status": "removed",
            "additions": 0,
            "deletions": 50,
            "patch": "@@ -1,50 +0,0 @@\n-entire old file",
        },
        {
            "filename": "image.png",
            "status": "added",
            "additions": 0,
            "deletions": 0,
            # No patch for binary files
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
        files = client.fetch_pr_files("owner", "repo", 10)

    assert len(files) == 4
    assert files[0]["filename"] == "src/main.py"
    assert files[0]["status"] == "modified"
    assert files[0]["additions"] == 10
    assert files[0]["deletions"] == 5
    assert files[0]["patch"] is not None

    assert files[1]["status"] == "added"
    assert files[2]["status"] == "removed"
    assert files[3]["patch"] is None  # Binary file


def test_fetch_pr_files_renamed():
    """Test PR with renamed files."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = Mock()
    mock_response.json.return_value = [
        {
            "filename": "new_name.py",
            "status": "renamed",
            "additions": 0,
            "deletions": 0,
            "patch": None,
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
        files = client.fetch_pr_files("owner", "repo", 10)

    assert len(files) == 1
    assert files[0]["status"] == "renamed"


# =============================================================================
# Tests for fetch_pr_diff
# =============================================================================


def test_fetch_pr_diff_success():
    """Test fetching full PR diff."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    diff_text = """diff --git a/file.py b/file.py
index abc123..def456 100644
--- a/file.py
+++ b/file.py
@@ -1,5 +1,10 @@
+import os
+
 def main():
-    print("old")
+    print("new")
"""

    mock_response = Mock()
    mock_response.ok = True
    mock_response.text = diff_text

    with patch.object(requests.Session, "get") as mock_get:
        mock_get.return_value = mock_response

        client = GithubClient(credentials)
        diff = client.fetch_pr_diff("owner", "repo", 10)

    assert diff == diff_text
    # Verify Accept header was set for diff format
    call_kwargs = mock_get.call_args.kwargs
    assert call_kwargs["headers"]["Accept"] == "application/vnd.github.diff"


def test_fetch_pr_diff_failure():
    """Test handling diff fetch failure gracefully."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = Mock()
    mock_response.ok = False

    with patch.object(requests.Session, "get") as mock_get:
        mock_get.return_value = mock_response

        client = GithubClient(credentials)
        diff = client.fetch_pr_diff("owner", "repo", 10)

    assert diff is None


def test_fetch_pr_diff_exception():
    """Test handling exceptions during diff fetch."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    with patch.object(requests.Session, "get") as mock_get:
        mock_get.side_effect = requests.RequestException("Network error")

        client = GithubClient(credentials)
        diff = client.fetch_pr_diff("owner", "repo", 10)

    assert diff is None


# =============================================================================
# Tests for _parse_pr with pr_data
# =============================================================================


def test_parse_pr_fetches_all_pr_data():
    """Test that _parse_pr fetches and includes all PR-specific data."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    pr_raw = {
        "number": 42,
        "title": "Feature PR",
        "body": "PR description",
        "state": "open",
        "user": {"login": "contributor"},
        "labels": [{"name": "enhancement"}],
        "assignees": [{"login": "reviewer"}],
        "milestone": {"title": "v2.0", "number": 2},
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-02T00:00:00Z",
        "closed_at": None,
        "merged_at": None,
    }

    # Mock responses for all the fetch methods
    def mock_get(url, **kwargs):
        response = Mock()
        response.headers = {"X-RateLimit-Remaining": "4999"}
        response.raise_for_status = Mock()

        if "/issues/42/comments" in url:
            # Regular comments
            page = kwargs.get("params", {}).get("page", 1)
            if page == 1:
                response.json.return_value = [
                    {
                        "id": 1,
                        "user": {"login": "user1"},
                        "body": "Regular comment",
                        "created_at": "2024-01-01T10:00:00Z",
                        "updated_at": "2024-01-01T10:00:00Z",
                    }
                ]
            else:
                response.json.return_value = []
        elif "/pulls/42/comments" in url:
            # Review comments
            page = kwargs.get("params", {}).get("page", 1)
            if page == 1:
                response.json.return_value = [
                    {
                        "id": 101,
                        "user": {"login": "reviewer1"},
                        "body": "Review comment",
                        "path": "src/main.py",
                        "line": 10,
                        "side": "RIGHT",
                        "diff_hunk": "@@ -1,5 +1,10 @@",
                        "created_at": "2024-01-01T12:00:00Z",
                    }
                ]
            else:
                response.json.return_value = []
        elif "/pulls/42/reviews" in url:
            # Reviews
            page = kwargs.get("params", {}).get("page", 1)
            if page == 1:
                response.json.return_value = [
                    {
                        "id": 201,
                        "user": {"login": "lead"},
                        "state": "APPROVED",
                        "body": "LGTM",
                        "submitted_at": "2024-01-02T08:00:00Z",
                    }
                ]
            else:
                response.json.return_value = []
        elif "/pulls/42/files" in url:
            # Files
            page = kwargs.get("params", {}).get("page", 1)
            if page == 1:
                response.json.return_value = [
                    {
                        "filename": "src/main.py",
                        "status": "modified",
                        "additions": 50,
                        "deletions": 10,
                        "patch": "+new\n-old",
                    },
                    {
                        "filename": "tests/test_main.py",
                        "status": "added",
                        "additions": 30,
                        "deletions": 0,
                        "patch": "+tests",
                    },
                ]
            else:
                response.json.return_value = []
        elif "/pulls/42" in url and "diff" in kwargs.get("headers", {}).get(
            "Accept", ""
        ):
            # Full diff
            response.ok = True
            response.text = "diff --git a/src/main.py\n+new code\n-old code"
            return response
        else:
            response.json.return_value = []

        return response

    with patch.object(requests.Session, "get", side_effect=mock_get):
        client = GithubClient(credentials)
        result = client._parse_pr("owner", "repo", pr_raw)

    # Verify basic fields
    assert result["kind"] == "pr"
    assert result["number"] == 42
    assert result["title"] == "Feature PR"
    assert result["author"] == "contributor"
    assert len(result["comments"]) == 1

    # Verify pr_data
    pr_data = result["pr_data"]
    assert pr_data is not None

    # Verify diff
    assert pr_data["diff"] is not None
    assert "new code" in pr_data["diff"]

    # Verify files
    assert len(pr_data["files"]) == 2
    assert pr_data["files"][0]["filename"] == "src/main.py"
    assert pr_data["files"][0]["additions"] == 50

    # Verify stats calculated from files
    assert pr_data["additions"] == 80  # 50 + 30
    assert pr_data["deletions"] == 10
    assert pr_data["changed_files_count"] == 2

    # Verify reviews
    assert len(pr_data["reviews"]) == 1
    assert pr_data["reviews"][0]["user"] == "lead"
    assert pr_data["reviews"][0]["state"] == "approved"

    # Verify review comments
    assert len(pr_data["review_comments"]) == 1
    assert pr_data["review_comments"][0]["user"] == "reviewer1"
    assert pr_data["review_comments"][0]["path"] == "src/main.py"

    # Verify diff_summary is truncated version of full diff
    assert result["diff_summary"] == pr_data["diff"][:5000]


# =============================================================================
# Tests for helper methods
# =============================================================================


@pytest.mark.parametrize(
    "data,keys,default,expected",
    [
        ({"a": {"b": {"c": 1}}}, ("a", "b", "c"), None, 1),
        ({"a": {"b": {"c": 1}}}, ("a", "b"), None, {"c": 1}),
        ({"a": {"b": {"c": 1}}}, ("a", "x"), None, None),
        ({"a": {"b": {"c": 1}}}, ("a", "x"), "default", "default"),
        (None, ("a",), None, None),
        ({}, ("a",), "fallback", "fallback"),
        ({"a": None}, ("a", "b"), "default", "default"),
    ],
)
def test_extract_nested(data, keys, default, expected):
    """Test _extract_nested helper method."""
    result = GithubClient._extract_nested(data, *keys, default=default)
    assert result == expected


def test_graphql_success():
    """Test _graphql helper with successful response."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = mock_graphql_response(data={"repository": {"id": "R_123"}})

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_response

        client = GithubClient(credentials)
        data, errors = client._graphql("query { test }", {"var": "value"})

    assert data == {"repository": {"id": "R_123"}}
    assert errors is None


def test_graphql_with_errors():
    """Test _graphql helper when GraphQL returns errors."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = mock_graphql_response(
        data={"repository": None},
        errors=[{"message": "Not found"}]
    )

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_response

        client = GithubClient(credentials)
        data, errors = client._graphql("query { test }")

    assert data == {"repository": None}
    assert errors == [{"message": "Not found"}]


def test_graphql_request_exception():
    """Test _graphql helper handles request exceptions."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.side_effect = requests.RequestException("Network error")

        client = GithubClient(credentials)
        data, errors = client._graphql("query { test }")

    assert data is None
    assert errors is None


# =============================================================================
# Tests for GraphQL ID resolution methods
# =============================================================================


def test_get_repository_id():
    """Test fetching repository GraphQL node ID."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = mock_graphql_response(
        data={"repository": {"id": "R_kgDOA1234"}}
    )

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_response

        client = GithubClient(credentials)
        repo_id = client.get_repository_id("owner", "repo")

    assert repo_id == "R_kgDOA1234"


def test_get_repository_id_not_found():
    """Test get_repository_id returns None when repo not found."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = mock_graphql_response(
        data={"repository": None},
        errors=[{"message": "Could not resolve to a Repository"}]
    )

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_response

        client = GithubClient(credentials)
        repo_id = client.get_repository_id("owner", "nonexistent")

    assert repo_id is None


def test_get_issue_node_id():
    """Test fetching issue GraphQL node ID."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = mock_graphql_response(
        data={"repository": {"issue": {"id": "I_kwDOA1234"}}}
    )

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_response

        client = GithubClient(credentials)
        issue_id = client.get_issue_node_id("owner", "repo", 123)

    assert issue_id == "I_kwDOA1234"


def test_get_label_ids():
    """Test resolving label names to IDs."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = mock_graphql_response(
        data={
            "repository": {
                "labels": {
                    "nodes": [
                        {"id": "LA_1", "name": "bug"},
                        {"id": "LA_2", "name": "enhancement"},
                        {"id": "LA_3", "name": "docs"},
                    ]
                }
            }
        }
    )

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_response

        client = GithubClient(credentials)
        label_ids = client.get_label_ids("owner", "repo", ["bug", "enhancement", "missing"])

    # Should only return IDs for labels that exist
    assert label_ids == ["LA_1", "LA_2"]


def test_get_label_ids_empty_list():
    """Test get_label_ids with empty input."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    with patch.object(requests.Session, "post") as mock_post:
        client = GithubClient(credentials)
        label_ids = client.get_label_ids("owner", "repo", [])

    # Should not make any API calls
    mock_post.assert_not_called()
    assert label_ids == []


def test_get_user_id():
    """Test fetching user GraphQL node ID."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = mock_graphql_response(
        data={"user": {"id": "U_kgDOBXYZ"}}
    )

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_response

        client = GithubClient(credentials)
        user_id = client.get_user_id("octocat")

    assert user_id == "U_kgDOBXYZ"


def test_get_user_ids():
    """Test resolving multiple usernames to IDs."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    def mock_post_handler(url, **kwargs):
        response = Mock()
        response.headers = {"X-RateLimit-Remaining": "4999"}
        response.raise_for_status = Mock()

        variables = kwargs.get("json", {}).get("variables", {})
        login = variables.get("login", "")

        if login == "user1":
            response.json.return_value = {"data": {"user": {"id": "U_1"}}}
        elif login == "user2":
            response.json.return_value = {"data": {"user": {"id": "U_2"}}}
        else:
            response.json.return_value = {"data": {"user": None}, "errors": [{"message": "Not found"}]}

        return response

    with patch.object(requests.Session, "post", side_effect=mock_post_handler):
        client = GithubClient(credentials)
        user_ids = client.get_user_ids(["user1", "user2", "ghost_user"])

    assert user_ids == ["U_1", "U_2"]


def test_get_milestone_node_id():
    """Test fetching milestone GraphQL node ID."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = mock_graphql_response(
        data={"repository": {"milestone": {"id": "MI_kwDOA1234"}}}
    )

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_response

        client = GithubClient(credentials)
        milestone_id = client.get_milestone_node_id("owner", "repo", 5)

    assert milestone_id == "MI_kwDOA1234"


# =============================================================================
# Tests for GraphQL mutation methods
# =============================================================================


def test_create_issue_graphql():
    """Test creating an issue via GraphQL."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = mock_graphql_response(
        data={
            "createIssue": {
                "issue": {
                    "id": "I_kwDONew",
                    "number": 42,
                    "url": "https://github.com/owner/repo/issues/42",
                    "title": "New Issue",
                    "state": "OPEN",
                }
            }
        }
    )

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_response

        client = GithubClient(credentials)
        result = client.create_issue_graphql(
            repository_id="R_123",
            title="New Issue",
            body="Issue body",
            label_ids=["LA_1"],
            assignee_ids=["U_1"],
        )

    assert result is not None
    assert result["number"] == 42
    assert result["title"] == "New Issue"
    assert result["state"] == "OPEN"


def test_create_issue_graphql_failure():
    """Test create_issue_graphql handles errors."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = mock_graphql_response(
        data={"createIssue": None},
        errors=[{"message": "Repository not found"}]
    )

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_response

        client = GithubClient(credentials)
        result = client.create_issue_graphql(
            repository_id="R_invalid",
            title="New Issue",
        )

    assert result is None


def test_update_issue_graphql():
    """Test updating an issue via GraphQL."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = mock_graphql_response(
        data={
            "updateIssue": {
                "issue": {
                    "id": "I_kwDOExisting",
                    "number": 10,
                    "url": "https://github.com/owner/repo/issues/10",
                    "title": "Updated Title",
                    "state": "CLOSED",
                }
            }
        }
    )

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_response

        client = GithubClient(credentials)
        result = client.update_issue_graphql(
            issue_id="I_kwDOExisting",
            title="Updated Title",
            state="closed",
        )

    assert result is not None
    assert result["title"] == "Updated Title"
    assert result["state"] == "CLOSED"

    # Verify state was uppercased in the request
    call_kwargs = mock_post.call_args.kwargs
    input_data = call_kwargs["json"]["variables"]["input"]
    assert input_data["state"] == "CLOSED"


# =============================================================================
# Tests for project management methods
# =============================================================================


def test_find_project_by_name_org():
    """Test finding an organization project by name."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = mock_graphql_response(
        data={
            "organization": {
                "projectsV2": {
                    "nodes": [
                        {
                            "id": "PVT_org_proj",
                            "title": "Sprint Board",
                            "fields": {
                                "nodes": [
                                    {"id": "PVTF_1", "name": "Title"},
                                    {
                                        "id": "PVTF_2",
                                        "name": "Status",
                                        "options": [
                                            {"id": "opt_1", "name": "Todo"},
                                            {"id": "opt_2", "name": "In Progress"},
                                            {"id": "opt_3", "name": "Done"},
                                        ],
                                    },
                                ]
                            },
                        }
                    ]
                }
            }
        }
    )

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_response

        client = GithubClient(credentials)
        project = client.find_project_by_name("myorg", "Sprint Board", is_org=True)

    assert project is not None
    assert project["id"] == "PVT_org_proj"
    assert "Status" in project["fields"]
    assert project["fields"]["Status"]["id"] == "PVTF_2"
    assert project["fields"]["Status"]["options"]["Todo"] == "opt_1"


def test_find_project_by_name_user():
    """Test finding a user project by name."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = mock_graphql_response(
        data={
            "user": {
                "projectsV2": {
                    "nodes": [
                        {
                            "id": "PVT_user_proj",
                            "title": "Personal Tasks",
                            "fields": {
                                "nodes": [
                                    {"id": "PVTF_1", "name": "Title"},
                                ]
                            },
                        }
                    ]
                }
            }
        }
    )

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_response

        client = GithubClient(credentials)
        project = client.find_project_by_name("octocat", "Personal Tasks", is_org=False)

    assert project is not None
    assert project["id"] == "PVT_user_proj"


def test_find_project_by_name_not_found():
    """Test find_project_by_name when project doesn't exist."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = mock_graphql_response(
        data={"organization": {"projectsV2": {"nodes": []}}}
    )

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_response

        client = GithubClient(credentials)
        project = client.find_project_by_name("myorg", "Nonexistent", is_org=True)

    assert project is None


def test_add_issue_to_project():
    """Test adding an issue to a project."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = mock_graphql_response(
        data={"addProjectV2ItemById": {"item": {"id": "PVTI_item123"}}}
    )

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_response

        client = GithubClient(credentials)
        item_id = client.add_issue_to_project(
            project_id="PVT_proj",
            content_id="I_issue123",
        )

    assert item_id == "PVTI_item123"


def test_get_project_item_id():
    """Test getting project item ID for an issue in a project."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = mock_graphql_response(
        data={
            "repository": {
                "issue": {
                    "projectItems": {
                        "nodes": [
                            {"id": "PVTI_item1", "project": {"id": "PVT_other"}},
                            {"id": "PVTI_item2", "project": {"id": "PVT_target"}},
                        ]
                    }
                }
            }
        }
    )

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_response

        client = GithubClient(credentials)
        item_id = client.get_project_item_id("owner", "repo", 42, "PVT_target")

    assert item_id == "PVTI_item2"


def test_get_project_item_id_not_in_project():
    """Test get_project_item_id when issue not in target project."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = mock_graphql_response(
        data={
            "repository": {
                "issue": {
                    "projectItems": {
                        "nodes": [
                            {"id": "PVTI_item1", "project": {"id": "PVT_other"}},
                        ]
                    }
                }
            }
        }
    )

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_response

        client = GithubClient(credentials)
        item_id = client.get_project_item_id("owner", "repo", 42, "PVT_target")

    assert item_id is None


def test_update_project_field_value():
    """Test updating a project field value."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = mock_graphql_response(
        data={"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": "PVTI_item"}}}
    )

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_response

        client = GithubClient(credentials)
        success = client.update_project_field_value(
            project_id="PVT_proj",
            item_id="PVTI_item",
            field_id="PVTF_status",
            value="opt_in_progress",
            value_type="singleSelectOptionId",
        )

    assert success is True


def test_update_project_field_value_failure():
    """Test update_project_field_value handles errors."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = mock_graphql_response(
        data=None,
        errors=[{"message": "Field not found"}]
    )

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_response

        client = GithubClient(credentials)
        success = client.update_project_field_value(
            project_id="PVT_proj",
            item_id="PVTI_item",
            field_id="PVTF_invalid",
            value="some_value",
        )

    assert success is False
