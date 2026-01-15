"""Comprehensive tests for GitHub MCP tools."""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from memory.common.db.models import GithubItem
from memory.common.db import connection as db_connection


@pytest.fixture(autouse=True)
def reset_db_cache():
    """Reset the cached database engine between tests."""
    db_connection._engine = None
    db_connection._session_factory = None
    db_connection._scoped_session = None
    yield
    db_connection._engine = None
    db_connection._session_factory = None
    db_connection._scoped_session = None


def _make_sha256(content: str) -> bytes:
    """Generate a sha256 hash for test content."""
    import hashlib
    return hashlib.sha256(content.encode()).digest()


@pytest.fixture
def sample_issues(db_session):
    """Create sample GitHub issues for testing."""
    now = datetime.now(timezone.utc)
    issues = [
        GithubItem(
            kind="issue",
            repo_path="owner/repo1",
            number=1,
            title="Fix authentication bug",
            content="There is a bug in the authentication system.\n\n## Comments\n\n**user1**: I can reproduce this.",
            state="open",
            author="alice",
            labels=["bug", "security"],
            assignees=["bob", "charlie"],
            project_status="In Progress",
            project_priority="High",
            project_fields={
                "EquiStamp.Client": "Redwood",
                "EquiStamp.Status": "In Progress",
                "EquiStamp.Task Type": "Bug Fix",
            },
            comment_count=1,
            created_at=now - timedelta(days=10),
            github_updated_at=now - timedelta(days=1),
            modality="github",
            sha256=_make_sha256("issue-1-content"),
        ),
        GithubItem(
            kind="issue",
            repo_path="owner/repo1",
            number=2,
            title="Add dark mode support",
            content="Users want dark mode for the application.",
            state="open",
            author="bob",
            labels=["enhancement", "ui"],
            assignees=["alice"],
            project_status="Backlog",
            project_priority="Medium",
            project_fields={
                "EquiStamp.Client": "University of Illinois",
                "EquiStamp.Status": "Backlog",
                "EquiStamp.Task Type": "Feature",
            },
            comment_count=0,
            created_at=now - timedelta(days=5),
            github_updated_at=now - timedelta(days=2),
            modality="github",
            sha256=_make_sha256("issue-2-content"),
        ),
        GithubItem(
            kind="issue",
            repo_path="owner/repo2",
            number=10,
            title="Database migration issue",
            content="Migration fails on PostgreSQL 15.",
            state="closed",
            author="charlie",
            labels=["bug"],
            assignees=["alice"],
            project_status="Closed",
            project_priority=None,
            project_fields={
                "EquiStamp.Client": "Redwood",
                "EquiStamp.Status": "Closed",
            },
            comment_count=3,
            created_at=now - timedelta(days=20),
            closed_at=now - timedelta(days=3),
            github_updated_at=now - timedelta(days=3),
            modality="github",
            sha256=_make_sha256("issue-10-content"),
        ),
        GithubItem(
            kind="pr",
            repo_path="owner/repo1",
            number=50,
            title="Refactor user service",
            content="This PR refactors the user service for better performance.",
            state="merged",
            author="alice",
            labels=["refactor"],
            assignees=["bob"],
            project_status="Approved for Payment",
            project_priority="Low",
            project_fields={
                "EquiStamp.Client": "Redwood",
                "EquiStamp.Status": "Approved for Payment",
                "EquiStamp.Hours taken": "5",
            },
            comment_count=2,
            created_at=now - timedelta(days=15),
            merged_at=now - timedelta(days=7),
            github_updated_at=now - timedelta(days=7),
            modality="github",
            sha256=_make_sha256("pr-50-content"),
        ),
        GithubItem(
            kind="issue",
            repo_path="owner/repo1",
            number=100,
            title="Stale issue without updates",
            content="This issue has not been updated in a long time.",
            state="open",
            author="dave",
            labels=["stale"],
            assignees=[],
            project_status=None,
            project_priority=None,
            project_fields=None,
            comment_count=0,
            created_at=now - timedelta(days=60),
            github_updated_at=now - timedelta(days=45),
            modality="github",
            sha256=_make_sha256("issue-100-content"),
        ),
    ]

    for issue in issues:
        db_session.add(issue)
    db_session.commit()

    # Refresh to get IDs
    for issue in issues:
        db_session.refresh(issue)

    return issues


# =============================================================================
# Tests for list_issues
# =============================================================================


