"""Tests for GitHub API client and parser."""

import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, patch, MagicMock
import requests

from memory.common.github import (
    GithubCredentials,
    GithubClient,
    GithubComment,
    parse_github_date,
    compute_content_hash,
)


# =============================================================================
# Helper for creating mock responses
# =============================================================================


def mock_graphql_response(data=None, errors=None):
    """Create a mock response for GraphQL requests."""
    response = Mock()
    response.json.return_value = {"data": data, "errors": errors} if errors else {"data": data}
    response.headers = {"X-RateLimit-Remaining": "4999"}
    response.raise_for_status = Mock()
    return response


def mock_rest_response(data=None, status_code=200):
    """Create a mock response for REST requests."""
    response = Mock()
    response.json.return_value = data or {}
    response.headers = {"X-RateLimit-Remaining": "4999"}
    response.raise_for_status = Mock()
    response.status_code = status_code
    response.ok = status_code == 200
    response.text = ""
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
# Tests for fetch_issues (GraphQL)
# =============================================================================


def test_fetch_issues_basic():
    """Test fetching issues via GraphQL."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    graphql_data = {
        "repository": {
            "issues": {
                "nodes": [
                    {
                        "number": 1,
                        "title": "Test Issue",
                        "body": "Issue body",
                        "state": "OPEN",
                        "author": {"login": "testuser"},
                        "labels": {"nodes": [{"name": "bug"}]},
                        "assignees": {"nodes": [{"login": "dev1"}]},
                        "milestone": {"number": 1},
                        "createdAt": "2024-01-01T00:00:00Z",
                        "updatedAt": "2024-01-02T00:00:00Z",
                        "closedAt": None,
                        "comments": {
                            "nodes": [
                                {
                                    "databaseId": 100,
                                    "author": {"login": "commenter"},
                                    "body": "A comment",
                                    "createdAt": "2024-01-01T12:00:00Z",
                                    "updatedAt": "2024-01-01T12:00:00Z",
                                }
                            ],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        },
                    }
                ],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        }
    }

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_graphql_response(graphql_data)
        client = GithubClient(credentials)
        issues = list(client.fetch_issues("owner", "repo"))

    assert len(issues) == 1
    issue = issues[0]
    assert issue["number"] == 1
    assert issue["title"] == "Test Issue"
    assert issue["kind"] == "issue"
    assert issue["state"] == "open"  # Lowercased from OPEN
    assert issue["author"] == "testuser"
    assert issue["labels"] == ["bug"]
    assert issue["assignees"] == ["dev1"]
    assert issue["milestone_number"] == 1
    assert len(issue["comments"]) == 1
    assert issue["comments"][0]["author"] == "commenter"


def test_fetch_issues_pagination():
    """Test that fetch_issues handles GraphQL pagination."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    # First page
    page1_data = {
        "repository": {
            "issues": {
                "nodes": [
                    {
                        "number": 1,
                        "title": "Issue 1",
                        "body": "Body",
                        "state": "OPEN",
                        "author": {"login": "user"},
                        "labels": {"nodes": []},
                        "assignees": {"nodes": []},
                        "milestone": None,
                        "createdAt": "2024-01-01T00:00:00Z",
                        "updatedAt": "2024-01-02T00:00:00Z",
                        "closedAt": None,
                        "comments": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}},
                    }
                ],
                "pageInfo": {"hasNextPage": True, "endCursor": "cursor1"},
            }
        }
    }

    # Second page
    page2_data = {
        "repository": {
            "issues": {
                "nodes": [
                    {
                        "number": 2,
                        "title": "Issue 2",
                        "body": "Body",
                        "state": "OPEN",
                        "author": {"login": "user"},
                        "labels": {"nodes": []},
                        "assignees": {"nodes": []},
                        "milestone": None,
                        "createdAt": "2024-01-01T00:00:00Z",
                        "updatedAt": "2024-01-02T00:00:00Z",
                        "closedAt": None,
                        "comments": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}},
                    }
                ],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        }
    }

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.side_effect = [
            mock_graphql_response(page1_data),
            mock_graphql_response(page2_data),
        ]
        client = GithubClient(credentials)
        issues = list(client.fetch_issues("owner", "repo"))

    assert len(issues) == 2
    assert issues[0]["number"] == 1
    assert issues[1]["number"] == 2


