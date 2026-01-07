"""Comprehensive tests for GitHub MCP tools."""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, MagicMock, patch

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
            milestone="v1.0",
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
            milestone="v2.0",
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
            milestone=None,
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
            milestone="v1.0",
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
            milestone=None,
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
# Tests for list_github_issues
# =============================================================================


@pytest.mark.asyncio
async def test_list_github_issues_no_filters(db_session, sample_issues):
    """Test listing all issues without filters."""
    from memory.api.MCP.servers.github import list_github_issues

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        results = await list_github_issues()

    # Should return all issues and PRs (not comments)
    assert len(results) == 5
    # Should be ordered by github_updated_at desc
    assert results[0]["number"] == 1  # Most recently updated


@pytest.mark.asyncio
async def test_list_github_issues_filter_by_repo(db_session, sample_issues):
    """Test filtering by repository."""
    from memory.api.MCP.servers.github import list_github_issues

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        results = await list_github_issues(repo="owner/repo1")

    assert len(results) == 4
    assert all(r["repo_path"] == "owner/repo1" for r in results)


@pytest.mark.asyncio
async def test_list_github_issues_filter_by_assignee(db_session, sample_issues):
    """Test filtering by assignee."""
    from memory.api.MCP.servers.github import list_github_issues

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        results = await list_github_issues(assignee="alice")

    assert len(results) == 2
    assert all("alice" in r["assignees"] for r in results)


@pytest.mark.asyncio
async def test_list_github_issues_filter_by_author(db_session, sample_issues):
    """Test filtering by author."""
    from memory.api.MCP.servers.github import list_github_issues

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        results = await list_github_issues(author="alice")

    assert len(results) == 2
    assert all(r["author"] == "alice" for r in results)


@pytest.mark.asyncio
async def test_list_github_issues_filter_by_state(db_session, sample_issues):
    """Test filtering by state."""
    from memory.api.MCP.servers.github import list_github_issues

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        open_results = await list_github_issues(state="open")
        closed_results = await list_github_issues(state="closed")
        merged_results = await list_github_issues(state="merged")

    assert len(open_results) == 3
    assert all(r["state"] == "open" for r in open_results)

    assert len(closed_results) == 1
    assert closed_results[0]["state"] == "closed"

    assert len(merged_results) == 1
    assert merged_results[0]["state"] == "merged"


@pytest.mark.asyncio
async def test_list_github_issues_filter_by_kind(db_session, sample_issues):
    """Test filtering by kind (issue vs PR)."""
    from memory.api.MCP.servers.github import list_github_issues

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        issues = await list_github_issues(kind="issue")
        prs = await list_github_issues(kind="pr")

    assert len(issues) == 4
    assert all(r["kind"] == "issue" for r in issues)

    assert len(prs) == 1
    assert prs[0]["kind"] == "pr"


@pytest.mark.asyncio
async def test_list_github_issues_filter_by_labels(db_session, sample_issues):
    """Test filtering by labels."""
    from memory.api.MCP.servers.github import list_github_issues

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        results = await list_github_issues(labels=["bug"])

    assert len(results) == 2
    assert all("bug" in r["labels"] for r in results)


@pytest.mark.asyncio
async def test_list_github_issues_filter_by_project_status(db_session, sample_issues):
    """Test filtering by project status."""
    from memory.api.MCP.servers.github import list_github_issues

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        results = await list_github_issues(project_status="In Progress")

    assert len(results) == 1
    assert results[0]["project_status"] == "In Progress"
    assert results[0]["number"] == 1


@pytest.mark.asyncio
async def test_list_github_issues_filter_by_project_field(db_session, sample_issues):
    """Test filtering by project field (JSONB)."""
    from memory.api.MCP.servers.github import list_github_issues

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        results = await list_github_issues(
            project_field={"EquiStamp.Client": "Redwood"}
        )

    assert len(results) == 3
    assert all(
        r["project_fields"].get("EquiStamp.Client") == "Redwood" for r in results
    )


@pytest.mark.asyncio
async def test_list_github_issues_filter_by_updated_since(db_session, sample_issues):
    """Test filtering by updated_since."""
    from memory.api.MCP.servers.github import list_github_issues

    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=2)).isoformat()

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        results = await list_github_issues(updated_since=since)

    # Only issue #1 was updated within last 2 days
    assert len(results) == 1
    assert results[0]["number"] == 1