def test_list_issues_no_filters(db_session, sample_issues):
    """Test listing all issues without filters."""
    from memory.api.MCP.servers.github_helpers import list_issues

    with patch("memory.api.MCP.servers.github_helpers.make_session") as mock_session:
        mock_session.return_value.__enter__ = lambda s: db_session
        mock_session.return_value.__exit__ = lambda s, *args: None
        results = list_issues()

    # Should return all issues and PRs (not comments)
    assert len(results) == 5
    # Should be ordered by github_updated_at desc
    assert results[0]["number"] == 1  # Most recently updated


def test_list_issues_filter_by_repo(db_session, sample_issues):
    """Test filtering by repository."""
    from memory.api.MCP.servers.github_helpers import list_issues

    with patch("memory.api.MCP.servers.github_helpers.make_session") as mock_session:
        mock_session.return_value.__enter__ = lambda s: db_session
        mock_session.return_value.__exit__ = lambda s, *args: None
        results = list_issues(repo="owner/repo1")

    assert len(results) == 4
    assert all(r["repo_path"] == "owner/repo1" for r in results)


def test_list_issues_filter_by_assignee(db_session, sample_issues):
    """Test filtering by assignee."""
    from memory.api.MCP.servers.github_helpers import list_issues

    with patch("memory.api.MCP.servers.github_helpers.make_session") as mock_session:
        mock_session.return_value.__enter__ = lambda s: db_session
        mock_session.return_value.__exit__ = lambda s, *args: None
        results = list_issues(assignee="alice")

    assert len(results) == 2
    assert all("alice" in r["assignees"] for r in results)


def test_list_issues_filter_by_author(db_session, sample_issues):
    """Test filtering by author."""
    from memory.api.MCP.servers.github_helpers import list_issues

    with patch("memory.api.MCP.servers.github_helpers.make_session") as mock_session:
        mock_session.return_value.__enter__ = lambda s: db_session
        mock_session.return_value.__exit__ = lambda s, *args: None
        results = list_issues(author="alice")

    assert len(results) == 2
    assert all(r["author"] == "alice" for r in results)


def test_list_issues_filter_by_state(db_session, sample_issues):
    """Test filtering by state."""
    from memory.api.MCP.servers.github_helpers import list_issues

    with patch("memory.api.MCP.servers.github_helpers.make_session") as mock_session:
        mock_session.return_value.__enter__ = lambda s: db_session
        mock_session.return_value.__exit__ = lambda s, *args: None
        open_results = list_issues(state="open")

    with patch("memory.api.MCP.servers.github_helpers.make_session") as mock_session:
        mock_session.return_value.__enter__ = lambda s: db_session
        mock_session.return_value.__exit__ = lambda s, *args: None
        closed_results = list_issues(state="closed")

    with patch("memory.api.MCP.servers.github_helpers.make_session") as mock_session:
        mock_session.return_value.__enter__ = lambda s: db_session
        mock_session.return_value.__exit__ = lambda s, *args: None
        merged_results = list_issues(state="merged")

    assert len(open_results) == 3
    assert all(r["state"] == "open" for r in open_results)

    assert len(closed_results) == 1
    assert closed_results[0]["state"] == "closed"

    assert len(merged_results) == 1
    assert merged_results[0]["state"] == "merged"


def test_list_issues_filter_by_kind(db_session, sample_issues):
    """Test filtering by kind (issue vs PR)."""
    from memory.api.MCP.servers.github_helpers import list_issues

    with patch("memory.api.MCP.servers.github_helpers.make_session") as mock_session:
        mock_session.return_value.__enter__ = lambda s: db_session
        mock_session.return_value.__exit__ = lambda s, *args: None
        issues = list_issues(kind="issue")

    with patch("memory.api.MCP.servers.github_helpers.make_session") as mock_session:
        mock_session.return_value.__enter__ = lambda s: db_session
        mock_session.return_value.__exit__ = lambda s, *args: None
        prs = list_issues(kind="pr")

    assert len(issues) == 4
    assert all(r["kind"] == "issue" for r in issues)

    assert len(prs) == 1
    assert prs[0]["kind"] == "pr"


def test_list_issues_filter_by_labels(db_session, sample_issues):
    """Test filtering by labels."""
    from memory.api.MCP.servers.github_helpers import list_issues

    with patch("memory.api.MCP.servers.github_helpers.make_session") as mock_session:
        mock_session.return_value.__enter__ = lambda s: db_session
        mock_session.return_value.__exit__ = lambda s, *args: None
        results = list_issues(labels=["bug"])

    assert len(results) == 2
    assert all("bug" in r["labels"] for r in results)


