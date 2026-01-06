"""Tests for GitHub issue/PR syncing tasks."""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from memory.common.db.models import GithubItem
from memory.common.db.models.sources import GithubAccount, GithubRepo
from memory.workers.tasks import github
from memory.workers.tasks.github import (
    _build_content,
    _needs_reindex,
    _deserialize_issue_data,
)
from memory.parsers.github import GithubIssueData, GithubComment, serialize_issue_data
from memory.common.db import connection as db_connection


@pytest.fixture(autouse=True)
def reset_db_cache():
    """Reset the cached database engine between tests.

    The db connection module caches the engine globally, which can cause
    issues when test databases are created/dropped between tests.
    """
    # Reset before test
    db_connection._engine = None
    db_connection._session_factory = None
    db_connection._scoped_session = None
    yield
    # Reset after test
    db_connection._engine = None
    db_connection._session_factory = None
    db_connection._scoped_session = None


@pytest.fixture
def mock_github_comment() -> GithubComment:
    """Mock comment data."""
    return GithubComment(
        id=1001,
        author="commenter",
        body="This is a comment on the issue.",
        created_at="2024-01-01T12:30:00Z",
        updated_at="2024-01-01T12:30:00Z",
    )


@pytest.fixture
def mock_issue_data(mock_github_comment) -> GithubIssueData:
    """Mock issue data for testing."""
    return GithubIssueData(
        kind="issue",
        number=42,
        title="Test Issue Title",
        body="This is the issue body with some content to test.",
        state="open",
        author="testuser",
        labels=["bug", "help wanted"],
        assignees=["developer1"],
        milestone="v1.0",
        created_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime(2024, 1, 2, 10, 0, 0, tzinfo=timezone.utc),
        comment_count=1,
        comments=[mock_github_comment],
        diff_summary=None,
        project_fields=None,
        content_hash="abc123hash",
    )