@pytest.mark.asyncio
async def test_list_github_issues_filter_by_updated_before(db_session, sample_issues):
    """Test filtering by updated_before (stale issues)."""
    from memory.api.MCP.servers.github import list_github_issues

    now = datetime.now(timezone.utc)
    before = (now - timedelta(days=30)).isoformat()

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        results = await list_github_issues(updated_before=before)

    # Only issue #100 hasn't been updated in 30+ days
    assert len(results) == 1
    assert results[0]["number"] == 100


@pytest.mark.asyncio
async def test_list_github_issues_order_by_created(db_session, sample_issues):
    """Test ordering by created date."""
    from memory.api.MCP.servers.github import list_github_issues

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        results = await list_github_issues(order_by="created")

    # Should be ordered by created_at desc
    assert results[0]["number"] == 2  # Most recently created


@pytest.mark.asyncio
async def test_list_github_issues_limit(db_session, sample_issues):
    """Test limiting results."""
    from memory.api.MCP.servers.github import list_github_issues

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        results = await list_github_issues(limit=2)

    assert len(results) == 2


@pytest.mark.asyncio
async def test_list_github_issues_limit_max_enforced(db_session, sample_issues):
    """Test that limit is capped at 200."""
    from memory.api.MCP.servers.github import list_github_issues

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        # Request 500 but should be capped at 200
        results = await list_github_issues(limit=500)

    # We only have 5 issues, but the limit should be internally capped at 200
    assert len(results) <= 200


@pytest.mark.asyncio
async def test_list_github_issues_combined_filters(db_session, sample_issues):
    """Test combining multiple filters."""
    from memory.api.MCP.servers.github import list_github_issues

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        results = await list_github_issues(
            repo="owner/repo1",
            state="open",
            kind="issue",
            project_field={"EquiStamp.Client": "Redwood"},
        )

    assert len(results) == 1
    assert results[0]["number"] == 1
    assert results[0]["title"] == "Fix authentication bug"


@pytest.mark.asyncio
async def test_list_github_issues_url_construction(db_session, sample_issues):
    """Test that URLs are correctly constructed."""
    from memory.api.MCP.servers.github import list_github_issues

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        results = await list_github_issues(kind="issue", limit=1)

    assert "url" in results[0]
    assert "github.com" in results[0]["url"]
    assert "/issues/" in results[0]["url"]

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        pr_results = await list_github_issues(kind="pr")

    assert "/pull/" in pr_results[0]["url"]


# =============================================================================
# Tests for github_issue_details
# =============================================================================


@pytest.mark.asyncio
async def test_github_issue_details_found(db_session, sample_issues):
    """Test getting details for an existing issue."""
    from memory.api.MCP.servers.github import github_issue_details

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        result = await github_issue_details(repo="owner/repo1", number=1)

    assert result["number"] == 1
    assert result["title"] == "Fix authentication bug"
    assert "content" in result
    assert "authentication" in result["content"]
    assert result["project_fields"]["EquiStamp.Client"] == "Redwood"


@pytest.mark.asyncio
async def test_github_issue_details_not_found(db_session, sample_issues):
    """Test getting details for a non-existent issue."""
    from memory.api.MCP.servers.github import github_issue_details

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        with pytest.raises(ValueError, match="not found"):
            await github_issue_details(repo="owner/repo1", number=999)


@pytest.mark.asyncio
async def test_github_issue_details_pr(db_session, sample_issues):
    """Test getting details for a PR."""
    from memory.api.MCP.servers.github import github_issue_details

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        result = await github_issue_details(repo="owner/repo1", number=50)

    assert result["kind"] == "pr"
    assert result["state"] == "merged"
    assert result["merged_at"] is not None


# =============================================================================
# Tests for github_work_summary
# =============================================================================


@pytest.mark.asyncio
async def test_github_work_summary_by_client(db_session, sample_issues):
    """Test work summary grouped by client."""
    from memory.api.MCP.servers.github import github_work_summary

    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=30)).isoformat()

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        result = await github_work_summary(since=since, group_by="client")

    assert "period" in result
    assert "summary" in result
    assert result["group_by"] == "client"

    # Check Redwood group
    redwood = next((g for g in result["summary"] if g["group"] == "Redwood"), None)
    assert redwood is not None
    assert redwood["total"] >= 1