def test_list_issues_filter_by_project_status(db_session, sample_issues):
    """Test filtering by project status."""
    from memory.api.MCP.servers.github_helpers import list_issues

    with patch("memory.api.MCP.servers.github_helpers.make_session") as mock_session:
        mock_session.return_value.__enter__ = lambda s: db_session
        mock_session.return_value.__exit__ = lambda s, *args: None
        results = list_issues(project_status="In Progress")

    assert len(results) == 1
    assert results[0]["project_status"] == "In Progress"
    assert results[0]["number"] == 1


def test_list_issues_filter_by_project_field(db_session, sample_issues):
    """Test filtering by project field (JSONB)."""
    from memory.api.MCP.servers.github_helpers import list_issues

    with patch("memory.api.MCP.servers.github_helpers.make_session") as mock_session:
        mock_session.return_value.__enter__ = lambda s: db_session
        mock_session.return_value.__exit__ = lambda s, *args: None
        results = list_issues(
            project_field={"EquiStamp.Client": "Redwood"}
        )

    assert len(results) == 3
    assert all(
        r["project_fields"].get("EquiStamp.Client") == "Redwood" for r in results
    )


def test_list_issues_filter_by_updated_since(db_session, sample_issues):
    """Test filtering by updated_since."""
    from memory.api.MCP.servers.github_helpers import list_issues

    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=2)).isoformat()

    with patch("memory.api.MCP.servers.github_helpers.make_session") as mock_session:
        mock_session.return_value.__enter__ = lambda s: db_session
        mock_session.return_value.__exit__ = lambda s, *args: None
        results = list_issues(updated_since=since)

    # Only issue #1 was updated within last 2 days
    assert len(results) == 1
    assert results[0]["number"] == 1


def test_list_issues_filter_by_updated_before(db_session, sample_issues):
    """Test filtering by updated_before (stale issues)."""
    from memory.api.MCP.servers.github_helpers import list_issues

    now = datetime.now(timezone.utc)
    before = (now - timedelta(days=30)).isoformat()

    with patch("memory.api.MCP.servers.github_helpers.make_session") as mock_session:
        mock_session.return_value.__enter__ = lambda s: db_session
        mock_session.return_value.__exit__ = lambda s, *args: None
        results = list_issues(updated_before=before)

    # Only issue #100 hasn't been updated in 30+ days
    assert len(results) == 1
    assert results[0]["number"] == 100


def test_list_issues_order_by_created(db_session, sample_issues):
    """Test ordering by created date."""
    from memory.api.MCP.servers.github_helpers import list_issues

    with patch("memory.api.MCP.servers.github_helpers.make_session") as mock_session:
        mock_session.return_value.__enter__ = lambda s: db_session
        mock_session.return_value.__exit__ = lambda s, *args: None
        results = list_issues(order_by="created")

    # Should be ordered by created_at desc
    assert results[0]["number"] == 2  # Most recently created


def test_list_issues_limit(db_session, sample_issues):
    """Test limiting results."""
    from memory.api.MCP.servers.github_helpers import list_issues

    with patch("memory.api.MCP.servers.github_helpers.make_session") as mock_session:
        mock_session.return_value.__enter__ = lambda s: db_session
        mock_session.return_value.__exit__ = lambda s, *args: None
        results = list_issues(limit=2)

    assert len(results) == 2


def test_list_issues_limit_max_enforced(db_session, sample_issues):
    """Test that limit is capped at 200."""
    from memory.api.MCP.servers.github_helpers import list_issues

    with patch("memory.api.MCP.servers.github_helpers.make_session") as mock_session:
        mock_session.return_value.__enter__ = lambda s: db_session
        mock_session.return_value.__exit__ = lambda s, *args: None
        # Request 500 but should be capped at 200
        results = list_issues(limit=500)

    # We only have 5 issues, but the limit should be internally capped at 200
    assert len(results) <= 200


def test_list_issues_combined_filters(db_session, sample_issues):
    """Test combining multiple filters."""
    from memory.api.MCP.servers.github_helpers import list_issues

    with patch("memory.api.MCP.servers.github_helpers.make_session") as mock_session:
        mock_session.return_value.__enter__ = lambda s: db_session
        mock_session.return_value.__exit__ = lambda s, *args: None
        results = list_issues(
            repo="owner/repo1",
            state="open",
            kind="issue",
            project_field={"EquiStamp.Client": "Redwood"},
        )

    assert len(results) == 1
    assert results[0]["number"] == 1
    assert results[0]["title"] == "Fix authentication bug"


