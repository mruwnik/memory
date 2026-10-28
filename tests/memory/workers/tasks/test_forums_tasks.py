import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from sqlalchemy.exc import IntegrityError

from memory.common.db.models import ForumComment, ForumPost, ForumSyncConfig
from memory.workers.tasks import forums
from memory.parsers.lesswrong import LessWrongPost
from memory.common.content_processing import create_content_hash


@pytest.fixture(autouse=True)
def lw_comments_api():
    """Keep every sync off the real LessWrong comment API and the comment task queue."""
    with (
        patch("memory.workers.tasks.forums.fetch_post_comments", return_value=([], ALL_LIVE)) as comments,
        patch("memory.workers.tasks.forums.fetch_comment_counts", return_value={}) as counts,
        patch.object(forums.sync_lesswrong_comments, "apply_async") as dispatch,
        patch("memory.workers.tasks.forums.time.sleep"),
    ):
        yield Mock(comments=comments, counts=counts, dispatch=dispatch)


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
    # Create existing forum post with old karma values
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
        karma=10,
        votes=3,
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
        assert result["new_posts"] == 1
        assert result["updated_posts"] == 1

        # Verify sync_lesswrong_post was only called for the new post
        mock_sync_post.delay.assert_called_once_with(new_post, [])

    # Verify existing post had its karma updated
    db_session.refresh(existing_post)
    assert existing_post.karma == 25  # Updated from mock_lesswrong_post
    assert existing_post.votes == 10


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

    with pytest.raises(Exception, match="API error"):
        forums.sync_lesswrong()


def test_sync_lesswrong_post_error_handling(db_session):
    """Test error handling in sync_lesswrong_post with missing fields."""
    invalid_post = {
        "title": "Test",
        "url": "invalid-url",
        "content": "test content",
        # Missing required fields like 'tags'
    }

    with pytest.raises(KeyError):
        forums.sync_lesswrong_post(invalid_post)


def test_update_engagement_metrics_updates_changed_fields(db_session):
    """Test that update_engagement_metrics updates karma/votes/score/comments."""
    existing = ForumPost(
        url="https://www.lesswrong.com/posts/test/metrics-test",
        title="Metrics Test",
        content="Test content",
        sha256=b"test_hash" + bytes(24),
        modality="forum",
        tags=[],
        mime_type="text/markdown",
        size=100,
        karma=10,
        votes=5,
        score=12,
        comments=3,
    )
    db_session.add(existing)
    db_session.commit()

    post = LessWrongPost(
        title="Metrics Test",
        url="https://www.lesswrong.com/posts/test/metrics-test",
        description="",
        content="Test content",
        authors=[],
        published_at=None,
        guid=None,
        karma=150,
        votes=40,
        score=180,
        comments=25,
        words=100,
        tags=[],
        af=False,
        extended_score=200,
    )

    changed = forums.update_engagement_metrics(existing, post)
    assert changed is True
    assert existing.karma == 150
    assert existing.votes == 40
    assert existing.score == 180
    assert existing.comments == 25


def test_update_engagement_metrics_no_change(db_session):
    """Test that update_engagement_metrics returns False when nothing changed."""
    existing = ForumPost(
        url="https://www.lesswrong.com/posts/test/no-change",
        title="No Change",
        content="Test content",
        sha256=b"test_hash_nc" + bytes(20),
        modality="forum",
        tags=[],
        mime_type="text/markdown",
        size=100,
        karma=25,
        votes=10,
        score=25,
        comments=5,
    )
    db_session.add(existing)
    db_session.commit()

    post = LessWrongPost(
        title="No Change",
        url="https://www.lesswrong.com/posts/test/no-change",
        description="",
        content="Test content",
        authors=[],
        published_at=None,
        guid=None,
        karma=25,
        votes=10,
        score=25,
        comments=5,
        words=100,
        tags=[],
        af=False,
        extended_score=30,
    )

    changed = forums.update_engagement_metrics(existing, post)
    assert changed is False