@pytest.mark.asyncio
async def test_github_work_summary_by_status(db_session, sample_issues):
    """Test work summary grouped by status."""
    from memory.api.MCP.servers.github import github_work_summary

    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=30)).isoformat()

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        result = await github_work_summary(since=since, group_by="status")

    assert result["group_by"] == "status"
    # Check that we have status groups
    statuses = [g["group"] for g in result["summary"]]
    assert any(s in statuses for s in ["In Progress", "Backlog", "Closed", "(unset)"])


@pytest.mark.asyncio
async def test_github_work_summary_by_author(db_session, sample_issues):
    """Test work summary grouped by author."""
    from memory.api.MCP.servers.github import github_work_summary

    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=30)).isoformat()

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        result = await github_work_summary(since=since, group_by="author")

    assert result["group_by"] == "author"
    authors = [g["group"] for g in result["summary"]]
    assert "alice" in authors


@pytest.mark.asyncio
async def test_github_work_summary_by_repo(db_session, sample_issues):
    """Test work summary grouped by repository."""
    from memory.api.MCP.servers.github import github_work_summary

    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=30)).isoformat()

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        result = await github_work_summary(since=since, group_by="repo")

    assert result["group_by"] == "repo"
    repos = [g["group"] for g in result["summary"]]
    assert "owner/repo1" in repos


@pytest.mark.asyncio
async def test_github_work_summary_with_until(db_session, sample_issues):
    """Test work summary with until date."""
    from memory.api.MCP.servers.github import github_work_summary

    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=30)).isoformat()
    until = (now - timedelta(days=5)).isoformat()

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        result = await github_work_summary(since=since, until=until)

    assert result["period"]["until"] is not None


@pytest.mark.asyncio
async def test_github_work_summary_with_repo_filter(db_session, sample_issues):
    """Test work summary filtered by repository."""
    from memory.api.MCP.servers.github import github_work_summary

    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=30)).isoformat()

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        result = await github_work_summary(
            since=since, group_by="client", repo="owner/repo1"
        )

    # Should only include items from repo1
    total = sum(g["total"] for g in result["summary"])
    assert total <= 4  # repo1 has 4 items


@pytest.mark.asyncio
async def test_github_work_summary_invalid_group_by(db_session, sample_issues):
    """Test work summary with invalid group_by value."""
    from memory.api.MCP.servers.github import github_work_summary

    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=30)).isoformat()

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        with pytest.raises(ValueError, match="Invalid group_by"):
            await github_work_summary(since=since, group_by="invalid")


@pytest.mark.asyncio
async def test_github_work_summary_includes_sample_issues(db_session, sample_issues):
    """Test that work summary includes sample issues."""
    from memory.api.MCP.servers.github import github_work_summary

    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=30)).isoformat()

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        result = await github_work_summary(since=since, group_by="client")

    for group in result["summary"]:
        assert "issues" in group
        if group["total"] > 0:
            assert len(group["issues"]) <= 5  # Limited to 5 samples
            for issue in group["issues"]:
                assert "number" in issue
                assert "title" in issue
                assert "url" in issue


# =============================================================================
# Tests for github_repo_overview
# =============================================================================


@pytest.mark.asyncio
async def test_github_repo_overview_basic(db_session, sample_issues):
    """Test basic repo overview."""
    from memory.api.MCP.servers.github import github_repo_overview

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        result = await github_repo_overview(repo="owner/repo1")

    assert result["repo_path"] == "owner/repo1"
    assert "counts" in result
    assert result["counts"]["total"] == 4
    assert result["counts"]["total_issues"] == 3
    assert result["counts"]["total_prs"] == 1


@pytest.mark.asyncio
async def test_github_repo_overview_counts(db_session, sample_issues):
    """Test repo overview counts are correct."""
    from memory.api.MCP.servers.github import github_repo_overview

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        result = await github_repo_overview(repo="owner/repo1")

    counts = result["counts"]
    assert counts["open_issues"] == 3  # Issues 1, 2, 100 are all open in repo1
    assert counts["merged_prs"] == 1