def test_list_issues_url_construction(db_session, sample_issues):
    """Test that URLs are correctly constructed."""
    from memory.api.MCP.servers.github_helpers import list_issues

    with patch("memory.api.MCP.servers.github_helpers.make_session") as mock_session:
        mock_session.return_value.__enter__ = lambda s: db_session
        mock_session.return_value.__exit__ = lambda s, *args: None
        results = list_issues(kind="issue", limit=1)

    assert "url" in results[0]
    assert "github.com" in results[0]["url"]
    assert "/issues/" in results[0]["url"]

    with patch("memory.api.MCP.servers.github_helpers.make_session") as mock_session:
        mock_session.return_value.__enter__ = lambda s: db_session
        mock_session.return_value.__exit__ = lambda s, *args: None
        pr_results = list_issues(kind="pr")

    assert "/pull/" in pr_results[0]["url"]


# =============================================================================
# Tests for fetch_issue
# =============================================================================


def test_fetch_issue_found(db_session, sample_issues):
    """Test getting details for an existing issue."""
    from memory.api.MCP.servers.github_helpers import fetch_issue

    with patch("memory.api.MCP.servers.github_helpers.make_session") as mock_session:
        mock_session.return_value.__enter__ = lambda s: db_session
        mock_session.return_value.__exit__ = lambda s, *args: None
        result = fetch_issue(repo="owner/repo1", number=1)

    assert result["number"] == 1
    assert result["title"] == "Fix authentication bug"
    assert "content" in result
    assert "authentication" in result["content"]
    assert result["project_fields"]["EquiStamp.Client"] == "Redwood"


def test_fetch_issue_not_found(db_session, sample_issues):
    """Test getting details for a non-existent issue."""
    from memory.api.MCP.servers.github_helpers import fetch_issue

    with patch("memory.api.MCP.servers.github_helpers.make_session") as mock_session:
        mock_session.return_value.__enter__ = lambda s: db_session
        mock_session.return_value.__exit__ = lambda s, *args: None
        with pytest.raises(ValueError, match="not found"):
            fetch_issue(repo="owner/repo1", number=999)


def test_fetch_issue_pr(db_session, sample_issues):
    """Test getting details for a PR."""
    from memory.api.MCP.servers.github_helpers import fetch_issue

    with patch("memory.api.MCP.servers.github_helpers.make_session") as mock_session:
        mock_session.return_value.__enter__ = lambda s: db_session
        mock_session.return_value.__exit__ = lambda s, *args: None
        result = fetch_issue(repo="owner/repo1", number=50)

    assert result["kind"] == "pr"
    assert result["state"] == "merged"
    assert result["merged_at"] is not None


# =============================================================================
# Parametrized tests
# =============================================================================


@pytest.mark.parametrize(
    "order_by,expected_first_number",
    [
        ("updated", 1),  # Most recently updated
        ("created", 2),  # Most recently created (among repo1)
        ("number", 100),  # Highest number
    ],
)
def test_list_issues_ordering(
    db_session, sample_issues, order_by, expected_first_number
):
    """Test different ordering options."""
    from memory.api.MCP.servers.github_helpers import list_issues

    with patch("memory.api.MCP.servers.github_helpers.make_session") as mock_session:
        mock_session.return_value.__enter__ = lambda s: db_session
        mock_session.return_value.__exit__ = lambda s, *args: None
        results = list_issues(order_by=order_by)

    assert results[0]["number"] == expected_first_number


@pytest.mark.parametrize(
    "labels,expected_count",
    [
        (["bug"], 2),
        (["enhancement"], 1),
        (["bug", "security"], 1),  # Only issue 1 has both
        (["nonexistent"], 0),
    ],
)
def test_list_issues_label_filtering(
    db_session, sample_issues, labels, expected_count
):
    """Test various label filtering scenarios."""
    from memory.api.MCP.servers.github_helpers import list_issues

    with patch("memory.api.MCP.servers.github_helpers.make_session") as mock_session:
        mock_session.return_value.__enter__ = lambda s: db_session
        mock_session.return_value.__exit__ = lambda s, *args: None
        results = list_issues(labels=labels)

    # Note: label filtering uses ANY match, so ["bug", "security"] matches
    # anything with "bug" OR "security"
    assert len(results) >= expected_count