def test_fetch_issues_handles_graphql_error():
    """Test that fetch_issues handles GraphQL errors gracefully."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_graphql_response(
            data=None, errors=[{"message": "Not found"}]
        )
        client = GithubClient(credentials)
        issues = list(client.fetch_issues("owner", "repo"))

    assert len(issues) == 0


def test_fetch_issues_with_state_filter():
    """Test fetching issues with state filter."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    graphql_data = {
        "repository": {
            "issues": {
                "nodes": [
                    {
                        "number": 1,
                        "title": "Closed Issue",
                        "body": "Body",
                        "state": "CLOSED",
                        "author": {"login": "user"},
                        "labels": {"nodes": []},
                        "assignees": {"nodes": []},
                        "milestone": None,
                        "createdAt": "2024-01-01T00:00:00Z",
                        "updatedAt": "2024-01-02T00:00:00Z",
                        "closedAt": "2024-01-02T00:00:00Z",
                        "comments": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}},
                    }
                ],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        }
    }

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_graphql_response(graphql_data)
        client = GithubClient(credentials)
        issues = list(client.fetch_issues("owner", "repo", state="closed"))

    # Verify the query includes the state filter
    call_args = mock_post.call_args
    query = call_args.kwargs.get("json", {}).get("query", "")
    assert "CLOSED" in query

    assert len(issues) == 1
    assert issues[0]["state"] == "closed"


# =============================================================================
# Tests for fetch_prs (GraphQL)
# =============================================================================


def test_fetch_prs_basic():
    """Test fetching PRs via GraphQL."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    graphql_data = {
        "repository": {
            "pullRequests": {
                "nodes": [
                    {
                        "number": 10,
                        "title": "Add feature",
                        "body": "PR body",
                        "state": "OPEN",
                        "author": {"login": "contributor"},
                        "labels": {"nodes": [{"name": "enhancement"}]},
                        "assignees": {"nodes": [{"login": "reviewer"}]},
                        "milestone": None,
                        "createdAt": "2024-01-05T00:00:00Z",
                        "updatedAt": "2024-01-06T00:00:00Z",
                        "closedAt": None,
                        "mergedAt": None,
                        "additions": 100,
                        "deletions": 50,
                        "changedFiles": 5,
                        "comments": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}},
                        "reviews": {"nodes": []},
                        "reviewThreads": {"nodes": []},
                        "files": {
                            "nodes": [
                                {"path": "test.py", "additions": 100, "deletions": 50, "changeType": "ADDED"}
                            ],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        },
                    }
                ],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        }
    }

    def mock_request(method):
        """Create a mock that handles both GET and POST."""
        def handler(url, **kwargs):
            # POST is GraphQL
            if method == "post":
                return mock_graphql_response(graphql_data)
            # GET is for the diff
            if "Accept" in kwargs.get("headers", {}) and "diff" in kwargs["headers"]["Accept"]:
                resp = mock_rest_response()
                resp.ok = True
                resp.text = "+100 lines added\n-50 lines removed"
                return resp
            return mock_rest_response()
        return handler

    with patch.object(requests.Session, "post", side_effect=mock_request("post")):
        with patch.object(requests.Session, "get", side_effect=mock_request("get")):
            client = GithubClient(credentials)
            prs = list(client.fetch_prs("owner", "repo"))

    assert len(prs) == 1
    pr = prs[0]
    assert pr["number"] == 10
    assert pr["title"] == "Add feature"
    assert pr["kind"] == "pr"
    assert pr["state"] == "open"
    assert pr["pr_data"] is not None
    assert pr["pr_data"]["additions"] == 100
    assert pr["pr_data"]["deletions"] == 50


def test_fetch_prs_merged():
    """Test fetching merged PR via GraphQL."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    graphql_data = {
        "repository": {
            "pullRequests": {
                "nodes": [
                    {
                        "number": 20,
                        "title": "Merged PR",
                        "body": "Body",
                        "state": "MERGED",
                        "author": {"login": "user"},
                        "labels": {"nodes": []},
                        "assignees": {"nodes": []},
                        "milestone": None,
                        "createdAt": "2024-01-01T00:00:00Z",
                        "updatedAt": "2024-01-10T00:00:00Z",
                        "closedAt": "2024-01-10T00:00:00Z",
                        "mergedAt": "2024-01-10T00:00:00Z",
                        "additions": 10,
                        "deletions": 5,
                        "changedFiles": 1,
                        "comments": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}},
                        "reviews": {"nodes": []},
                        "reviewThreads": {"nodes": []},
                        "files": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}},
                    }
                ],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        }
    }

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_graphql_response(graphql_data)
        with patch.object(requests.Session, "get") as mock_get:
            mock_get.return_value = mock_rest_response()
            client = GithubClient(credentials)
            prs = list(client.fetch_prs("owner", "repo"))

    pr = prs[0]
    # MERGED state maps to "closed" for REST compatibility
    assert pr["state"] == "closed"
    assert pr["merged_at"] == datetime(2024, 1, 10, 0, 0, 0, tzinfo=timezone.utc)