@patch("memory.workers.tasks.forums.fetch_lesswrong_posts")
def test_sync_lesswrong_no_update_when_metrics_unchanged(
    mock_fetch, db_session
):
    """Test sync doesn't count posts as updated when metrics haven't changed."""
    existing_post = ForumPost(
        url="https://www.lesswrong.com/posts/test/unchanged",
        title="Unchanged",
        content="Test content",
        sha256=b"unchanged_hash" + bytes(18),
        modality="forum",
        tags=[],
        mime_type="text/markdown",
        size=100,
        karma=25,
        votes=10,
        score=25,
        comments=5,
    )
    db_session.add(existing_post)
    db_session.commit()

    post = LessWrongPost(
        title="Unchanged",
        url="https://www.lesswrong.com/posts/test/unchanged",
        description="",
        content="Test content",
        authors=[],
        published_at=None,
        guid=None,
        karma=25,
        votes=10,
        score=25,
        comments=5,
        words=100,
        tags=[],
        af=False,
        extended_score=30,
    )
    mock_fetch.return_value = [post]

    result = forums.sync_lesswrong(max_items=100)

    assert result["posts_num"] == 1
    assert result["new_posts"] == 0
    assert result["updated_posts"] == 0


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


def test_sync_lesswrong_post_content_hash_consistency(db_session, qdrant):
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


NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
# Live ids covering every comment the tests store, so a full fetch deletes nothing.
ALL_LIVE = {"c1", "c2"}


def make_post(db_session, guid, published_days_ago=1, comments=0, synced=None, checked_days_ago=None, **extra):
    post = ForumPost(
        url=f"https://www.lesswrong.com/posts/{guid}/slug",
        title=f"Post {guid}",
        content=f"content {guid}",
        sha256=create_content_hash(f"content {guid}"),
        modality="forum",
        mime_type="text/markdown",
        size=10,
        published_at=NOW - timedelta(days=published_days_ago),
        comments=comments,
        synced_comment_count=synced,
        comments_checked_at=(
            None if checked_days_ago is None else NOW - timedelta(days=checked_days_ago)
        ),
        **extra,
    )
    db_session.add(post)
    db_session.commit()
    return post


def listed(guid, comments, published_days_ago=1):
    """The post as the LessWrong listing returns it."""
    return LessWrongPost(
        title=f"Post {guid}",
        url=f"https://www.lesswrong.com/posts/{guid}/slug",
        description="",
        content=f"content {guid}",
        authors=["A"],
        published_at=NOW - timedelta(days=published_days_ago),
        guid=guid,
        karma=10,
        votes=1,
        comments=comments,
        words=10,
        tags=[],
        af=False,
        score=10,
        extended_score=10,
    )


def lw_comment(comment_id, content="A thoughtful reply to the post.", karma=7, parent=None):
    return {
        "comment_id": comment_id,
        "parent_comment_id": parent,
        "url": f"https://www.lesswrong.com/posts/p/slug?commentId={comment_id}",
        "author": "Commenter",
        "content": content,
        "karma": karma,
        "votes": 2,
        "posted_at": "2026-10-01T12:00:00+00:00",
    }


def set_config(db_session, **values):
    db_session.merge(ForumSyncConfig(forum="lesswrong", **values))
    db_session.commit()


@pytest.fixture
def frozen_now():
    with patch("memory.workers.tasks.forums.utcnow", return_value=NOW):
        yield NOW


@pytest.mark.parametrize(
    "published_days_ago, checked_days_ago, expected",
    [
        (10, None, True),  # never checked
        (10, 0.5, False),  # young posts wait at least a day
        (10, 2, True),
        (200, 10, False),  # 200 days / 10 = every 20 days
        (200, 25, True),
        (1000, 29, False),  # capped at the 30 day max interval
        (1000, 31, True),
    ],
)
def test_comment_recheck_due(published_days_ago, checked_days_ago, expected):
    checked_at = None if checked_days_ago is None else NOW - timedelta(days=checked_days_ago)
    assert (
        forums.comment_recheck_due(
            NOW - timedelta(days=published_days_ago),
            checked_at,
            NOW,
            divisor=10,
            max_interval=timedelta(days=30),
        )
        is expected
    )


def dispatched(lw_comments_api) -> list[list]:
    """The (post id, guid, live count, min karma) of each queued comment fetch."""
    return [call.kwargs["args"] for call in lw_comments_api.dispatch.call_args_list]