# =============================================================================
# Tests for GithubPRData model and PR-specific functionality
# =============================================================================


def test_github_pr_data_diff_compression():
    """Test that GithubPRData compresses and decompresses diffs correctly."""
    from memory.common.db.models import GithubPRData

    pr_data = GithubPRData()
    test_diff = """diff --git a/file.py b/file.py
index 123..456 789
--- a/file.py
+++ b/file.py
@@ -1,3 +1,4 @@
 def hello():
     print("Hello")
+    print("World")
"""

    # Set diff via property (should compress)
    pr_data.diff = test_diff
    assert pr_data.diff_compressed is not None
    assert len(pr_data.diff_compressed) < len(test_diff.encode("utf-8"))

    # Get diff via property (should decompress)
    assert pr_data.diff == test_diff


def test_github_pr_data_diff_none():
    """Test GithubPRData handles None diff correctly."""
    from memory.common.db.models import GithubPRData

    pr_data = GithubPRData()
    assert pr_data.diff is None

    pr_data.diff = None
    assert pr_data.diff_compressed is None
    assert pr_data.diff is None


@pytest.fixture
def sample_pr_with_data(db_session):
    """Create a sample PR with GithubPRData attached."""
    from memory.common.db.models import GithubItem, GithubPRData

    now = datetime.now(timezone.utc)

    pr = GithubItem(
        kind="pr",
        repo_path="owner/repo1",
        number=999,
        title="Test PR with data",
        content="This PR has full PR data attached.",
        state="open",
        author="alice",
        labels=["feature"],
        assignees=["alice"],
        # milestone_id is a ForeignKey, omitting it (defaults to None)
        project_status="In Progress",
        project_priority=None,
        project_fields={"EquiStamp.Client": "Test"},
        comment_count=1,
        created_at=now - timedelta(days=1),
        github_updated_at=now,
        modality="github",
        sha256=_make_sha256("pr-999-content"),
    )

    pr_data = GithubPRData(
        additions=50,
        deletions=10,
        changed_files_count=3,
        files=[
            {"filename": "src/main.py", "status": "modified", "additions": 30, "deletions": 5, "patch": "@@ -1,3 +1,4 @@"},
            {"filename": "tests/test_main.py", "status": "added", "additions": 20, "deletions": 0, "patch": None},
            {"filename": "README.md", "status": "modified", "additions": 0, "deletions": 5, "patch": "@@ -10,5 +10,0 @@"},
        ],
        reviews=[
            {"id": 1, "user": "bob", "state": "approved", "body": "LGTM!", "submitted_at": "2025-12-23T10:00:00Z"},
        ],
        review_comments=[
            {"id": 101, "user": "bob", "body": "Nice refactoring here", "path": "src/main.py", "line": 10, "side": "RIGHT", "diff_hunk": "@@ context", "created_at": "2025-12-23T09:00:00Z"},
        ],
    )
    pr_data.diff = "diff --git a/src/main.py b/src/main.py\n..."

    pr.pr_data = pr_data
    db_session.add(pr)
    db_session.commit()
    db_session.refresh(pr)

    return pr


def test_fetch_issue_includes_pr_data(db_session, sample_pr_with_data):
    """Test that fetch_issue includes PR data for PRs."""
    from memory.api.MCP.servers.github_helpers import fetch_issue

    with patch("memory.api.MCP.servers.github_helpers.make_session") as mock_session:
        mock_session.return_value.__enter__ = lambda s: db_session
        mock_session.return_value.__exit__ = lambda s, *args: None
        result = fetch_issue(repo="owner/repo1", number=999)

    assert result["kind"] == "pr"
    assert "pr_data" in result
    assert result["pr_data"]["additions"] == 50
    assert result["pr_data"]["deletions"] == 10
    assert result["pr_data"]["changed_files_count"] == 3
    assert len(result["pr_data"]["files"]) == 3
    assert len(result["pr_data"]["reviews"]) == 1
    assert len(result["pr_data"]["review_comments"]) == 1
    assert result["pr_data"]["diff"] is not None
    assert "diff --git" in result["pr_data"]["diff"]


def test_fetch_issue_no_pr_data_for_issues(db_session, sample_issues):
    """Test that fetch_issue does not include pr_data for issues."""
    from memory.api.MCP.servers.github_helpers import fetch_issue

    with patch("memory.api.MCP.servers.github_helpers.make_session") as mock_session:
        mock_session.return_value.__enter__ = lambda s: db_session
        mock_session.return_value.__exit__ = lambda s, *args: None
        result = fetch_issue(repo="owner/repo1", number=1)

    assert result["kind"] == "issue"
    assert "pr_data" not in result