def test_fetch_prs_stops_at_since():
    """Test that PR fetching stops when reaching older items."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    graphql_data = {
        "repository": {
            "pullRequests": {
                "nodes": [
                    {
                        "number": 30,
                        "title": "Recent PR",
                        "body": "Body",
                        "state": "OPEN",
                        "author": {"login": "user"},
                        "labels": {"nodes": []},
                        "assignees": {"nodes": []},
                        "milestone": None,
                        "createdAt": "2024-01-20T00:00:00Z",
                        "updatedAt": "2024-01-20T00:00:00Z",
                        "closedAt": None,
                        "mergedAt": None,
                        "additions": 1,
                        "deletions": 1,
                        "changedFiles": 1,
                        "comments": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}},
                        "reviews": {"nodes": []},
                        "reviewThreads": {"nodes": []},
                        "files": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}},
                    },
                    {
                        "number": 29,
                        "title": "Old PR",
                        "body": "Body",
                        "state": "OPEN",
                        "author": {"login": "user"},
                        "labels": {"nodes": []},
                        "assignees": {"nodes": []},
                        "milestone": None,
                        "createdAt": "2024-01-01T00:00:00Z",
                        "updatedAt": "2024-01-01T00:00:00Z",  # Older than since
                        "closedAt": None,
                        "mergedAt": None,
                        "additions": 1,
                        "deletions": 1,
                        "changedFiles": 1,
                        "comments": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}},
                        "reviews": {"nodes": []},
                        "reviewThreads": {"nodes": []},
                        "files": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}},
                    },
                ],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        }
    }

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_graphql_response(graphql_data)
        with patch.object(requests.Session, "get") as mock_get:
            mock_get.return_value = mock_rest_response()
            client = GithubClient(credentials)
            since = datetime(2024, 1, 15, tzinfo=timezone.utc)
            prs = list(client.fetch_prs("owner", "repo", since=since))

    # Should only get the recent PR, stop at the old one
    assert len(prs) == 1
    assert prs[0]["number"] == 30


# =============================================================================
# Tests for fetch_milestones (GraphQL)
# =============================================================================


def test_fetch_milestones_basic():
    """Test fetching milestones via GraphQL."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    graphql_data = {
        "repository": {
            "milestones": {
                "nodes": [
                    {
                        "id": "M_123",
                        "number": 1,
                        "title": "v1.0",
                        "description": "First release",
                        "state": "OPEN",
                        "dueOn": "2024-06-01T00:00:00Z",
                        "createdAt": "2024-01-01T00:00:00Z",
                        "updatedAt": "2024-01-15T00:00:00Z",
                        "closedAt": None,
                    }
                ],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        }
    }

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_graphql_response(graphql_data)
        client = GithubClient(credentials)
        milestones = list(client.fetch_milestones("owner", "repo"))

    assert len(milestones) == 1
    ms = milestones[0]
    assert ms["number"] == 1
    assert ms["title"] == "v1.0"
    assert ms["state"] == "open"  # Lowercased from OPEN
    assert ms["description"] == "First release"


def test_fetch_milestone_single():
    """Test fetching a single milestone via GraphQL."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    graphql_data = {
        "repository": {
            "milestone": {
                "id": "M_123",
                "number": 1,
                "title": "v1.0",
                "description": "First release",
                "state": "OPEN",
                "dueOn": "2024-06-01T00:00:00Z",
                "createdAt": "2024-01-01T00:00:00Z",
                "updatedAt": "2024-01-15T00:00:00Z",
                "closedAt": None,
            }
        }
    }

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_graphql_response(graphql_data)
        client = GithubClient(credentials)
        ms = client.fetch_milestone("owner", "repo", 1)

    assert ms is not None
    assert ms["number"] == 1
    assert ms["title"] == "v1.0"


def test_fetch_milestone_not_found():
    """Test fetching a non-existent milestone."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    graphql_data = {"repository": {"milestone": None}}

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_graphql_response(graphql_data)
        client = GithubClient(credentials)
        ms = client.fetch_milestone("owner", "repo", 999)

    assert ms is None


