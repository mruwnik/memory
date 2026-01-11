import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from memory.common.db.models import ForumPost
from memory.workers.tasks import forums
from memory.parsers.lesswrong import LessWrongPost
from memory.common.content_processing import create_content_hash


@pytest.fixture
def mock_lesswrong_post():
    """Mock LessWrong post data for testing."""
    return LessWrongPost(
        title="Test LessWrong Post",
        url="https://www.lesswrong.com/posts/test123/test-post",
        description="This is a test post description",
        content="This is test post content with enough text to be processed and embedded.",
        authors=["Test Author"],
        published_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        guid="test123",
        karma=25,
        votes=10,
        comments=5,
        words=100,
        tags=["rationality", "ai"],
        af=False,
        score=25,
        extended_score=30,
        modified_at="2024-01-01T12:30:00Z",
        slug="test-post",
        images=[],  # Empty images to avoid file path issues
    )


@pytest.fixture
def mock_empty_lesswrong_post():
    """Mock LessWrong post with empty content."""
    return LessWrongPost(
        title="Empty Post",
        url="https://www.lesswrong.com/posts/empty123/empty-post",
        description="",
        content="",
        authors=["Empty Author"],
        published_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        guid="empty123",
        karma=5,
        votes=2,
        comments=0,
        words=0,
        tags=[],
        af=False,
        score=5,
        extended_score=5,
        slug="empty-post",
        images=[],
    )


@pytest.fixture
def mock_af_post():
    """Mock Alignment Forum post."""
    return LessWrongPost(
        title="AI Safety Research",
        url="https://www.lesswrong.com/posts/af123/ai-safety-research",
        description="Important AI safety research",
        content="This is important AI safety research content that should be processed.",
        authors=["AI Researcher"],
        published_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        guid="af123",
        karma=50,
        votes=20,
        comments=15,
        words=200,
        tags=["ai-safety", "alignment"],
        af=True,
        score=50,
        extended_score=60,
        slug="ai-safety-research",
        images=[],
    )


def test_sync_lesswrong_post_success(mock_lesswrong_post, db_session, qdrant):
    """Test successful LessWrong post synchronization."""
    result = forums.sync_lesswrong_post(mock_lesswrong_post, ["test", "forum"])

    # Verify the ForumPost was created in the database
    forum_post = (
        db_session.query(ForumPost)
        .filter_by(url="https://www.lesswrong.com/posts/test123/test-post")
        .first()
    )
    assert forum_post is not None
    assert forum_post.title == "Test LessWrong Post"
    assert (
        forum_post.content
        == "This is test post content with enough text to be processed and embedded."
    )
    assert forum_post.modality == "forum"
    assert forum_post.mime_type == "text/markdown"
    assert forum_post.authors == ["Test Author"]
    assert forum_post.karma == 25
    assert forum_post.votes == 10
    assert forum_post.comments == 5
    assert forum_post.words == 100
    assert forum_post.score == 25
    assert forum_post.slug == "test-post"
    assert forum_post.images == []
    assert "test" in forum_post.tags
    assert "forum" in forum_post.tags
    assert "rationality" in forum_post.tags
    assert "ai" in forum_post.tags

    # Verify the result
    assert result["status"] == "processed"
    assert result["forumpost_id"] == forum_post.id
    assert result["title"] == "Test LessWrong Post"


def test_sync_lesswrong_post_empty_content(mock_empty_lesswrong_post, db_session):
    """Test LessWrong post sync with empty content."""
    result = forums.sync_lesswrong_post(mock_empty_lesswrong_post)

    # Should still create the post but with failed status due to no chunks
    forum_post = (
        db_session.query(ForumPost)
        .filter_by(url="https://www.lesswrong.com/posts/empty123/empty-post")
        .first()
    )
    assert forum_post is not None
    assert forum_post.title == "Empty Post"
    assert forum_post.content == ""
    assert result["status"] == "failed"  # No chunks generated for empty content
    assert result["chunks_count"] == 0