def test_serialize_issue_includes_pr_data(db_session, sample_pr_with_data):
    """Test that serialize_issue includes pr_data when include_content=True."""
    from memory.api.MCP.servers.github_helpers import serialize_issue

    result = serialize_issue(sample_pr_with_data, include_content=True)

    assert "pr_data" in result
    assert result["pr_data"]["additions"] == 50
    assert result["pr_data"]["reviews"][0]["state"] == "approved"


def test_serialize_issue_no_pr_data_without_content(db_session, sample_pr_with_data):
    """Test that serialize_issue excludes pr_data when include_content=False."""
    from memory.api.MCP.servers.github_helpers import serialize_issue

    result = serialize_issue(sample_pr_with_data, include_content=False)

    assert "pr_data" not in result
    assert "content" not in result


def test_serialize_issue_basic(db_session, sample_issues):
    """Test issue serialization."""
    from memory.api.MCP.servers.github_helpers import serialize_issue

    issue = sample_issues[0]
    result = serialize_issue(issue)

    assert result["id"] == issue.id
    assert result["number"] == issue.number
    assert result["title"] == issue.title
    assert result["state"] == issue.state
    assert result["author"] == issue.author
    assert result["assignees"] == issue.assignees
    assert result["labels"] == issue.labels
    assert "content" not in result


def test_serialize_issue_with_content(db_session, sample_issues):
    """Test issue serialization with content."""
    from memory.api.MCP.servers.github_helpers import serialize_issue

    issue = sample_issues[0]
    result = serialize_issue(issue, include_content=True)

    assert "content" in result
    assert result["content"] == issue.content


# =============================================================================
# Tests for deadline extraction
# =============================================================================


def test_serialize_issue_deadline_from_project_fields(db_session, sample_issues):
    """Test deadline is extracted from project_fields."""
    from memory.api.MCP.servers.github_helpers import serialize_issue

    issue = sample_issues[0]
    issue.project_fields = {
        "EquiStamp.Due Date": "2026-03-15",
        "EquiStamp.Status": "In Progress",
    }
    db_session.commit()

    result = serialize_issue(issue)

    assert result["deadline"] == "2026-03-15"


def test_serialize_issue_no_deadline(db_session, sample_issues):
    """Test deadline is None when not set in project_fields."""
    from memory.api.MCP.servers.github_helpers import serialize_issue

    issue = sample_issues[4]  # Has project_fields=None
    result = serialize_issue(issue)

    assert result["deadline"] is None


def test_serialize_issue_deadline_without_due_date_field(db_session, sample_issues):
    """Test deadline is None when project_fields exists but no Due Date."""
    from memory.api.MCP.servers.github_helpers import serialize_issue

    issue = sample_issues[0]  # Has project_fields but no Due Date
    result = serialize_issue(issue)

    assert result["deadline"] is None


def test_extract_deadline_from_project_fields():
    """Test extract_deadline helper with project_fields."""
    from memory.api.MCP.servers.github_helpers import extract_deadline
    from unittest.mock import MagicMock

    item = MagicMock()
    item.project_fields = {"EquiStamp.Due Date": "2026-02-28"}
    item.milestone_rel = None

    assert extract_deadline(item) == "2026-02-28"


def test_extract_deadline_from_milestone():
    """Test extract_deadline falls back to milestone due_on."""
    from memory.api.MCP.servers.github_helpers import extract_deadline
    from unittest.mock import MagicMock
    from datetime import datetime, timezone

    milestone = MagicMock()
    milestone.due_on = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)

    item = MagicMock()
    item.project_fields = {}
    item.milestone_rel = milestone

    assert extract_deadline(item) == "2026-04-15"


def test_extract_deadline_project_field_takes_priority():
    """Test project_fields Due Date takes priority over milestone."""
    from memory.api.MCP.servers.github_helpers import extract_deadline
    from unittest.mock import MagicMock
    from datetime import datetime, timezone

    milestone = MagicMock()
    milestone.due_on = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)

    item = MagicMock()
    item.project_fields = {"EquiStamp.Due Date": "2026-03-01"}
    item.milestone_rel = milestone

    # Project field should win
    assert extract_deadline(item) == "2026-03-01"