def test_sync_lesswrong_comments_inherits_post_access(
    db_session, qdrant, admin_user, frozen_now, lw_comments_api
):
    post = make_post(
        db_session,
        "p1",
        comments=2,
        sensitivity="confidential",
        creator_id=admin_user.id,
        tags=["ai"],
    )
    lw_comments_api.comments.return_value = ([lw_comment("c1"), lw_comment("c2", parent="c1")], ALL_LIVE)

    result = forums.sync_lesswrong_comments(post.id, "p1", 2, 3)

    lw_comments_api.comments.assert_called_once_with("p1", min_karma=3)
    assert result == {"post_id": post.id, "new": 2, "edited": 0, "unchanged": 0, "skipped": 0, "deleted": 0}
    db_session.expire_all()
    comments = db_session.query(ForumComment).order_by(ForumComment.comment_id).all()
    assert [(c.comment_id, c.parent_comment_id, c.post_id) for c in comments] == [
        ("c1", None, post.id),
        ("c2", "c1", post.id),
    ]
    assert {(c.sensitivity, c.project_id, c.creator_id) for c in comments} == {
        ("confidential", None, admin_user.id)
    }
    assert {c.embed_status for c in comments} == {"STORED"}
    assert comments[0].posted_at == datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    assert {tuple(sorted(c.tags)) for c in comments} == {("ai", "comment")}
    points, _ = qdrant.scroll(collection_name="forum", with_payload=True)
    assert {tuple(sorted(p.payload["tags"])) for p in points} == {("ai", "comment")}
    post = db_session.get(ForumPost, post.id)
    assert (post.synced_comment_count, post.comments_checked_at) == (2, NOW)


def test_sync_lesswrong_comments_reembeds_only_edited_comments(
    db_session, qdrant, frozen_now, lw_comments_api
):
    post = make_post(db_session, "p1", comments=2)
    lw_comments_api.comments.return_value = ([lw_comment("c1", "Original text"), lw_comment("c2")], ALL_LIVE)
    forums.sync_lesswrong_comments(post.id, "p1", 2, 0)

    processed = []
    process = forums.process_content_item

    def record(item, session):
        processed.append(item.comment_id)
        return process(item, session)

    lw_comments_api.comments.return_value = ([lw_comment("c1", "Edited text"), lw_comment("c2", karma=50)], ALL_LIVE)
    with patch("memory.workers.tasks.forums.process_content_item", side_effect=record):
        result = forums.sync_lesswrong_comments(post.id, "p1", 2, 0)

    assert result == {"post_id": post.id, "new": 0, "edited": 1, "unchanged": 1, "skipped": 0, "deleted": 0}
    assert processed == ["c1"]
    db_session.expire_all()
    by_id = {c.comment_id: c for c in db_session.query(ForumComment)}
    assert by_id["c1"].content == "Edited text"
    assert by_id["c1"].embed_status == "STORED"
    assert by_id["c2"].karma == 50


@pytest.mark.parametrize(
    "live_ids, kept",
    [
        ({"c1"}, ["c1"]),
        ({"c1", "c2"}, ["c1", "c2"]),  # c2 now under the karma floor, but still on LW
        (None, ["c1", "c2"]),  # truncated: a deleted comment looks like one past the limit
    ],
)
def test_sync_lesswrong_comments_drops_comments_gone_from_lw(
    db_session, qdrant, frozen_now, lw_comments_api, live_ids, kept
):
    post = make_post(db_session, "p1", comments=2)
    lw_comments_api.comments.return_value = ([lw_comment("c1"), lw_comment("c2")], {"c1", "c2"})
    forums.sync_lesswrong_comments(post.id, "p1", 2, 0)

    lw_comments_api.comments.return_value = ([lw_comment("c1")], live_ids)
    forums.sync_lesswrong_comments(post.id, "p1", 1, 0)

    db_session.expire_all()
    assert sorted(c.comment_id for c in db_session.query(ForumComment)) == kept


def test_a_comment_stored_concurrently_is_skipped(db_session, qdrant, frozen_now, lw_comments_api):
    """A manual and a scheduled sync may both fetch a post; the second insert loses the race."""
    post = make_post(db_session, "p1", comments=2)
    lw_comments_api.comments.return_value = ([lw_comment("c1"), lw_comment("c2")], ALL_LIVE)
    process = forums.process_content_item
    race = IntegrityError("INSERT", {}, Exception("duplicate key"))

    def lose_first_race(item, session):
        if item.comment_id == "c1":
            raise race
        return process(item, session)

    with patch("memory.workers.tasks.forums.process_content_item", side_effect=lose_first_race):
        result = forums.sync_lesswrong_comments(post.id, "p1", 2, 0)

    assert result == {"post_id": post.id, "new": 1, "edited": 0, "unchanged": 0, "skipped": 1, "deleted": 0}
    db_session.expire_all()
    assert [c.comment_id for c in db_session.query(ForumComment)] == ["c2"]
    assert db_session.get(ForumPost, post.id).synced_comment_count == 2