def test_sync_lesswrong_post_already_exists(mock_lesswrong_post, db_session):
    """Test LessWrong post sync when content already exists."""
    # Add existing forum post with same content hash
    existing_post = ForumPost(
        url="https://www.lesswrong.com/posts/test123/test-post",
        title="Test LessWrong Post",
        content="This is test post content with enough text to be processed and embedded.",
        sha256=create_content_hash(
            "This is test post content with enough text to be processed and embedded."
        ),
        modality="forum",
        tags=["existing"],
        mime_type="text/markdown",
        size=77,
        authors=["Test Author"],
        karma=25,
    )
    db_session.add(existing_post)
    db_session.commit()

    result = forums.sync_lesswrong_post(mock_lesswrong_post, ["test"])

    assert result["status"] == "already_exists"
    assert result["forumpost_id"] == existing_post.id

    # Verify no duplicate was created
    forum_posts = (
        db_session.query(ForumPost)
        .filter_by(url="https://www.lesswrong.com/posts/test123/test-post")
        .all()
    )
    assert len(forum_posts) == 1


def test_sync_lesswrong_post_with_custom_tags(mock_lesswrong_post, db_session, qdrant):
    """Test LessWrong post sync with custom tags."""
    result = forums.sync_lesswrong_post(mock_lesswrong_post, ["custom", "tags"])

    # Verify the ForumPost was created with custom tags merged with post tags
    forum_post = (
        db_session.query(ForumPost)
        .filter_by(url="https://www.lesswrong.com/posts/test123/test-post")
        .first()
    )
    assert forum_post is not None
    assert "custom" in forum_post.tags
    assert "tags" in forum_post.tags
    assert "rationality" in forum_post.tags  # Original post tags
    assert "ai" in forum_post.tags
    assert result["status"] == "processed"


def test_sync_lesswrong_post_af_post(mock_af_post, db_session, qdrant):
    """Test syncing an Alignment Forum post."""
    result = forums.sync_lesswrong_post(mock_af_post, ["alignment-forum"])

    forum_post = (
        db_session.query(ForumPost)
        .filter_by(url="https://www.lesswrong.com/posts/af123/ai-safety-research")
        .first()
    )
    assert forum_post is not None
    assert forum_post.title == "AI Safety Research"
    assert forum_post.karma == 50
    assert "ai-safety" in forum_post.tags
    assert "alignment" in forum_post.tags
    assert "alignment-forum" in forum_post.tags
    assert result["status"] == "processed"


@patch("memory.workers.tasks.forums.fetch_lesswrong_posts")
def test_sync_lesswrong_success(mock_fetch, mock_lesswrong_post, db_session):
    """Test successful LessWrong synchronization."""
    mock_fetch.return_value = [mock_lesswrong_post]

    with patch("memory.workers.tasks.forums.sync_lesswrong_post") as mock_sync_post:
        mock_sync_post.delay.return_value = Mock(id="task-123")

        result = forums.sync_lesswrong(
            since="2024-01-01T00:00:00",
            min_karma=10,
            limit=50,
            cooldown=0.1,
            max_items=100,
            af=False,
            tags=["test"],
        )

        assert result["posts_num"] == 1
        assert result["new_posts"] == 1
        assert result["since"] == "2024-01-01T00:00:00"
        assert result["min_karma"] == 10
        assert result["max_items"] == 100
        assert not result["af"]

        # Verify fetch_lesswrong_posts was called with correct arguments (kwargs)
        mock_fetch.assert_called_once()
        kwargs = mock_fetch.call_args.kwargs
        assert kwargs["since"] == datetime.fromisoformat("2024-01-01T00:00:00")
        assert kwargs["min_karma"] == 10
        assert kwargs["limit"] == 50
        assert kwargs["cooldown"] == 0.1
        assert kwargs["max_items"] == 100
        assert kwargs["af"] is False
        assert "until" in kwargs
        assert isinstance(kwargs["until"], datetime)

        # Verify sync_lesswrong_post was called for the new post
        mock_sync_post.delay.assert_called_once_with(mock_lesswrong_post, ["test"])