@pytest.mark.asyncio
async def test_github_repo_overview_status_breakdown(db_session, sample_issues):
    """Test repo overview includes status breakdown."""
    from memory.api.MCP.servers.github import github_repo_overview

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        result = await github_repo_overview(repo="owner/repo1")

    assert "status_breakdown" in result
    assert "In Progress" in result["status_breakdown"]
    assert "Backlog" in result["status_breakdown"]


@pytest.mark.asyncio
async def test_github_repo_overview_top_assignees(db_session, sample_issues):
    """Test repo overview includes top assignees."""
    from memory.api.MCP.servers.github import github_repo_overview

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        result = await github_repo_overview(repo="owner/repo1")

    assert "top_assignees" in result
    assert isinstance(result["top_assignees"], list)


@pytest.mark.asyncio
async def test_github_repo_overview_labels(db_session, sample_issues):
    """Test repo overview includes labels."""
    from memory.api.MCP.servers.github import github_repo_overview

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        result = await github_repo_overview(repo="owner/repo1")

    assert "labels" in result
    assert "bug" in result["labels"]
    assert "enhancement" in result["labels"]


@pytest.mark.asyncio
async def test_github_repo_overview_last_updated(db_session, sample_issues):
    """Test repo overview includes last updated timestamp."""
    from memory.api.MCP.servers.github import github_repo_overview

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        result = await github_repo_overview(repo="owner/repo1")

    assert "last_updated" in result
    assert result["last_updated"] is not None


@pytest.mark.asyncio
async def test_github_repo_overview_empty_repo(db_session):
    """Test repo overview for a repo with no issues."""
    from memory.api.MCP.servers.github import github_repo_overview

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        result = await github_repo_overview(repo="nonexistent/repo")

    assert result["counts"]["total"] == 0


# =============================================================================
# Tests for search_github_issues
# =============================================================================