def test_deleting_a_post_deletes_its_comments(db_session, qdrant, frozen_now, lw_comments_api):
    post = make_post(db_session, "p1", comments=1)
    lw_comments_api.comments.return_value = ([lw_comment("c1")], ALL_LIVE)
    forums.sync_lesswrong_comments(post.id, "p1", 1, 0)

    db_session.expire_all()
    db_session.delete(db_session.get(ForumPost, post.id))
    db_session.commit()

    assert db_session.query(ForumComment).count() == 0


@patch("memory.workers.tasks.forums.fetch_lesswrong_posts")
def test_sync_fetches_comments_only_for_posts_whose_count_changed(
    mock_fetch, db_session, lw_comments_api, frozen_now
):
    set_config(db_session, min_comment_karma=4)
    changed = make_post(db_session, "changed", comments=2, synced=2)
    unchanged = make_post(db_session, "same", comments=3, synced=3)
    never = make_post(db_session, "never", comments=0)
    mock_fetch.return_value = [listed("changed", 5), listed("same", 3), listed("never", 0)]

    result = forums.sync_lesswrong()

    assert dispatched(lw_comments_api) == [[changed.id, "changed", 5, 4]]
    assert result["comment_fetches"] == 1
    db_session.expire_all()
    # Only the comment task may mark a changed post as checked, once its comments are stored.
    assert db_session.get(ForumPost, changed.id).comments_checked_at is None
    assert db_session.get(ForumPost, unchanged.id).comments_checked_at == NOW
    assert db_session.get(ForumPost, never.id).comments_checked_at == NOW


@patch("memory.workers.tasks.forums.fetch_lesswrong_posts")
def test_comment_fetches_are_spaced_by_the_cooldown(mock_fetch, db_session, lw_comments_api, frozen_now):
    for i in range(3):
        make_post(db_session, f"p{i}", comments=1)
    mock_fetch.return_value = [listed(f"p{i}", 1) for i in range(3)]

    forums.sync_lesswrong(cooldown=2)

    assert [c.kwargs["countdown"] for c in lw_comments_api.dispatch.call_args_list] == [0, 2, 4]


@patch("memory.workers.tasks.forums.fetch_lesswrong_posts")
def test_sync_rechecks_older_posts_on_a_decaying_schedule(
    mock_fetch, db_session, lw_comments_api, frozen_now
):
    set_config(db_session, days_back=30, comment_max_age_days=365, min_comment_karma=0)
    due_changed = make_post(db_session, "duechanged", published_days_ago=100, synced=1)
    due_same = make_post(db_session, "duesame", published_days_ago=100, synced=4, checked_days_ago=20)
    make_post(db_session, "recent", published_days_ago=100, synced=1, checked_days_ago=2)
    make_post(db_session, "ancient", published_days_ago=400, synced=1)
    mock_fetch.return_value = []
    lw_comments_api.counts.return_value = {"duechanged": 3, "duesame": 4}

    result = forums.sync_lesswrong()

    lw_comments_api.counts.assert_called_once()
    assert sorted(lw_comments_api.counts.call_args.args[0]) == ["duechanged", "duesame"]
    assert dispatched(lw_comments_api) == [[due_changed.id, "duechanged", 3, 0]]
    assert result["comment_counts_checked"] == 2
    db_session.expire_all()
    assert db_session.get(ForumPost, due_same.id).comments_checked_at == NOW
    assert db_session.get(ForumPost, due_changed.id).comments_checked_at is None


@patch("memory.workers.tasks.forums.fetch_lesswrong_posts")
def test_a_karma_floor_refetches_due_posts_even_when_their_count_is_unchanged(
    mock_fetch, db_session, lw_comments_api, frozen_now
):
    """Comments below the floor are never stored, but may since have earned enough karma."""
    set_config(db_session, min_comment_karma=5)
    same = make_post(db_session, "same", published_days_ago=100, synced=4)
    make_post(db_session, "empty", published_days_ago=100, synced=0)
    mock_fetch.return_value = []
    lw_comments_api.counts.return_value = {"same": 4, "empty": 0}

    forums.sync_lesswrong()

    assert dispatched(lw_comments_api) == [[same.id, "same", 4, 5]]