@patch("memory.workers.tasks.forums.fetch_lesswrong_posts")
def test_sync_lesswrong_with_existing_posts(
    mock_fetch, mock_lesswrong_post, db_session
):
    """Test sync when some posts already exist."""
    # Create existing forum post
    existing_post = ForumPost(
        url="https://www.lesswrong.com/posts/test123/test-post",
        title="Existing Post",
        content="Existing content",
        sha256=b"existing_hash" + bytes(24),
        modality="forum",
        tags=["existing"],
        mime_type="text/markdown",
        size=100,
        authors=["Test Author"],
    )
    db_session.add(existing_post)
    db_session.commit()

    # Mock fetch to return existing post and a new one
    new_post = mock_lesswrong_post.copy()
    new_post["url"] = "https://www.lesswrong.com/posts/new123/new-post"
    new_post["title"] = "New Post"

    mock_fetch.return_value = [mock_lesswrong_post, new_post]

    with patch("memory.workers.tasks.forums.sync_lesswrong_post") as mock_sync_post:
        mock_sync_post.delay.return_value = Mock(id="task-456")

        result = forums.sync_lesswrong(max_items=100)

        assert result["posts_num"] == 2
        assert result["new_posts"] == 1  # Only one new post

        # Verify sync_lesswrong_post was only called for the new post
        mock_sync_post.delay.assert_called_once_with(new_post, [])


@patch("memory.workers.tasks.forums.fetch_lesswrong_posts")
def test_sync_lesswrong_no_posts(mock_fetch, db_session):
    """Test sync when no posts are returned."""
    mock_fetch.return_value = []

    result = forums.sync_lesswrong()

    assert result["posts_num"] == 0
    assert result["new_posts"] == 0


@patch("memory.workers.tasks.forums.fetch_lesswrong_posts")
def test_sync_lesswrong_max_items_limit(mock_fetch, db_session):
    """Test that max_items limit is respected."""
    # Create multiple mock posts
    posts = []
    for i in range(5):
        post = LessWrongPost(
            title=f"Post {i}",
            url=f"https://www.lesswrong.com/posts/test{i}/post-{i}",
            description=f"Description {i}",
            content=f"Content {i}",
            authors=[f"Author {i}"],
            published_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            guid=f"test{i}",
            karma=10,
            votes=5,
            comments=2,
            words=50,
            tags=[],
            af=False,
            score=10,
            extended_score=10,
            slug=f"post-{i}",
            images=[],
        )
        posts.append(post)

    mock_fetch.return_value = posts

    with patch("memory.workers.tasks.forums.sync_lesswrong_post") as mock_sync_post:
        mock_sync_post.delay.return_value = Mock(id="task-123")

        result = forums.sync_lesswrong(max_items=3)

        # Should stop at max_items, but new_posts can be higher because
        # the check happens after incrementing new_posts but before incrementing posts_num
        assert result["posts_num"] == 3
        assert result["new_posts"] == 4  # One more than posts_num due to timing
        assert result["max_items"] == 3


@patch("memory.workers.tasks.forums.fetch_lesswrong_posts")
def test_sync_lesswrong_since_parameter(mock_fetch, db_session):
    """Test that since parameter is handled correctly."""
    mock_fetch.return_value = []

    forums.sync_lesswrong(since="2024-01-01T00:00:00")
    expected_since = datetime.fromisoformat("2024-01-01T00:00:00")

    # Verify fetch was called with correct since date (kwargs)
    kwargs = mock_fetch.call_args.kwargs
    actual_since = kwargs["since"]

    assert actual_since == expected_since
    assert "until" in kwargs
    assert isinstance(kwargs["until"], datetime)
    assert kwargs["until"] >= actual_since


@pytest.mark.parametrize(
    "af_value,min_karma,limit,cooldown",
    [
        (True, 20, 25, 1.0),
        (False, 5, 100, 0.0),
        (True, 50, 10, 0.5),
    ],
)
@patch("memory.workers.tasks.forums.fetch_lesswrong_posts")
def test_sync_lesswrong_parameters(
    mock_fetch, af_value, min_karma, limit, cooldown, db_session
):
    """Test that all parameters are passed correctly to fetch function."""
    mock_fetch.return_value = []

    result = forums.sync_lesswrong(
        af=af_value,
        min_karma=min_karma,
        limit=limit,
        cooldown=cooldown,
        max_items=500,
    )

    # Verify fetch was called with correct parameters (kwargs)
    kwargs = mock_fetch.call_args.kwargs

    assert kwargs["min_karma"] == min_karma
    assert kwargs["limit"] == limit
    assert kwargs["cooldown"] == cooldown
    assert kwargs["max_items"] == 500
    assert kwargs["af"] == af_value

    assert result["min_karma"] == min_karma
    assert result["af"] == af_value


@patch("memory.workers.tasks.forums.fetch_lesswrong_posts")
def test_sync_lesswrong_fetch_error(mock_fetch, db_session):
    """Test sync when fetch_lesswrong_posts raises an exception."""
    mock_fetch.side_effect = Exception("API error")

    # The safe_task_execution decorator should catch this
    result = forums.sync_lesswrong()

    assert result["status"] == "error"
    assert "API error" in result["error"]


