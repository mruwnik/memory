"""Tests for GitHub issue/PR syncing tasks."""

import pytest
from sqlalchemy import update
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, Mock, patch

from memory.common.db.models import GithubIssue, GithubItem, GithubPR
from memory.common.db.models.sources import CONTENT_FULL, CONTENT_METADATA, GithubAccount, GithubRepo
from memory.workers.tasks import github
from memory.workers.tasks.github import (
    _build_content,
    _needs_reindex,
    _deserialize_issue_data,
)
from memory.common.github import GithubClient, GithubIssueData, GithubQueryError, GithubComment, serialize_issue_data
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
    yield
    # Reset after test
    db_connection._engine = None
    db_connection._session_factory = None


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
        milestone_number=1,
        created_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime(2024, 1, 2, 10, 0, 0, tzinfo=timezone.utc),
        comment_count=1,
        comments=[mock_github_comment],
        diff_summary=None,
        project_fields=None,
        content_hash="abc123hash",
        pr_data=None,
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
        milestone_number=2,
        created_at=datetime(2024, 1, 5, 9, 0, 0, tzinfo=timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime(2024, 1, 6, 14, 0, 0, tzinfo=timezone.utc),
        comment_count=0,
        comments=[],
        diff_summary="+100 -50",
        project_fields={"Status": "In Progress", "Priority": "High"},
        content_hash="pr123hash",
        pr_data=None,
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
        milestone_number=None,
        created_at=datetime(2023, 12, 1, 12, 0, 0, tzinfo=timezone.utc),
        closed_at=datetime(2023, 12, 15, 18, 0, 0, tzinfo=timezone.utc),
        merged_at=None,
        github_updated_at=datetime(2023, 12, 15, 18, 0, 0, tzinfo=timezone.utc),
        comment_count=0,
        comments=[],
        diff_summary=None,
        project_fields=None,
        content_hash="closedhash",
        pr_data=None,
    )