# =============================================================================
# Tests for fetch_pr_diff (REST - requires special Accept header)
# =============================================================================


def test_fetch_pr_diff():
    """Test fetching PR diff via REST (GraphQL doesn't support this)."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = Mock()
    mock_response.ok = True
    mock_response.text = """diff --git a/file.py b/file.py
+++ b/file.py
@@ -1,3 +1,5 @@
+new line 1
+new line 2
 existing line"""
    mock_response.headers = {"X-RateLimit-Remaining": "4999"}

    with patch.object(requests.Session, "get") as mock_get:
        mock_get.return_value = mock_response
        client = GithubClient(credentials)
        diff = client.fetch_pr_diff("owner", "repo", 10)

    assert diff is not None
    assert "diff --git" in diff
    # Verify the Accept header was set correctly
    call_args = mock_get.call_args
    assert call_args.kwargs.get("headers", {}).get("Accept") == "application/vnd.github.diff"


def test_fetch_pr_diff_failure():
    """Test PR diff fetch failure returns None."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    mock_response = Mock()
    mock_response.ok = False
    mock_response.headers = {"X-RateLimit-Remaining": "4999"}

    with patch.object(requests.Session, "get") as mock_get:
        mock_get.return_value = mock_response
        client = GithubClient(credentials)
        diff = client.fetch_pr_diff("owner", "repo", 10)

    assert diff is None


# =============================================================================
# Tests for project fields (GraphQL)
# =============================================================================


def test_fetch_project_fields():
    """Test fetching GitHub Projects v2 fields."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    graphql_data = {
        "repository": {
            "issue": {
                "projectItems": {
                    "nodes": [
                        {
                            "project": {"title": "My Project"},
                            "fieldValues": {
                                "nodes": [
                                    {
                                        "name": "In Progress",
                                        "field": {"name": "Status"},
                                    }
                                ]
                            },
                        }
                    ]
                }
            }
        }
    }

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_graphql_response(graphql_data)
        client = GithubClient(credentials)
        fields = client.fetch_project_fields("owner", "repo", 1)

    assert fields is not None
    assert "My Project.Status" in fields
    assert fields["My Project.Status"] == "In Progress"


def test_fetch_project_fields_not_in_project():
    """Test fetching project fields for issue not in any project."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    graphql_data = {"repository": {"issue": {"projectItems": {"nodes": []}}}}

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_graphql_response(graphql_data)
        client = GithubClient(credentials)
        fields = client.fetch_project_fields("owner", "repo", 1)

    assert fields is None


def test_fetch_project_fields_graphql_error():
    """Test fetching project fields with GraphQL error."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_graphql_response(
            data=None, errors=[{"message": "Some error"}]
        )
        client = GithubClient(credentials)
        fields = client.fetch_project_fields("owner", "repo", 1)

    assert fields is None


# =============================================================================
# Tests for list_repos (REST - no direct GraphQL equivalent)
# =============================================================================


def test_list_repos_pat_auth():
    """Test listing repos with PAT authentication."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    page1 = [
        {
            "owner": {"login": "testuser"},
            "name": "repo1",
            "full_name": "testuser/repo1",
            "description": "Test repo",
            "private": False,
            "html_url": "https://github.com/testuser/repo1",
        }
    ]

    with patch.object(requests.Session, "get") as mock_get:
        mock_get.side_effect = [
            mock_rest_response(page1),
            mock_rest_response([]),  # Empty page to stop
        ]
        client = GithubClient(credentials)
        repos = list(client.list_repos(max_repos=10))

    assert len(repos) == 1
    assert repos[0]["name"] == "repo1"
    assert repos[0]["owner"] == "testuser"


# =============================================================================
# Tests for GraphQL helper methods
# =============================================================================


def test_get_repository_id():
    """Test getting repository GraphQL node ID."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    graphql_data = {"repository": {"id": "R_123abc"}}

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_graphql_response(graphql_data)
        client = GithubClient(credentials)
        repo_id = client.get_repository_id("owner", "repo")

    assert repo_id == "R_123abc"


def test_get_issue_node_id():
    """Test getting issue GraphQL node ID."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    graphql_data = {"repository": {"issue": {"id": "I_456def"}}}

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_graphql_response(graphql_data)
        client = GithubClient(credentials)
        issue_id = client.get_issue_node_id("owner", "repo", 1)

    assert issue_id == "I_456def"