@pytest.fixture
def mock_pr_data() -> GithubIssueData:
    """Mock PR data for testing."""
    return GithubIssueData(
        kind="pr",
        number=123,
        title="Add new feature",
        body="This PR adds a new feature to the project.",
        state="open",
        author="contributor",
        labels=["enhancement"],
        assignees=["reviewer1", "reviewer2"],
        milestone="v2.0",
        created_at=datetime(2024, 1, 5, 9, 0, 0, tzinfo=timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime(2024, 1, 6, 14, 0, 0, tzinfo=timezone.utc),
        comment_count=0,
        comments=[],
        diff_summary="+100 -50",
        project_fields={"Status": "In Progress", "Priority": "High"},
        content_hash="pr123hash",
    )


@pytest.fixture
def mock_closed_issue_data() -> GithubIssueData:
    """Mock closed issue data."""
    return GithubIssueData(
        kind="issue",
        number=10,
        title="Fixed Bug",
        body="This bug has been fixed.",
        state="closed",
        author="reporter",
        labels=["bug", "fixed"],
        assignees=[],
        milestone=None,
        created_at=datetime(2023, 12, 1, 12, 0, 0, tzinfo=timezone.utc),
        closed_at=datetime(2023, 12, 15, 18, 0, 0, tzinfo=timezone.utc),
        merged_at=None,
        github_updated_at=datetime(2023, 12, 15, 18, 0, 0, tzinfo=timezone.utc),
        comment_count=0,
        comments=[],
        diff_summary=None,
        project_fields=None,
        content_hash="closedhash",
    )


@pytest.fixture
def github_account(db_session) -> GithubAccount:
    """Create a GitHub account for testing."""
    account = GithubAccount(
        name="Test Account",
        auth_type="pat",
        access_token="ghp_test_token_12345",
        active=True,
    )
    db_session.add(account)
    db_session.commit()
    return account


@pytest.fixture
def inactive_github_account(db_session) -> GithubAccount:
    """Create an inactive GitHub account."""
    account = GithubAccount(
        name="Inactive Account",
        auth_type="pat",
        access_token="ghp_inactive_token",
        active=False,
    )
    db_session.add(account)
    db_session.commit()
    return account


@pytest.fixture
def github_repo(db_session, github_account) -> GithubRepo:
    """Create a GitHub repo for testing."""
    repo = GithubRepo(
        account_id=github_account.id,
        owner="testorg",
        name="testrepo",
        track_issues=True,
        track_prs=True,
        track_comments=True,
        track_project_fields=False,
        labels_filter=[],
        state_filter=None,
        tags=["github", "test"],
        check_interval=60,
        full_sync_interval=1440,
        active=True,
        last_sync_at=None,
        last_full_sync_at=None,
    )
    db_session.add(repo)
    db_session.commit()
    return repo


@pytest.fixture
def inactive_github_repo(db_session, github_account) -> GithubRepo:
    """Create an inactive GitHub repo."""
    repo = GithubRepo(
        account_id=github_account.id,
        owner="testorg",
        name="inactiverepo",
        track_issues=True,
        track_prs=True,
        active=False,
    )
    db_session.add(repo)
    db_session.commit()
    return repo


@pytest.fixture
def github_repo_with_project_fields(db_session, github_account) -> GithubRepo:
    """Create a GitHub repo with project field tracking enabled."""
    repo = GithubRepo(
        account_id=github_account.id,
        owner="testorg",
        name="projectrepo",
        track_issues=True,
        track_prs=True,
        track_comments=True,
        track_project_fields=True,
        labels_filter=[],
        state_filter=None,
        tags=["project"],
        check_interval=60,
        full_sync_interval=1440,
        active=True,
        last_sync_at=None,
        last_full_sync_at=None,
    )
    db_session.add(repo)
    db_session.commit()
    return repo


@pytest.fixture
def mock_github_client():
    """Mock GitHub client for testing."""
    client = Mock()
    client.fetch_issues.return_value = iter([])
    client.fetch_prs.return_value = iter([])
    client.fetch_project_fields.return_value = None
    client.fetch_pr_project_fields.return_value = None
    return client


# =============================================================================
# Tests for helper functions
# =============================================================================


def test_build_content_basic(mock_issue_data):
    """Test content building from issue data."""
    content = _build_content(mock_issue_data)

    assert "# Test Issue Title" in content
    assert "This is the issue body with some content to test." in content
    assert "**commenter**: This is a comment on the issue." in content


def test_build_content_no_comments():
    """Test content building with no comments."""
    data = GithubIssueData(
        kind="issue",
        number=1,
        title="Simple Issue",
        body="Body text",
        state="open",
        author="user",
        labels=[],
        assignees=[],
        milestone=None,
        created_at=datetime.now(timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime.now(timezone.utc),
        comment_count=0,
        comments=[],
        diff_summary=None,
        project_fields=None,
        content_hash="hash",
    )
    content = _build_content(data)

    assert "# Simple Issue" in content
    assert "Body text" in content
    assert "---" not in content  # No comment separator


def test_serialize_deserialize_issue_data(mock_issue_data):
    """Test serialization and deserialization roundtrip."""
    serialized = serialize_issue_data(mock_issue_data)
    deserialized = _deserialize_issue_data(serialized)

    assert deserialized["kind"] == mock_issue_data["kind"]
    assert deserialized["number"] == mock_issue_data["number"]
    assert deserialized["title"] == mock_issue_data["title"]
    assert deserialized["body"] == mock_issue_data["body"]
    assert deserialized["state"] == mock_issue_data["state"]
    assert deserialized["author"] == mock_issue_data["author"]
    assert deserialized["labels"] == mock_issue_data["labels"]
    assert deserialized["created_at"] == mock_issue_data["created_at"]
    assert deserialized["github_updated_at"] == mock_issue_data["github_updated_at"]


def test_serialize_handles_none_dates():
    """Test serialization handles None dates correctly."""
    data = GithubIssueData(
        kind="issue",
        number=1,
        title="Test",
        body="Body",
        state="open",
        author="user",
        labels=[],
        assignees=[],
        milestone=None,
        created_at=datetime.now(timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime.now(timezone.utc),
        comment_count=0,
        comments=[],
        diff_summary=None,
        project_fields=None,
        content_hash="hash",
    )
    serialized = serialize_issue_data(data)

    assert serialized["closed_at"] is None
    assert serialized["merged_at"] is None


# =============================================================================
# Tests for _needs_reindex
# =============================================================================


def test_needs_reindex_content_hash_changed(github_repo, db_session):
    """Test reindex triggered by content hash change."""
    existing = GithubItem(
        repo_path="testorg/testrepo",
        repo_id=github_repo.id,
        number=42,
        kind="issue",
        title="Old Title",
        content_hash="oldhash",
        github_updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        project_fields=None,
        modality="text",
        sha256=b"x" * 32,
    )
    db_session.add(existing)
    db_session.commit()

    new_data = GithubIssueData(
        kind="issue",
        number=42,
        title="New Title",
        body="New body",
        state="open",
        author="user",
        labels=[],
        assignees=[],
        milestone=None,
        created_at=datetime.now(timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        comment_count=0,
        comments=[],
        diff_summary=None,
        project_fields=None,
        content_hash="newhash",  # Different hash
    )

    assert _needs_reindex(existing, new_data) is True


def test_needs_reindex_github_updated_at_newer(github_repo, db_session):
    """Test reindex triggered by newer github_updated_at."""
    existing = GithubItem(
        repo_path="testorg/testrepo",
        repo_id=github_repo.id,
        number=42,
        kind="issue",
        title="Title",
        content_hash="samehash",
        github_updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        project_fields=None,
        modality="text",
        sha256=b"x" * 32,
    )
    db_session.add(existing)
    db_session.commit()

    new_data = GithubIssueData(
        kind="issue",
        number=42,
        title="Title",
        body="Body",
        state="open",
        author="user",
        labels=[],
        assignees=[],
        milestone=None,
        created_at=datetime.now(timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime(2024, 1, 2, tzinfo=timezone.utc),  # Newer
        comment_count=0,
        comments=[],
        diff_summary=None,
        project_fields=None,
        content_hash="samehash",
    )

    assert _needs_reindex(existing, new_data) is True


def test_needs_reindex_project_fields_changed(github_repo, db_session):
    """Test reindex triggered by project field changes."""
    existing = GithubItem(
        repo_path="testorg/testrepo",
        repo_id=github_repo.id,
        number=42,
        kind="issue",
        title="Title",
        content_hash="samehash",
        github_updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        project_fields={"Status": "Todo"},
        modality="text",
        sha256=b"x" * 32,
    )
    db_session.add(existing)
    db_session.commit()

    new_data = GithubIssueData(
        kind="issue",
        number=42,
        title="Title",
        body="Body",
        state="open",
        author="user",
        labels=[],
        assignees=[],
        milestone=None,
        created_at=datetime.now(timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        comment_count=0,
        comments=[],
        diff_summary=None,
        project_fields={"Status": "In Progress"},  # Changed
        content_hash="samehash",
    )

    assert _needs_reindex(existing, new_data) is True


def test_needs_reindex_no_changes(github_repo, db_session):
    """Test no reindex when nothing changed."""
    existing = GithubItem(
        repo_path="testorg/testrepo",
        repo_id=github_repo.id,
        number=42,
        kind="issue",
        title="Title",
        content_hash="samehash",
        github_updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        project_fields={"Status": "Todo"},
        modality="text",
        sha256=b"x" * 32,
    )
    db_session.add(existing)
    db_session.commit()

    new_data = GithubIssueData(
        kind="issue",
        number=42,
        title="Title",
        body="Body",
        state="open",
        author="user",
        labels=[],
        assignees=[],
        milestone=None,
        created_at=datetime.now(timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),  # Same
        comment_count=0,
        comments=[],
        diff_summary=None,
        project_fields={"Status": "Todo"},  # Same
        content_hash="samehash",  # Same
    )

    assert _needs_reindex(existing, new_data) is False


# =============================================================================
# Tests for sync_github_item
# =============================================================================


def test_sync_github_item_new_issue(mock_issue_data, github_repo, db_session, qdrant):
    """Test syncing a new GitHub issue."""
    serialized = serialize_issue_data(mock_issue_data)

    result = github.sync_github_item(github_repo.id, serialized)

    assert result["status"] == "processed"

    # Verify item was created
    item = (
        db_session.query(GithubItem)
        .filter_by(repo_path="testorg/testrepo", number=42)
        .first()
    )
    assert item is not None
    assert item.title == "Test Issue Title"
    assert item.kind == "issue"
    assert item.state == "open"
    assert item.author == "testuser"
    assert "bug" in item.labels
    assert "github" in item.tags  # From repo tags
    assert "bug" in item.tags  # From issue labels


def test_sync_github_item_new_pr(mock_pr_data, github_repo, db_session, qdrant):
    """Test syncing a new GitHub PR."""
    serialized = serialize_issue_data(mock_pr_data)

    result = github.sync_github_item(github_repo.id, serialized)

    assert result["status"] == "processed"

    # Verify item was created
    item = (
        db_session.query(GithubItem)
        .filter_by(repo_path="testorg/testrepo", number=123, kind="pr")
        .first()
    )
    assert item is not None
    assert item.title == "Add new feature"
    assert item.kind == "pr"
    assert item.diff_summary == "+100 -50"
    assert item.project_status == "In Progress"
    assert item.project_priority == "High"


def test_sync_github_item_repo_not_found(mock_issue_data, db_session):
    """Test syncing with non-existent repo."""
    serialized = serialize_issue_data(mock_issue_data)

    result = github.sync_github_item(99999, serialized)

    assert result["status"] == "error"
    assert "Repo not found" in result["error"]


def test_sync_github_item_existing_unchanged(
    mock_issue_data, github_repo, db_session, qdrant
):
    """Test syncing existing item with no changes."""
    # Create existing item
    serialized = serialize_issue_data(mock_issue_data)
    github.sync_github_item(github_repo.id, serialized)

    # Sync again with same data
    result = github.sync_github_item(github_repo.id, serialized)

    assert result["status"] == "unchanged"


def test_sync_github_item_existing_updated(github_repo, db_session, qdrant):
    """Test syncing existing item with content changes."""
    from memory.workers.tasks.content_processing import create_content_hash

    # Create existing item directly in the test database
    existing_item = GithubItem(
        repo_path="testorg/testrepo",
        repo_id=github_repo.id,
        number=99,
        kind="issue",
        title="Original Title",
        content="# Original Title\n\nOriginal body",
        state="open",
        author="user",
        labels=["bug"],
        assignees=[],
        milestone=None,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        github_updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        comment_count=0,
        content_hash="originalhash",
        modality="text",
        mime_type="text/markdown",
        sha256=create_content_hash("# Original Title\n\nOriginal body"),
        size=100,
        tags=["github", "test", "bug"],
    )
    db_session.add(existing_item)
    db_session.commit()

    # Update with new content
    updated_data = GithubIssueData(
        kind="issue",
        number=99,
        title="Updated Title",
        body="Updated body with more content",
        state="open",
        author="user",
        labels=["bug", "fixed"],
        assignees=["dev1"],
        milestone=None,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime(2024, 1, 5, tzinfo=timezone.utc),
        comment_count=0,
        comments=[],
        diff_summary=None,
        project_fields=None,
        content_hash="updatedhash",  # Different hash triggers reindex
    )
    serialized = serialize_issue_data(updated_data)
    result = github.sync_github_item(github_repo.id, serialized)

    assert result["status"] == "processed"

    # Verify item was updated - query fresh from DB
    db_session.expire_all()
    item = (
        db_session.query(GithubItem)
        .filter_by(repo_path="testorg/testrepo", number=99)
        .first()
    )
    assert item.title == "Updated Title"
    assert "fixed" in item.labels
    assert "dev1" in item.assignees


# =============================================================================
# Tests for sync_github_repo
# =============================================================================


@patch("memory.workers.tasks.github.GithubClient")
def test_sync_github_repo_success(
    mock_client_class, mock_issue_data, github_repo, db_session
):
    """Test successful repo sync."""
    mock_client = Mock()
    mock_client.fetch_issues.return_value = iter([mock_issue_data])
    mock_client.fetch_prs.return_value = iter([])
    mock_client_class.return_value = mock_client

    with patch("memory.workers.tasks.github.sync_github_item") as mock_sync_item:
        mock_sync_item.delay.return_value = Mock(id="task-123")

        result = github.sync_github_repo(github_repo.id)

        assert result["status"] == "completed"
        assert result["sync_type"] == "incremental"
        assert result["repo_path"] == "testorg/testrepo"
        assert result["issues_synced"] == 1
        assert result["prs_synced"] == 0
        assert result["task_ids"] == ["task-123"]

        # Verify sync_github_item was called
        mock_sync_item.delay.assert_called_once()


@patch("memory.workers.tasks.github.GithubClient")
def test_sync_github_repo_with_prs(
    mock_client_class, mock_issue_data, mock_pr_data, github_repo, db_session
):
    """Test repo sync with both issues and PRs."""
    mock_client = Mock()
    mock_client.fetch_issues.return_value = iter([mock_issue_data])
    mock_client.fetch_prs.return_value = iter([mock_pr_data])
    mock_client_class.return_value = mock_client

    with patch("memory.workers.tasks.github.sync_github_item") as mock_sync_item:
        mock_sync_item.delay.side_effect = [Mock(id="task-1"), Mock(id="task-2")]

        result = github.sync_github_repo(github_repo.id)

        assert result["issues_synced"] == 1
        assert result["prs_synced"] == 1
        assert len(result["task_ids"]) == 2


def test_sync_github_repo_not_found(db_session):
    """Test sync with non-existent repo."""
    result = github.sync_github_repo(99999)

    assert result["status"] == "error"
    assert "Repo not found or inactive" in result["error"]


def test_sync_github_repo_inactive(inactive_github_repo, db_session):
    """Test sync with inactive repo."""
    result = github.sync_github_repo(inactive_github_repo.id)

    assert result["status"] == "error"
    assert "Repo not found or inactive" in result["error"]


def test_sync_github_repo_inactive_account(db_session, inactive_github_account):
    """Test sync with inactive account."""
    repo = GithubRepo(
        account_id=inactive_github_account.id,
        owner="testorg",
        name="repo",
        active=True,
    )
    db_session.add(repo)
    db_session.commit()

    result = github.sync_github_repo(repo.id)

    assert result["status"] == "error"
    assert "Account not found or inactive" in result["error"]


@pytest.mark.parametrize(
    "check_interval_minutes,seconds_since_check,should_skip",
    [
        (60, 30, True),  # 60min interval, checked 30s ago -> skip
        (60, 3000, True),  # 60min interval, checked 50min ago -> skip
        (60, 4000, False),  # 60min interval, checked 66min ago -> don't skip
        (30, 1000, True),  # 30min interval, checked 16min ago -> skip
        (30, 2000, False),  # 30min interval, checked 33min ago -> don't skip
    ],
)
@patch("memory.workers.tasks.github.GithubClient")
def test_sync_github_repo_check_interval(
    mock_client_class,
    check_interval_minutes,
    seconds_since_check,
    should_skip,
    github_account,
    db_session,
):
    """Test sync respects check interval."""
    from sqlalchemy import text

    # Setup mock client for non-skipped cases
    mock_client = Mock()
    mock_client.fetch_issues.return_value = iter([])
    mock_client.fetch_prs.return_value = iter([])
    mock_client_class.return_value = mock_client

    # Create repo with specific check interval
    repo = GithubRepo(
        account_id=github_account.id,
        owner="testorg",
        name="intervalrepo",
        track_issues=True,
        track_prs=True,
        check_interval=check_interval_minutes,
        active=True,
    )
    db_session.add(repo)
    db_session.flush()

    # Set last_sync_at
    last_sync_time = datetime.now(timezone.utc) - timedelta(seconds=seconds_since_check)
    db_session.execute(
        text("UPDATE github_repos SET last_sync_at = :timestamp WHERE id = :repo_id"),
        {"timestamp": last_sync_time, "repo_id": repo.id},
    )
    db_session.commit()

    result = github.sync_github_repo(repo.id)

    if should_skip:
        assert result["status"] == "skipped_recent_check"
        mock_client_class.assert_not_called()
    else:
        assert result["status"] == "completed"


@patch("memory.workers.tasks.github.GithubClient")
def test_sync_github_repo_force_full(mock_client_class, github_repo, db_session):
    """Test force_full bypasses check interval."""
    from sqlalchemy import text

    mock_client = Mock()
    mock_client.fetch_issues.return_value = iter([])
    mock_client.fetch_prs.return_value = iter([])
    mock_client_class.return_value = mock_client

    # Set recent last_sync_at
    last_sync_time = datetime.now(timezone.utc) - timedelta(seconds=30)
    db_session.execute(
        text("UPDATE github_repos SET last_sync_at = :timestamp WHERE id = :repo_id"),
        {"timestamp": last_sync_time, "repo_id": github_repo.id},
    )
    db_session.commit()

    result = github.sync_github_repo(github_repo.id, force_full=True)

    assert result["status"] == "completed"
    assert result["sync_type"] == "full"


@patch("memory.workers.tasks.github.GithubClient")
def test_sync_github_repo_full_sync_for_project_fields(
    mock_client_class, github_repo_with_project_fields, db_session
):
    """Test full sync triggered for project fields when never synced before."""
    mock_client = Mock()
    mock_client.fetch_issues.return_value = iter([])
    mock_client.fetch_prs.return_value = iter([])
    mock_client.fetch_project_fields.return_value = None
    mock_client.fetch_pr_project_fields.return_value = None
    mock_client_class.return_value = mock_client

    result = github.sync_github_repo(github_repo_with_project_fields.id)

    assert result["status"] == "completed"
    assert result["sync_type"] == "full"

    # Verify fetch_issues was called with state="open" for full sync
    mock_client.fetch_issues.assert_called_once()
    call_args = mock_client.fetch_issues.call_args
    assert call_args[0][3] == "open"  # state argument


@patch("memory.workers.tasks.github.GithubClient")
def test_sync_github_repo_updates_timestamps(mock_client_class, github_repo, db_session):
    """Test that sync updates last_sync_at timestamp."""
    mock_client = Mock()
    mock_client.fetch_issues.return_value = iter([])
    mock_client.fetch_prs.return_value = iter([])
    mock_client_class.return_value = mock_client

    assert github_repo.last_sync_at is None

    github.sync_github_repo(github_repo.id)

    db_session.refresh(github_repo)
    assert github_repo.last_sync_at is not None


@patch("memory.workers.tasks.github.GithubClient")
def test_sync_github_repo_with_labels_filter(
    mock_client_class, github_account, db_session
):
    """Test sync passes labels filter to client."""
    mock_client = Mock()
    mock_client.fetch_issues.return_value = iter([])
    mock_client.fetch_prs.return_value = iter([])
    mock_client_class.return_value = mock_client

    repo = GithubRepo(
        account_id=github_account.id,
        owner="testorg",
        name="filtered",
        labels_filter=["bug", "critical"],
        track_issues=True,
        track_prs=False,
        active=True,
    )
    db_session.add(repo)
    db_session.commit()

    github.sync_github_repo(repo.id)

    # Verify labels filter was passed
    mock_client.fetch_issues.assert_called_once()
    call_args = mock_client.fetch_issues.call_args
    assert call_args[0][4] == ["bug", "critical"]  # labels argument


@patch("memory.workers.tasks.github.GithubClient")
def test_sync_github_repo_issues_only(mock_client_class, github_account, db_session):
    """Test sync with only issues tracking enabled."""
    mock_client = Mock()
    mock_client.fetch_issues.return_value = iter([])
    mock_client.fetch_prs.return_value = iter([])
    mock_client_class.return_value = mock_client

    repo = GithubRepo(
        account_id=github_account.id,
        owner="testorg",
        name="issuesonly",
        track_issues=True,
        track_prs=False,
        active=True,
    )
    db_session.add(repo)
    db_session.commit()

    github.sync_github_repo(repo.id)

    mock_client.fetch_issues.assert_called_once()
    mock_client.fetch_prs.assert_not_called()


@patch("memory.workers.tasks.github.GithubClient")
def test_sync_github_repo_prs_only(mock_client_class, github_account, db_session):
    """Test sync with only PRs tracking enabled."""
    mock_client = Mock()
    mock_client.fetch_issues.return_value = iter([])
    mock_client.fetch_prs.return_value = iter([])
    mock_client_class.return_value = mock_client

    repo = GithubRepo(
        account_id=github_account.id,
        owner="testorg",
        name="prsonly",
        track_issues=False,
        track_prs=True,
        active=True,
    )
    db_session.add(repo)
    db_session.commit()

    github.sync_github_repo(repo.id)

    mock_client.fetch_issues.assert_not_called()
    mock_client.fetch_prs.assert_called_once()


# =============================================================================
# Tests for sync_all_github_repos
# =============================================================================


@patch("memory.workers.tasks.github.sync_github_repo")
def test_sync_all_github_repos(mock_sync_repo, db_session):
    """Test syncing all active repos."""
    # Create accounts and repos
    account1 = GithubAccount(
        name="Account 1", auth_type="pat", access_token="token1", active=True
    )
    account2 = GithubAccount(
        name="Account 2", auth_type="pat", access_token="token2", active=True
    )
    db_session.add_all([account1, account2])
    db_session.flush()

    repo1 = GithubRepo(
        account_id=account1.id, owner="org1", name="repo1", active=True
    )
    repo2 = GithubRepo(
        account_id=account2.id, owner="org2", name="repo2", active=True
    )
    repo3 = GithubRepo(
        account_id=account1.id, owner="org1", name="inactive", active=False
    )
    db_session.add_all([repo1, repo2, repo3])
    db_session.commit()

    mock_sync_repo.delay.side_effect = [Mock(id="task-1"), Mock(id="task-2")]

    result = github.sync_all_github_repos()

    assert len(result) == 2  # Only active repos
    assert result[0]["repo_path"] == "org1/repo1"
    assert result[0]["task_id"] == "task-1"
    assert result[1]["repo_path"] == "org2/repo2"
    assert result[1]["task_id"] == "task-2"


@patch("memory.workers.tasks.github.sync_github_repo")
def test_sync_all_github_repos_inactive_account(mock_sync_repo, db_session):
    """Test that repos with inactive accounts are not synced."""
    active_account = GithubAccount(
        name="Active", auth_type="pat", access_token="token", active=True
    )
    inactive_account = GithubAccount(
        name="Inactive", auth_type="pat", access_token="token", active=False
    )
    db_session.add_all([active_account, inactive_account])
    db_session.flush()

    repo1 = GithubRepo(
        account_id=active_account.id, owner="org", name="repo1", active=True
    )
    repo2 = GithubRepo(
        account_id=inactive_account.id, owner="org", name="repo2", active=True
    )
    db_session.add_all([repo1, repo2])
    db_session.commit()

    mock_sync_repo.delay.return_value = Mock(id="task-1")

    result = github.sync_all_github_repos()

    assert len(result) == 1
    assert result[0]["repo_path"] == "org/repo1"


def test_sync_all_github_repos_no_active_repos(db_session):
    """Test sync_all when no active repos exist."""
    # Create only inactive repo
    account = GithubAccount(
        name="Account", auth_type="pat", access_token="token", active=True
    )
    db_session.add(account)
    db_session.flush()

    inactive_repo = GithubRepo(
        account_id=account.id, owner="org", name="inactive", active=False
    )
    db_session.add(inactive_repo)
    db_session.commit()

    result = github.sync_all_github_repos()

    assert result == []


# =============================================================================
# Tests for project field extraction
# =============================================================================


def test_project_status_extraction(github_repo, db_session, qdrant):
    """Test project status is extracted from project_fields."""
    data = GithubIssueData(
        kind="issue",
        number=50,
        title="Project Issue",
        body="Body",
        state="open",
        author="user",
        labels=[],
        assignees=[],
        milestone=None,
        created_at=datetime.now(timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime.now(timezone.utc),
        comment_count=0,
        comments=[],
        diff_summary=None,
        project_fields={"Status": "Done", "Priority": "Low", "Custom Field": "Value"},
        content_hash="hash",
    )
    serialized = serialize_issue_data(data)
    github.sync_github_item(github_repo.id, serialized)

    item = db_session.query(GithubItem).filter_by(number=50).first()
    assert item.project_status == "Done"
    assert item.project_priority == "Low"
    assert item.project_fields == {
        "Status": "Done",
        "Priority": "Low",
        "Custom Field": "Value",
    }


def test_project_fields_case_insensitive(github_repo, db_session, qdrant):
    """Test project field extraction is case insensitive."""
    data = GithubIssueData(
        kind="issue",
        number=51,
        title="Case Test",
        body="Body",
        state="open",
        author="user",
        labels=[],
        assignees=[],
        milestone=None,
        created_at=datetime.now(timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime.now(timezone.utc),
        comment_count=0,
        comments=[],
        diff_summary=None,
        project_fields={"PROJECT STATUS": "In Review", "item priority": "Medium"},
        content_hash="hash",
    )
    serialized = serialize_issue_data(data)
    github.sync_github_item(github_repo.id, serialized)

    item = db_session.query(GithubItem).filter_by(number=51).first()
    assert item.project_status == "In Review"
    assert item.project_priority == "Medium"


# =============================================================================
# Tests for tag merging
# =============================================================================


@pytest.mark.parametrize(
    "repo_tags,issue_labels,expected_tags",
    [
        (["github"], ["bug"], ["github", "bug"]),
        (["tag1", "tag2"], ["label1", "label2"], ["tag1", "tag2", "label1", "label2"]),
        ([], ["bug"], ["bug"]),
        (["github"], [], ["github"]),
        ([], [], []),
    ],
)
def test_tag_merging(repo_tags, issue_labels, expected_tags, github_account, db_session, qdrant):
    """Test tags are merged from repo and issue labels."""
    repo = GithubRepo(
        account_id=github_account.id,
        owner="testorg",
        name="tagrepo",
        tags=repo_tags,
        active=True,
    )
    db_session.add(repo)
    db_session.commit()

    data = GithubIssueData(
        kind="issue",
        number=60,
        title="Tag Test",
        body="Body",
        state="open",
        author="user",
        labels=issue_labels,
        assignees=[],
        milestone=None,
        created_at=datetime.now(timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime.now(timezone.utc),
        comment_count=0,
        comments=[],
        diff_summary=None,
        project_fields=None,
        content_hash="hash",
    )
    serialized = serialize_issue_data(data)
    github.sync_github_item(repo.id, serialized)

    item = db_session.query(GithubItem).filter_by(number=60).first()
    assert item.tags == expected_tags


# =============================================================================
# Tests for PR data handling
# =============================================================================


@pytest.fixture
def mock_pr_data_with_extended() -> GithubIssueData:
    """Mock PR data with full pr_data dict."""
    from memory.parsers.github import GithubPRDataDict

    pr_data: GithubPRDataDict = {
        "diff": "diff --git a/file.py\n+new line\n-old line",
        "files": [
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
                "patch": "+test code",
            },
        ],
        "additions": 80,
        "deletions": 10,
        "changed_files_count": 2,
        "reviews": [
            {
                "id": 1001,
                "user": "lead_reviewer",
                "state": "approved",
                "body": "LGTM!",
                "submitted_at": "2024-01-02T10:00:00Z",
            }
        ],
        "review_comments": [
            {
                "id": 2001,
                "user": "reviewer1",
                "body": "Please add a docstring here",
                "path": "src/main.py",
                "line": 42,
                "side": "RIGHT",
                "diff_hunk": "@@ -40,3 +40,5 @@",
                "created_at": "2024-01-01T15:00:00Z",
            }
        ],
    }

    return GithubIssueData(
        kind="pr",
        number=200,
        title="Feature: Add new capability",
        body="This PR adds a new capability to the system.",
        state="open",
        author="contributor",
        labels=["enhancement", "needs-review"],
        assignees=["reviewer1"],
        milestone="v2.0",
        created_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime(2024, 1, 2, 10, 0, 0, tzinfo=timezone.utc),
        comment_count=1,
        comments=[
            {
                "id": 5001,
                "author": "maintainer",
                "body": "Thanks for the PR!",
                "created_at": "2024-01-01T14:00:00Z",
                "updated_at": "2024-01-01T14:00:00Z",
            }
        ],
        diff_summary="+new\n-old",
        project_fields=None,
        content_hash="pr_extended_hash",
        pr_data=pr_data,
    )


def test_build_content_with_review_comments(mock_pr_data_with_extended):
    """Test _build_content includes review comments for PRs."""
    content = _build_content(mock_pr_data_with_extended)

    # Basic content
    assert "# Feature: Add new capability" in content
    assert "This PR adds a new capability" in content

    # Regular comment
    assert "**maintainer**: Thanks for the PR!" in content

    # Review comments section
    assert "## Code Review Comments" in content
    assert "**reviewer1**" in content
    assert "Please add a docstring here" in content
    assert "`src/main.py`" in content


def test_build_content_pr_without_review_comments():
    """Test _build_content for PR with no review comments."""
    data = GithubIssueData(
        kind="pr",
        number=201,
        title="Simple PR",
        body="Body",
        state="open",
        author="user",
        labels=[],
        assignees=[],
        milestone=None,
        created_at=datetime.now(timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime.now(timezone.utc),
        comment_count=0,
        comments=[],
        diff_summary=None,
        project_fields=None,
        content_hash="hash",
        pr_data={
            "diff": None,
            "files": [],
            "additions": 0,
            "deletions": 0,
            "changed_files_count": 0,
            "reviews": [],
            "review_comments": [],  # Empty
        },
    )

    content = _build_content(data)

    assert "# Simple PR" in content
    assert "## Code Review Comments" not in content


def test_build_content_issue_no_pr_data():
    """Test _build_content for issue (no pr_data)."""
    data = GithubIssueData(
        kind="issue",
        number=100,
        title="Bug Report",
        body="There's a bug",
        state="open",
        author="reporter",
        labels=["bug"],
        assignees=[],
        milestone=None,
        created_at=datetime.now(timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime.now(timezone.utc),
        comment_count=0,
        comments=[],
        diff_summary=None,
        project_fields=None,
        content_hash="hash",
        pr_data=None,  # Issues don't have pr_data
    )

    content = _build_content(data)

    assert "# Bug Report" in content
    assert "There's a bug" in content
    assert "## Code Review Comments" not in content


def test_create_pr_data_function(mock_pr_data_with_extended):
    """Test _create_pr_data creates GithubPRData correctly."""
    from memory.workers.tasks.github import _create_pr_data

    result = _create_pr_data(mock_pr_data_with_extended)

    assert result is not None
    assert result.additions == 80
    assert result.deletions == 10
    assert result.changed_files_count == 2

    # Files are stored as JSONB
    assert len(result.files) == 2
    assert result.files[0]["filename"] == "src/main.py"

    # Reviews
    assert len(result.reviews) == 1
    assert result.reviews[0]["user"] == "lead_reviewer"
    assert result.reviews[0]["state"] == "approved"

    # Review comments
    assert len(result.review_comments) == 1
    assert result.review_comments[0]["path"] == "src/main.py"

    # Diff is compressed - test the property getter
    assert result.diff is not None
    assert "new line" in result.diff


def test_create_pr_data_none_for_issue():
    """Test _create_pr_data returns None for issues."""
    from memory.workers.tasks.github import _create_pr_data

    data = GithubIssueData(
        kind="issue",
        number=100,
        title="Issue",
        body="Body",
        state="open",
        author="user",
        labels=[],
        assignees=[],
        milestone=None,
        created_at=datetime.now(timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime.now(timezone.utc),
        comment_count=0,
        comments=[],
        diff_summary=None,
        project_fields=None,
        content_hash="hash",
        pr_data=None,
    )

    result = _create_pr_data(data)
    assert result is None


def test_serialize_deserialize_with_pr_data(mock_pr_data_with_extended):
    """Test serialization roundtrip preserves pr_data."""
    serialized = serialize_issue_data(mock_pr_data_with_extended)

    # Verify pr_data is included in serialized form
    assert "pr_data" in serialized
    assert serialized["pr_data"]["additions"] == 80
    assert len(serialized["pr_data"]["files"]) == 2
    assert len(serialized["pr_data"]["reviews"]) == 1
    assert len(serialized["pr_data"]["review_comments"]) == 1

    # Deserialize and verify
    deserialized = _deserialize_issue_data(serialized)

    assert deserialized["pr_data"] is not None
    assert deserialized["pr_data"]["additions"] == 80
    assert deserialized["pr_data"]["deletions"] == 10
    assert len(deserialized["pr_data"]["files"]) == 2
    assert deserialized["pr_data"]["diff"] == mock_pr_data_with_extended["pr_data"]["diff"]


def test_serialize_deserialize_without_pr_data(mock_issue_data):
    """Test serialization roundtrip for issue without pr_data."""
    # Add pr_data=None to the mock (issues don't have it)
    issue_with_none = dict(mock_issue_data)
    issue_with_none["pr_data"] = None

    serialized = serialize_issue_data(issue_with_none)
    assert serialized.get("pr_data") is None

    deserialized = _deserialize_issue_data(serialized)
    assert deserialized.get("pr_data") is None


def test_sync_github_item_creates_pr_data(
    mock_pr_data_with_extended, github_repo, db_session, qdrant
):
    """Test that syncing a PR creates associated GithubPRData."""
    serialized = serialize_issue_data(mock_pr_data_with_extended)
    result = github.sync_github_item(github_repo.id, serialized)

    assert result["status"] == "processed"

    # Query the created item
    item = (
        db_session.query(GithubItem)
        .filter_by(repo_path="testorg/testrepo", number=200, kind="pr")
        .first()
    )
    assert item is not None
    assert item.kind == "pr"

    # Check pr_data relationship
    assert item.pr_data is not None
    assert item.pr_data.additions == 80
    assert item.pr_data.deletions == 10
    assert item.pr_data.changed_files_count == 2
    assert len(item.pr_data.files) == 2
    assert len(item.pr_data.reviews) == 1
    assert len(item.pr_data.review_comments) == 1

    # Verify diff decompression works
    assert item.pr_data.diff is not None
    assert "new line" in item.pr_data.diff


def test_sync_github_item_pr_without_pr_data(github_repo, db_session, qdrant):
    """Test syncing a PR that doesn't have extended pr_data."""
    data = GithubIssueData(
        kind="pr",
        number=202,
        title="Legacy PR",
        body="PR without extended data",
        state="merged",
        author="old_contributor",
        labels=[],
        assignees=[],
        milestone=None,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        closed_at=datetime(2024, 1, 5, tzinfo=timezone.utc),
        merged_at=datetime(2024, 1, 5, tzinfo=timezone.utc),
        github_updated_at=datetime(2024, 1, 5, tzinfo=timezone.utc),
        comment_count=0,
        comments=[],
        diff_summary="+10 -5",
        project_fields=None,
        content_hash="legacy_hash",
        pr_data=None,  # No extended PR data
    )

    serialized = serialize_issue_data(data)
    result = github.sync_github_item(github_repo.id, serialized)

    assert result["status"] == "processed"

    item = db_session.query(GithubItem).filter_by(number=202).first()
    assert item is not None
    assert item.kind == "pr"
    assert item.pr_data is None  # No pr_data created


def test_sync_github_item_updates_existing_pr_data(github_repo, db_session, qdrant):
    """Test updating an existing PR with new pr_data."""
    from memory.common.db.models import GithubPRData
    from memory.workers.tasks.content_processing import create_content_hash

    # Create initial PR with pr_data
    initial_content = "# Initial PR\n\nOriginal body"
    existing_item = GithubItem(
        repo_path="testorg/testrepo",
        repo_id=github_repo.id,
        number=300,
        kind="pr",
        title="Initial PR",
        content=initial_content,
        state="open",
        author="user",
        labels=[],
        assignees=[],
        milestone=None,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        github_updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        comment_count=0,
        content_hash="initial_hash",
        diff_summary="+5 -2",
        modality="github",
        mime_type="text/markdown",
        sha256=create_content_hash(initial_content),
        size=len(initial_content),
        tags=["github", "test"],
    )

    # Create initial pr_data
    initial_pr_data = GithubPRData(
        additions=5,
        deletions=2,
        changed_files_count=1,
        files=[{"filename": "old.py", "status": "modified", "additions": 5, "deletions": 2, "patch": None}],
        reviews=[],
        review_comments=[],
    )
    initial_pr_data.diff = "old diff"
    existing_item.pr_data = initial_pr_data

    db_session.add(existing_item)
    db_session.commit()

    # Now update with new data
    updated_data = GithubIssueData(
        kind="pr",
        number=300,
        title="Updated PR",
        body="Updated body with more changes",
        state="open",
        author="user",
        labels=["ready-for-review"],
        assignees=["reviewer"],
        milestone=None,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime(2024, 1, 5, tzinfo=timezone.utc),  # Newer
        comment_count=2,
        comments=[
            {"id": 1, "author": "reviewer", "body": "LGTM", "created_at": "", "updated_at": ""}
        ],
        diff_summary="+50 -10",
        project_fields=None,
        content_hash="updated_hash",  # Different hash triggers update
        pr_data={
            "diff": "new diff with lots of changes",
            "files": [
                {"filename": "new.py", "status": "added", "additions": 50, "deletions": 0, "patch": "+code"},
                {"filename": "old.py", "status": "modified", "additions": 0, "deletions": 10, "patch": "-code"},
            ],
            "additions": 50,
            "deletions": 10,
            "changed_files_count": 2,
            "reviews": [
                {"id": 1, "user": "reviewer", "state": "approved", "body": "Approved!", "submitted_at": ""}
            ],
            "review_comments": [
                {"id": 1, "user": "reviewer", "body": "Nice!", "path": "new.py", "line": 10, "side": "RIGHT", "diff_hunk": "", "created_at": ""}
            ],
        },
    )

    serialized = serialize_issue_data(updated_data)
    result = github.sync_github_item(github_repo.id, serialized)

    assert result["status"] == "processed"

    # Refresh from DB
    db_session.expire_all()
    item = db_session.query(GithubItem).filter_by(number=300).first()

    assert item.title == "Updated PR"
    assert item.pr_data is not None
    assert item.pr_data.additions == 50
    assert item.pr_data.deletions == 10
    assert item.pr_data.changed_files_count == 2
    assert len(item.pr_data.files) == 2
    assert len(item.pr_data.reviews) == 1
    assert len(item.pr_data.review_comments) == 1
    assert "new diff" in item.pr_data.diff


def test_sync_github_item_creates_pr_data_for_existing_pr_without(
    github_repo, db_session, qdrant
):
    """Test updating a PR that didn't have pr_data to add it."""
    from memory.workers.tasks.content_processing import create_content_hash

    # Create existing PR without pr_data (legacy data)
    initial_content = "# Legacy PR\n\nOriginal"
    existing_item = GithubItem(
        repo_path="testorg/testrepo",
        repo_id=github_repo.id,
        number=301,
        kind="pr",
        title="Legacy PR",
        content=initial_content,
        state="open",
        author="user",
        labels=[],
        assignees=[],
        milestone=None,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        github_updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        comment_count=0,
        content_hash="legacy_hash",
        diff_summary=None,
        modality="github",
        mime_type="text/markdown",
        sha256=create_content_hash(initial_content),
        size=len(initial_content),
        tags=["github"],
        pr_data=None,  # No pr_data initially
    )
    db_session.add(existing_item)
    db_session.commit()

    # Update with pr_data
    updated_data = GithubIssueData(
        kind="pr",
        number=301,
        title="Legacy PR",
        body="Original with new review",
        state="open",
        author="user",
        labels=[],
        assignees=[],
        milestone=None,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        comment_count=0,
        comments=[],
        diff_summary="+10 -0",
        project_fields=None,
        content_hash="new_hash",  # Different
        pr_data={
            "diff": "the full diff",
            "files": [{"filename": "new.py", "status": "added", "additions": 10, "deletions": 0, "patch": None}],
            "additions": 10,
            "deletions": 0,
            "changed_files_count": 1,
            "reviews": [],
            "review_comments": [],
        },
    )

    serialized = serialize_issue_data(updated_data)
    result = github.sync_github_item(github_repo.id, serialized)

    assert result["status"] == "processed"

    db_session.expire_all()
    item = db_session.query(GithubItem).filter_by(number=301).first()

    # Now should have pr_data
    assert item.pr_data is not None
    assert item.pr_data.additions == 10
    assert item.pr_data.diff == "the full diff"