@pytest.fixture
def test_user(db_session):
    """Create a test user for GitHub account fixtures."""
    from memory.common.db.models import User
    user = User(
        name="Test User",
        email="testuser@example.com",
        password_hash="bcrypt_hash_placeholder",
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def github_account(db_session, test_user) -> GithubAccount:
    """Create a GitHub account for testing."""
    account = GithubAccount(
        user_id=test_user.id,
        name="Test Account",
        auth_type="pat",
        access_token="ghp_test_token_12345",
        active=True,
    )
    db_session.add(account)
    db_session.commit()
    return account


@pytest.fixture
def inactive_github_account(db_session, test_user) -> GithubAccount:
    """Create an inactive GitHub account."""
    account = GithubAccount(
        user_id=test_user.id,
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
    """The client sync_github_repo gets: a live repo with nothing to sync and nothing withheld."""
    client = Mock(spec=GithubClient)
    client.get_repo.return_value = {"archived": False}
    client.fetch_milestones.return_value = []
    client.fetch_issues.return_value = []
    client.fetch_prs.return_value = []
    client.fetch_project_fields.return_value = None
    client.fetch_pr_project_fields.return_value = None
    client.unreadable_message.return_value = None
    with patch("memory.common.project.client.GithubClient", return_value=client):
        yield client


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
        milestone_number=None,
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
        milestone_number=None,
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
        milestone_number=None,
        created_at=datetime.now(timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        comment_count=0,
        comments=[],
        diff_summary=None,
        project_fields=None,
        content_hash="newhash",  # Different hash
        pr_data=None,
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
        milestone_number=None,
        created_at=datetime.now(timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime(2024, 1, 2, tzinfo=timezone.utc),  # Newer
        comment_count=0,
        comments=[],
        diff_summary=None,
        project_fields=None,
        content_hash="samehash",
        pr_data=None,
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
        milestone_number=None,
        created_at=datetime.now(timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        comment_count=0,
        comments=[],
        diff_summary=None,
        project_fields={"Status": "In Progress"},  # Changed
        content_hash="samehash",
        pr_data=None,
    )

    assert _needs_reindex(existing, new_data) is True


@pytest.mark.parametrize(
    "embed_status, expected",
    [("STORED", False), ("FAILED", True), ("RAW", True), ("QUEUED", True)],
)
def test_needs_reindex_unchanged_content(github_repo, db_session, embed_status, expected):
    """Unchanged content only skips reindexing once the item is fully embedded."""
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
        embed_status=embed_status,
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
        milestone_number=None,
        created_at=datetime.now(timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),  # Same
        comment_count=0,
        comments=[],
        diff_summary=None,
        project_fields={"Status": "Todo"},  # Same
        content_hash="samehash",  # Same
        pr_data=None,
    )

    assert _needs_reindex(existing, new_data) is expected


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
    assert type(item) is GithubIssue
    assert item.type == "github_issue"
    assert item.title == "Test Issue Title"
    assert item.kind == "issue"
    assert item.state == "open"
    assert item.author == "testuser"
    # Author was resolved to a Person end-to-end (ingest wiring).
    assert item.author_person is not None
    assert item.author_person.contact_info["github"] == "testuser"
    assert "bug" in (item.labels or [])
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
    assert type(item) is GithubPR
    assert item.type == "github_pr"
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
    from memory.common.content_processing import create_content_hash

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
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        github_updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
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
        milestone_number=None,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime(2024, 1, 5, tzinfo=timezone.utc),
        comment_count=0,
        comments=[],
        diff_summary=None,
        project_fields=None,
        content_hash="updatedhash",  # Different hash triggers reindex
        pr_data=None,
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


def test_sync_github_repo_success(
    mock_github_client, mock_issue_data, github_repo, db_session
):
    """Test successful repo sync."""
    mock_github_client.fetch_issues.return_value = [mock_issue_data]

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


def test_sync_github_repo_with_prs(
    mock_github_client, mock_issue_data, mock_pr_data, github_repo, db_session
):
    """Test repo sync with both issues and PRs."""
    mock_github_client.fetch_issues.return_value = [mock_issue_data]
    mock_github_client.fetch_prs.return_value = [mock_pr_data]

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


def test_sync_github_repo_force_full(mock_github_client, github_repo, db_session):
    """Test force_full bypasses check interval."""
    from sqlalchemy import text


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


@pytest.mark.parametrize("mode, with_diff", [(CONTENT_FULL, True), (CONTENT_METADATA, False)])
def test_sync_github_repo_fetches_diffs_only_for_repos_that_store_content(
    mock_github_client, github_repo, db_session, mode, with_diff
):
    github_repo.content_mode = mode
    db_session.commit()

    result = github.sync_github_repo(github_repo.id)

    assert result["status"] == "completed"
    assert mock_github_client.fetch_prs.call_args.kwargs == {"with_diff": with_diff}


def test_sync_github_repo_full_sync_for_project_fields(
    mock_github_client, github_repo_with_project_fields, db_session
):
    """Test full sync triggered for project fields when never synced before."""

    result = github.sync_github_repo(github_repo_with_project_fields.id)

    assert result["status"] == "completed"
    assert result["sync_type"] == "full"

    # Verify fetch_issues was called with state="open" for full sync
    mock_github_client.fetch_issues.assert_called_once()
    call_args = mock_github_client.fetch_issues.call_args
    assert call_args[0][3] == "open"  # state argument


def test_sync_github_repo_updates_timestamps(mock_github_client, github_repo, db_session):
    """Test that sync updates last_sync_at timestamp."""

    assert github_repo.last_sync_at is None

    github.sync_github_repo(github_repo.id)

    db_session.refresh(github_repo)
    assert github_repo.last_sync_at is not None


def test_sync_github_repo_records_failure_until_next_success(
    mock_github_client, github_repo, db_session
):
    mock_github_client.fetch_issues.side_effect = [RuntimeError("401 Bad credentials"), iter([])]

    with pytest.raises(RuntimeError):
        github.sync_github_repo(github_repo.id)
    db_session.refresh(github_repo)

    assert github_repo.sync_error == "401 Bad credentials"
    assert github_repo.sync_failing_since is not None
    assert github_repo.last_sync_at is None

    github.sync_github_repo(github_repo.id)
    db_session.refresh(github_repo)

    assert (github_repo.sync_error, github_repo.sync_failing_since) == (None, None)


def test_sync_github_repo_with_labels_filter(
    mock_github_client, github_account, db_session
):
    """Test sync passes labels filter to client."""

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
    mock_github_client.fetch_issues.assert_called_once()
    call_args = mock_github_client.fetch_issues.call_args
    assert call_args[0][4] == ["bug", "critical"]  # labels argument


def test_sync_github_repo_issues_only(mock_github_client, github_account, db_session):
    """Test sync with only issues tracking enabled."""

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

    mock_github_client.fetch_issues.assert_called_once()
    mock_github_client.fetch_prs.assert_not_called()


def test_sync_github_repo_prs_only(mock_github_client, github_account, db_session):
    """Test sync with only PRs tracking enabled."""

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

    mock_github_client.fetch_issues.assert_not_called()
    mock_github_client.fetch_prs.assert_called_once()


WITHHELD = "The GitHub App can't read commits on testorg/testrepo: grant it Contents (read)"


def synced(repo: GithubRepo, db_session) -> tuple[str, datetime | None]:
    result = github.sync_github_repo(repo.id)
    db_session.refresh(repo)
    return result["status"], repo.last_sync_at


@pytest.mark.parametrize("repo_fixture", ["github_repo", "github_repo_with_project_fields"])
def test_syncs_missing_fields_move_the_cursor_on_and_the_first_complete_one_catches_up(
    mock_github_client, db_session, request, repo_fixture
):
    repo = request.getfixturevalue(repo_fixture)
    cursor = datetime(2026, 1, 1, tzinfo=timezone.utc)
    repo.last_sync_at, repo.last_full_sync_at = cursor, datetime.now(timezone.utc)
    db_session.commit()

    mock_github_client.unreadable_message.return_value = WITHHELD
    first, t1 = synced(repo, db_session)
    second, t2 = synced(repo, db_session)
    degraded = (repo.sync_error, repo.incomplete_since)
    mock_github_client.unreadable_message.return_value = None
    third, t3 = synced(repo, db_session)
    fourth, _ = synced(repo, db_session)

    fetched = [c.args[2:4] for c in mock_github_client.fetch_issues.call_args_list]
    assert [first, second, third, fourth] == ["incomplete", "incomplete", "completed", "completed"]
    assert degraded == (WITHHELD, cursor)
    # Project-tracking repos keep polling closed items while incomplete.
    assert fetched == [(cursor, "all"), (t1, "all"), (t2, "all"), (cursor, "all"), (t3, "all")]
    assert (repo.sync_error, repo.incomplete_since) == (None, None)


def test_an_open_only_sweep_missing_fields_catches_up_only_what_incremental_syncs_would_have(
    mock_github_client, github_repo_with_project_fields, db_session
):
    """The next sweep re-fetches every open item anyway; closed ones the sweep never touched."""
    cursor = datetime(2026, 1, 1, tzinfo=timezone.utc)
    github_repo_with_project_fields.last_sync_at = cursor
    db_session.commit()
    mock_github_client.unreadable_message.return_value = WITHHELD

    synced(github_repo_with_project_fields, db_session)

    assert github_repo_with_project_fields.incomplete_since == cursor


def test_an_open_only_sweep_also_fetches_what_closed_since_the_last_sync(
    mock_github_client, github_repo_with_project_fields, db_session
):
    cursor = datetime(2026, 1, 1, tzinfo=timezone.utc)
    github_repo_with_project_fields.last_sync_at = cursor
    db_session.commit()

    github.sync_github_repo(github_repo_with_project_fields.id)

    assert [c.args[2:4] for c in mock_github_client.fetch_issues.call_args_list] == [(None, "open"), (cursor, "all")]


def test_a_sync_that_fails_after_an_incomplete_one_reads_as_failing_and_still_catches_up(
    mock_github_client, github_repo, db_session
):
    cursor = datetime(2026, 1, 1, tzinfo=timezone.utc)
    github_repo.last_sync_at = cursor
    db_session.commit()
    mock_github_client.unreadable_message.return_value = WITHHELD
    synced(github_repo, db_session)
    partial = github_repo.sync_incomplete

    mock_github_client.get_repo.return_value = None
    with pytest.raises(GithubQueryError):
        github.sync_github_repo(github_repo.id)

    db_session.refresh(github_repo)
    assert (partial, github_repo.sync_incomplete, github_repo.incomplete_since) == (True, False, cursor)


def test_a_refetch_missing_fields_during_a_complete_sync_still_gets_caught_up(
    mock_github_client, github_repo, db_session
):
    github_repo.incomplete_since = datetime(2026, 2, 1, tzinfo=timezone.utc)
    db_session.commit()
    marked = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def refetch_meanwhile(*args):
        db_session.execute(update(GithubRepo).where(GithubRepo.id == github_repo.id).values(incomplete_since=marked))
        return []

    mock_github_client.fetch_issues.side_effect = refetch_meanwhile

    synced(github_repo, db_session)

    assert github_repo.incomplete_since == marked


def test_a_forced_full_sync_catches_up_without_a_second_fetch(mock_github_client, github_repo, db_session):
    github_repo.incomplete_since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db_session.commit()

    github.sync_github_repo(github_repo.id, force_full=True)

    db_session.refresh(github_repo)
    assert [c.args[2:4] for c in mock_github_client.fetch_issues.call_args_list] == [(None, "all")]
    assert github_repo.incomplete_since is None


def test_a_sync_github_refuses_milestones_to_still_syncs_issues_and_says_why(
    mock_github_client, mock_issue_data, github_repo, db_session
):
    mock_github_client.fetch_milestones.side_effect = GithubQueryError("grant it Issues (read)")
    mock_github_client.fetch_issues.return_value = [mock_issue_data]

    with patch.object(github, "sync_github_item"):
        result = github.sync_github_repo(github_repo.id)

    db_session.refresh(github_repo)
    assert (result["status"], result["issues_synced"]) == ("incomplete", 1)
    assert (github_repo.sync_error, github_repo.last_sync_at is not None) == ("grant it Issues (read)", True)
    assert github_repo.sync_incomplete is True
    # Every sync fetches all milestones, so there's nothing to catch up on.
    assert github_repo.incomplete_since is None


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
        milestone_number=None,
        created_at=datetime.now(timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime.now(timezone.utc),
        comment_count=0,
        comments=[],
        diff_summary=None,
        project_fields={"Status": "Done", "Priority": "Low", "Custom Field": "Value"},
        content_hash="hash",
        pr_data=None,
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


def test_extract_status_priority_skips_non_scalar_values():
    """Regression: GitHub Projects v2 fields can ship structured payloads
    (iteration / milestone field values are nested dicts/lists). The
    worker copy of this logic used to ``str()`` whatever it found,
    leaving ``"{'title': 'Sprint 1'}"`` etc. in the project_status DB
    column. Both extraction paths must skip non-scalar values.
    """
    from memory.common.github import extract_status_priority

    fields = {
        "Status": "In Progress",
        "Priority": 2,  # int is a scalar; must be coerced to str
        # Structured field values (iteration / milestone): must be SKIPPED.
        "Iteration": {"title": "Sprint 1", "startDate": "2026-01-01"},
        "AssigneesField": [{"login": "alice"}],
    }
    status, priority = extract_status_priority(fields)
    assert status == "In Progress"
    assert priority == "2"  # numeric scalar coerced
    # Crucially: the Iteration dict didn't sneak in as `status` via the
    # second-status-field substring match either.


def test_extract_status_priority_first_match_wins():
    """If multiple fields match the substring, the first wins (preserving
    historical behaviour). Locks in the contract."""
    from memory.common.github import extract_status_priority

    fields = {
        "Project Status": "Done",
        "Sub-status": "ignored",
        "Priority": "High",
    }
    status, priority = extract_status_priority(fields)
    assert status == "Done"
    assert priority == "High"


def test_extract_status_priority_skips_none():
    """``None`` values must not match — even if the key contains
    'status'/'priority'."""
    from memory.common.github import extract_status_priority

    fields = {"Status": None, "Priority": None}
    assert extract_status_priority(fields) == (None, None)


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
        milestone_number=None,
        created_at=datetime.now(timezone.utc),
        closed_at=None,
        merged_at=None,
        github_updated_at=datetime.now(timezone.utc),
        comment_count=0,
        comments=[],
        diff_summary=None,
        project_fields={"PROJECT STATUS": "In Review", "item priority": "Medium"},
        content_hash="hash",
        pr_data=None,
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
        milestone_number=None,
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
    from memory.common.github import GithubPRDataDict

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
                "commit_oid": None,
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
        milestone_number=2,
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
        milestone_number=None,
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
        milestone_number=None,
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
    assert result.files is not None
    assert len(result.files) == 2
    assert result.files[0]["filename"] == "src/main.py"

    # Reviews
    assert result.reviews is not None
    assert len(result.reviews) == 1
    assert result.reviews[0]["user"] == "lead_reviewer"
    assert result.reviews[0]["state"] == "approved"

    # Review comments
    assert result.review_comments is not None
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
        milestone_number=None,
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


@pytest.mark.parametrize("missing", ["created_at", "github_updated_at"])
def test_deserialize_issue_data_raises_on_missing_required_date(mock_issue_data, missing):
    serialized = serialize_issue_data(mock_issue_data) | {missing: None}

    with pytest.raises(ValueError, match="Missing required GitHub date"):
        _deserialize_issue_data(serialized)


def test_serialize_deserialize_without_pr_data(mock_issue_data):
    """Test serialization roundtrip for issue without pr_data."""
    # mock_issue_data already has pr_data=None
    serialized = serialize_issue_data(mock_issue_data)
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
        milestone_number=None,
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
    from memory.common.content_processing import create_content_hash

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
        milestone_number=None,
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
                {"id": 1, "user": "reviewer", "state": "approved", "body": "Approved!", "submitted_at": "", "commit_oid": None}
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
    from memory.common.content_processing import create_content_hash

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
        milestone_number=None,
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


# =============================================================================
# Repo archival/missing detection (Task 8)
# =============================================================================


def make_archive_test_repo(db_session) -> GithubRepo:
    """Create a fresh user/account/repo for archive-detection tests.

    # FIXME: This helper exists because the shared `test_user` fixture
    # builds a bare `User()` row, which violates the `user_has_auth_method`
    # check constraint introduced by user-auth refactors. The fixture
    # should be repaired (use `HumanUser` so an auth method is present)
    # and then this local workaround can be deleted in favour of the
    # standard `github_repo` fixture chain. Until that's prioritised,
    # build inline via `HumanUser` here.
    """
    import uuid as _uuid
    from memory.common.db.models import HumanUser

    suffix = _uuid.uuid4().hex[:8]
    user = HumanUser(
        name=f"Archive Test User {suffix}",
        email=f"archive-test-{suffix}@example.com",
        password_hash="bcrypt_hash_placeholder",
        scopes=["*"],
    )
    db_session.add(user)
    db_session.flush()

    account = GithubAccount(
        user_id=user.id,
        name=f"Archive Test Account {suffix}",
        auth_type="pat",
        active=True,
    )
    account.access_token = "ghp_archive_test_token"
    db_session.add(account)
    db_session.flush()

    repo = GithubRepo(
        account_id=account.id,
        github_id=42_424_242,
        owner=f"archive-test-{suffix}",
        name="repo",
        active=True,
    )
    db_session.add(repo)
    db_session.commit()
    return repo


def test_sync_github_repo_archived_deactivates_and_closes_children(db_session):
    """When repo is archived on GitHub, sync_github_repo deactivates the repo
    and closes all open child Project rows."""
    from memory.workers.tasks.github import sync_github_repo
    from memory.common.db.models.sources import Project

    repo = make_archive_test_repo(db_session)

    open_project = Project(
        repo_id=repo.id, github_id=1, number=1, title="ms", state="open"
    )
    closed_project = Project(
        repo_id=repo.id,
        github_id=2,
        number=2,
        title="ms2",
        state="closed",
        closed_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    db_session.add_all([open_project, closed_project])
    db_session.commit()

    mock_client = MagicMock()
    mock_client.get_repo.return_value = {
        "archived": True,
        "id": repo.github_id,
        "owner": {"login": repo.owner},
        "name": repo.name,
    }

    with patch(
        "memory.common.project.client.GithubClient", return_value=mock_client
    ):
        result = sync_github_repo(repo.id)

    assert result["status"] == "skipped_repo_archived"

    db_session.refresh(repo)
    assert repo.active is False

    db_session.refresh(open_project)
    assert open_project.state == "closed"
    assert open_project.closed_at is not None

    db_session.refresh(closed_project)
    assert closed_project.state == "closed"


@pytest.mark.parametrize("auth_type, hidden_by", [("app", "the GitHub App isn't installed on it"), ("pat", "the access token can't see it")])
def test_a_repo_github_does_not_find_stays_tracked_and_says_it_may_be_hidden(db_session, auth_type, hidden_by):
    """GitHub answers 404 for a private repo the App or token lost access to, as for a deleted one."""
    from memory.workers.tasks.github import sync_github_repo
    from memory.common.db.models.sources import Project

    repo = make_archive_test_repo(db_session)
    repo.account.auth_type = auth_type
    open_project = Project(
        repo_id=repo.id, github_id=10, number=1, title="ms", state="open"
    )
    db_session.add(open_project)
    db_session.commit()

    mock_client = MagicMock()
    mock_client.get_repo.return_value = None

    with patch(
        "memory.common.project.client.GithubClient", return_value=mock_client
    ), pytest.raises(GithubQueryError):
        sync_github_repo(repo.id)

    db_session.refresh(repo)
    db_session.refresh(open_project)
    assert (repo.active, open_project.state) == (True, "open")
    assert (repo.sync_error or "").startswith(
        f"GitHub returns not-found for {repo.repo_path}: {hidden_by}, or it was renamed or deleted."
    )