def test_item_exists_issue():
    """Test checking if an issue exists."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    graphql_data = {"repository": {"issue": {"id": "I_456def"}}}

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_graphql_response(graphql_data)
        client = GithubClient(credentials)
        exists = client.item_exists("owner", "repo", 1, "issue")

    assert exists is True


def test_item_exists_issue_not_found():
    """Test checking an issue that doesn't exist."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    graphql_data = {"repository": {"issue": None}}

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_graphql_response(graphql_data)
        client = GithubClient(credentials)
        exists = client.item_exists("owner", "repo", 999, "issue")

    assert exists is False


def test_item_exists_pr():
    """Test checking if a PR exists."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    graphql_data = {"repository": {"pullRequest": {"id": "PR_789xyz"}}}

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_graphql_response(graphql_data)
        client = GithubClient(credentials)
        exists = client.item_exists("owner", "repo", 42, "pr")

    assert exists is True


def test_item_exists_pr_not_found():
    """Test checking a PR that doesn't exist."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    graphql_data = {"repository": {"pullRequest": None}}

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_graphql_response(graphql_data)
        client = GithubClient(credentials)
        exists = client.item_exists("owner", "repo", 999, "pr")

    assert exists is False


def test_items_exist_batch():
    """Test batch checking if multiple issues/PRs exist."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    # Response with aliases for each item
    graphql_data = {
        "repository": {
            "item_1_issue": {"id": "I_123"},
            "item_2_pr": {"id": "PR_456"},
            "item_3_issue": None,  # Doesn't exist
        }
    }

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_graphql_response(graphql_data)
        client = GithubClient(credentials)
        results = client.items_exist(
            "owner", "repo", [(1, "issue"), (2, "pr"), (3, "issue")]
        )

    assert results[(1, "issue")] is True
    assert results[(2, "pr")] is True
    assert results[(3, "issue")] is False


def test_items_exist_empty():
    """Test batch check with empty list returns empty dict."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    with patch.object(requests.Session, "post") as mock_post:
        client = GithubClient(credentials)
        results = client.items_exist("owner", "repo", [])

    assert results == {}
    mock_post.assert_not_called()


def test_items_exist_api_error():
    """Test batch check when API returns error."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_graphql_response(None, errors=[{"message": "Error"}])
        client = GithubClient(credentials)
        results = client.items_exist("owner", "repo", [(1, "issue")])

    # Should return False for items when API errors
    assert results[(1, "issue")] is False


def test_get_label_ids():
    """Test resolving label names to node IDs."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    graphql_data = {
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

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_graphql_response(graphql_data)
        client = GithubClient(credentials)
        ids = client.get_label_ids("owner", "repo", ["bug", "enhancement"])

    assert len(ids) == 2
    assert "LA_1" in ids
    assert "LA_2" in ids


# =============================================================================
# Tests for create/update issue (GraphQL mutations)
# =============================================================================


def test_create_issue_graphql():
    """Test creating an issue via GraphQL mutation."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    graphql_data = {
        "createIssue": {
            "issue": {
                "id": "I_new123",
                "number": 42,
                "url": "https://github.com/owner/repo/issues/42",
                "title": "New Issue",
                "state": "OPEN",
            }
        }
    }

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_graphql_response(graphql_data)
        client = GithubClient(credentials)
        result = client.create_issue_graphql("R_123", "New Issue", body="Issue body")

    assert result is not None
    assert result["number"] == 42
    assert result["title"] == "New Issue"


def test_update_issue_graphql():
    """Test updating an issue via GraphQL mutation."""
    credentials = GithubCredentials(auth_type="pat", access_token="token")

    graphql_data = {
        "updateIssue": {
            "issue": {
                "id": "I_123",
                "number": 1,
                "url": "https://github.com/owner/repo/issues/1",
                "title": "Updated Title",
                "state": "CLOSED",
            }
        }
    }

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.return_value = mock_graphql_response(graphql_data)
        client = GithubClient(credentials)
        result = client.update_issue_graphql("I_123", title="Updated Title", state="closed")

    assert result is not None
    assert result["title"] == "Updated Title"
    assert result["state"] == "CLOSED"