@patch("memory.workers.tasks.forums.fetch_lesswrong_posts")
def test_a_failed_count_request_skips_only_its_batch(
    mock_fetch, db_session, lw_comments_api, frozen_now
):
    in_window = make_post(db_session, "inwindow", comments=0)
    old = make_post(db_session, "old", published_days_ago=100, synced=0)
    mock_fetch.return_value = [listed("inwindow", 2)]
    lw_comments_api.counts.side_effect = RuntimeError("LW down")

    forums.sync_lesswrong()

    assert dispatched(lw_comments_api) == [[in_window.id, "inwindow", 2, 5]]
    db_session.expire_all()
    assert db_session.get(ForumPost, old.id).comments_checked_at is None


@patch("memory.workers.tasks.forums.fetch_lesswrong_posts")
def test_sync_caps_comment_fetches_per_run(mock_fetch, db_session, lw_comments_api, frozen_now):
    set_config(db_session, max_comment_fetches=2)
    for i in range(4):
        make_post(db_session, f"p{i}", comments=1)
    make_post(db_session, "old", published_days_ago=100, synced=0)
    mock_fetch.return_value = [listed(f"p{i}", 1) for i in range(4)]

    result = forums.sync_lesswrong()

    assert len(dispatched(lw_comments_api)) == 2
    assert result["comment_fetches"] == 2
    lw_comments_api.counts.assert_not_called()


@patch("memory.workers.tasks.forums.fetch_lesswrong_posts")
def test_sync_skips_comments_when_disabled(mock_fetch, db_session, lw_comments_api, frozen_now):
    set_config(db_session, comments_enabled=False)
    make_post(db_session, "p1", comments=1)
    make_post(db_session, "old", published_days_ago=100)
    mock_fetch.return_value = [listed("p1", 5)]

    forums.sync_lesswrong()

    lw_comments_api.dispatch.assert_not_called()
    lw_comments_api.counts.assert_not_called()


@patch("memory.workers.tasks.forums.fetch_lesswrong_posts")
def test_sync_reads_post_settings_from_config_and_records_the_run(
    mock_fetch, db_session, frozen_now
):
    set_config(db_session, min_karma=42, days_back=7, max_items=12, af=True)
    mock_fetch.return_value = []

    result = forums.sync_lesswrong()

    kwargs = mock_fetch.call_args.kwargs
    assert (kwargs["min_karma"], kwargs["max_items"], kwargs["af"]) == (42, 12, True)
    assert kwargs["until"] - kwargs["since"] == timedelta(days=7)
    db_session.expire_all()
    config = db_session.get(ForumSyncConfig, "lesswrong")
    assert config.last_sync_at == NOW
    assert config.last_sync_result == result
    assert config.sync_error is None


@patch("memory.workers.tasks.forums.fetch_lesswrong_posts")
def test_sync_records_a_failed_run(mock_fetch, db_session, frozen_now):
    mock_fetch.side_effect = RuntimeError("LW down")

    with pytest.raises(RuntimeError):
        forums.sync_lesswrong()

    config = db_session.get(ForumSyncConfig, "lesswrong")
    assert (config.last_sync_at, config.sync_error) == (NOW, "LW down")


@pytest.mark.parametrize(
    "active, last_sync_hours_ago, expected_run",
    [
        (True, None, True),
        (True, 25, True),
        (True, 23.99, True),  # within the slack of a beat tick
        (True, 12, False),
        (False, None, False),
    ],
)
def test_sync_lesswrong_if_due(db_session, frozen_now, active, last_sync_hours_ago, expected_run):
    set_config(
        db_session,
        active=active,
        check_interval=24 * 60,
        last_sync_at=(
            None if last_sync_hours_ago is None else NOW - timedelta(hours=last_sync_hours_ago)
        ),
    )

    with patch("memory.workers.tasks.forums.sync_lesswrong") as sync:
        forums.sync_lesswrong_if_due()

    assert sync.called is expected_run


def test_a_due_sync_is_claimed_before_it_runs(db_session, frozen_now):
    """A tick during a long sync must not start a second one."""
    set_config(db_session, last_sync_at=NOW - timedelta(days=2))

    with patch("memory.workers.tasks.forums.sync_lesswrong") as sync:
        forums.sync_lesswrong_if_due()
        forums.sync_lesswrong_if_due()

    assert sync.call_count == 1