def test_sync_lesswrong_post_error_handling(db_session):
    """Test error handling in sync_lesswrong_post."""
    # Create invalid post data that will cause an error
    invalid_post = {
        "title": "Test",
        "url": "invalid-url",
        "content": "test content",
        # Missing required fields
    }

    # The safe_task_execution decorator should catch this
    result = forums.sync_lesswrong_post(invalid_post)

    assert result["status"] == "error"
    assert "error" in result


@pytest.mark.parametrize(
    "post_tags,additional_tags,expected_tags",
    [
        (["original"], ["new"], ["original", "new"]),
        ([], ["tag1", "tag2"], ["tag1", "tag2"]),
        (["existing"], [], ["existing"]),
        (["dup", "tag"], ["tag", "new"], ["dup", "tag", "new"]),  # Duplicates removed
    ],
)
def test_sync_lesswrong_post_tag_merging(
    post_tags, additional_tags, expected_tags, db_session, qdrant
):
    """Test that post tags and additional tags are properly merged."""
    post = LessWrongPost(
        title="Tag Test Post",
        url="https://www.lesswrong.com/posts/tag123/tag-test",
        description="Test description",
        content="Test content for tag merging",
        authors=["Tag Author"],
        published_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        guid="tag123",
        karma=15,
        votes=5,
        comments=2,
        words=50,
        tags=post_tags,
        af=False,
        score=15,
        extended_score=15,
        slug="tag-test",
        images=[],
    )

    forums.sync_lesswrong_post(post, additional_tags)

    forum_post = (
        db_session.query(ForumPost)
        .filter_by(url="https://www.lesswrong.com/posts/tag123/tag-test")
        .first()
    )
    assert forum_post is not None

    # Check that all expected tags are present (order doesn't matter)
    for tag in expected_tags:
        assert tag in forum_post.tags

    # Check that no unexpected tags are present
    assert len(forum_post.tags) == len(set(expected_tags))


def test_sync_lesswrong_post_datetime_handling(db_session, qdrant):
    """Test that datetime fields are properly handled."""
    post = LessWrongPost(
        title="DateTime Test",
        url="https://www.lesswrong.com/posts/dt123/datetime-test",
        description="Test description",
        content="Test content",
        authors=["DateTime Author"],
        published_at=datetime(2024, 6, 15, 14, 30, 45, tzinfo=timezone.utc),
        guid="dt123",
        karma=20,
        votes=8,
        comments=3,
        words=75,
        tags=["datetime"],
        af=False,
        score=20,
        extended_score=25,
        modified_at="2024-06-15T15:00:00Z",
        slug="datetime-test",
        images=[],
    )

    result = forums.sync_lesswrong_post(post)

    forum_post = (
        db_session.query(ForumPost)
        .filter_by(url="https://www.lesswrong.com/posts/dt123/datetime-test")
        .first()
    )
    assert forum_post is not None
    assert forum_post.published_at == datetime(
        2024, 6, 15, 14, 30, 45, tzinfo=timezone.utc
    )
    # modified_at should be stored as string in the post data
    assert result["status"] == "processed"


def test_sync_lesswrong_post_content_hash_consistency(db_session):
    """Test that content hash is calculated consistently."""
    post = LessWrongPost(
        title="Hash Test",
        url="https://www.lesswrong.com/posts/hash123/hash-test",
        description="Test description",
        content="Consistent content for hashing",
        authors=["Hash Author"],
        published_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        guid="hash123",
        karma=15,
        votes=5,
        comments=2,
        words=50,
        tags=["hash"],
        af=False,
        score=15,
        extended_score=15,
        slug="hash-test",
        images=[],
    )

    # Sync the same post twice
    result1 = forums.sync_lesswrong_post(post)
    result2 = forums.sync_lesswrong_post(post)

    # First should succeed, second should detect existing
    assert result1["status"] == "processed"
    assert result2["status"] == "already_exists"
    assert result1["forumpost_id"] == result2["forumpost_id"]

    # Verify only one post exists in database
    forum_posts = (
        db_session.query(ForumPost)
        .filter_by(url="https://www.lesswrong.com/posts/hash123/hash-test")
        .all()
    )
    assert len(forum_posts) == 1