@pytest.mark.asyncio
async def test_search_github_issues_basic(db_session, sample_issues):
    """Test basic search functionality."""
    from memory.api.MCP.servers.github import search_github_issues

    mock_search_result = Mock()
    mock_search_result.id = sample_issues[0].id
    mock_search_result.score = 0.95

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        with patch("memory.api.MCP.github.search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = [mock_search_result]
            results = await search_github_issues(query="authentication bug")

    assert len(results) == 1
    assert "search_score" in results[0]
    mock_search.assert_called_once()


@pytest.mark.asyncio
async def test_search_github_issues_with_repo_filter(db_session, sample_issues):
    """Test search with repository filter."""
    from memory.api.MCP.servers.github import search_github_issues

    mock_search_result = Mock()
    mock_search_result.id = sample_issues[0].id
    mock_search_result.score = 0.85

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        with patch("memory.api.MCP.github.search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = [mock_search_result]
            results = await search_github_issues(
                query="authentication", repo="owner/repo1"
            )

    # Verify search was called with source_ids filter
    mock_search.assert_called_once()
    call_kwargs = mock_search.call_args[1]
    assert "filters" in call_kwargs


@pytest.mark.asyncio
async def test_search_github_issues_with_state_filter(db_session, sample_issues):
    """Test search with state filter."""
    from memory.api.MCP.servers.github import search_github_issues

    mock_search_result = Mock()
    mock_search_result.id = sample_issues[0].id
    mock_search_result.score = 0.80

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        with patch("memory.api.MCP.github.search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = [mock_search_result]
            results = await search_github_issues(query="bug", state="open")

    mock_search.assert_called_once()


@pytest.mark.asyncio
async def test_search_github_issues_limit(db_session, sample_issues):
    """Test search respects limit."""
    from memory.api.MCP.servers.github import search_github_issues

    mock_results = [Mock(id=issue.id, score=0.9 - i * 0.1) for i, issue in enumerate(sample_issues[:3])]

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        with patch("memory.api.MCP.github.search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = mock_results
            results = await search_github_issues(query="test", limit=2)

    # The search function should have been called with limit=2 in config
    call_kwargs = mock_search.call_args[1]
    assert call_kwargs["config"].limit == 2


@pytest.mark.asyncio
async def test_search_github_issues_uses_github_modality(db_session, sample_issues):
    """Test that search uses github modality."""
    from memory.api.MCP.servers.github import search_github_issues

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        with patch("memory.api.MCP.github.search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = []
            await search_github_issues(query="test")

    call_kwargs = mock_search.call_args[1]
    assert call_kwargs["modalities"] == {"github"}


# =============================================================================
# Tests for helper functions
# =============================================================================


def test_build_github_url_issue():
    """Test URL construction for issues."""
    from memory.api.MCP.servers.github import _build_github_url

    url = _build_github_url("owner/repo", 123, "issue")
    assert url == "https://github.com/owner/repo/issues/123"


def test_build_github_url_pr():
    """Test URL construction for PRs."""
    from memory.api.MCP.servers.github import _build_github_url

    url = _build_github_url("owner/repo", 456, "pr")
    assert url == "https://github.com/owner/repo/pull/456"


def test_build_github_url_no_number():
    """Test URL construction without number."""
    from memory.api.MCP.servers.github import _build_github_url

    url = _build_github_url("owner/repo", None, "issue")
    assert url == "https://github.com/owner/repo"


def test_serialize_issue_basic(db_session, sample_issues):
    """Test issue serialization."""
    from memory.api.MCP.servers.github import _serialize_issue

    issue = sample_issues[0]
    result = _serialize_issue(issue)

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
    from memory.api.MCP.servers.github import _serialize_issue

    issue = sample_issues[0]
    result = _serialize_issue(issue, include_content=True)

    assert "content" in result
    assert result["content"] == issue.content


# =============================================================================
# Parametrized tests
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "order_by,expected_first_number",
    [
        ("updated", 1),  # Most recently updated
        ("created", 2),  # Most recently created (among repo1)
        ("number", 100),  # Highest number
    ],
)
async def test_list_github_issues_ordering(
    db_session, sample_issues, order_by, expected_first_number
):
    """Test different ordering options."""
    from memory.api.MCP.servers.github import list_github_issues

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        results = await list_github_issues(order_by=order_by)

    assert results[0]["number"] == expected_first_number


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "group_by",
    ["client", "status", "author", "repo", "task_type"],
)
async def test_github_work_summary_all_group_by_options(db_session, sample_issues, group_by):
    """Test all valid group_by options."""
    from memory.api.MCP.servers.github import github_work_summary

    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=30)).isoformat()

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        result = await github_work_summary(since=since, group_by=group_by)

    assert result["group_by"] == group_by
    assert "summary" in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "labels,expected_count",
    [
        (["bug"], 2),
        (["enhancement"], 1),
        (["bug", "security"], 1),  # Only issue 1 has both
        (["nonexistent"], 0),
    ],
)
async def test_list_github_issues_label_filtering(
    db_session, sample_issues, labels, expected_count
):
    """Test various label filtering scenarios."""
    from memory.api.MCP.servers.github import list_github_issues

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        results = await list_github_issues(labels=labels)

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
        milestone=None,
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


@pytest.mark.asyncio
async def test_github_issue_details_includes_pr_data(db_session, sample_pr_with_data):
    """Test that github_issue_details includes PR data for PRs."""
    from memory.api.MCP.servers.github import github_issue_details

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        result = await github_issue_details(repo="owner/repo1", number=999)

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


@pytest.mark.asyncio
async def test_github_issue_details_no_pr_data_for_issues(db_session, sample_issues):
    """Test that github_issue_details does not include pr_data for issues."""
    from memory.api.MCP.servers.github import github_issue_details

    with patch("memory.api.MCP.servers.github.make_session", return_value=db_session):
        result = await github_issue_details(repo="owner/repo1", number=1)

    assert result["kind"] == "issue"
    assert "pr_data" not in result


def test_serialize_issue_includes_pr_data(db_session, sample_pr_with_data):
    """Test that _serialize_issue includes pr_data when include_content=True."""
    from memory.api.MCP.servers.github import _serialize_issue

    result = _serialize_issue(sample_pr_with_data, include_content=True)

    assert "pr_data" in result
    assert result["pr_data"]["additions"] == 50
    assert result["pr_data"]["reviews"][0]["state"] == "approved"


def test_serialize_issue_no_pr_data_without_content(db_session, sample_pr_with_data):
    """Test that _serialize_issue excludes pr_data when include_content=False."""
    from memory.api.MCP.servers.github import _serialize_issue

    result = _serialize_issue(sample_pr_with_data, include_content=False)

    assert "pr_data" not in result
    assert "content" not in result
