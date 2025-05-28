import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from memory.common.db.models import ArticleFeed, BlogPost
from memory.workers.tasks import blogs
from memory.parsers.blogs import Article


@pytest.fixture
def mock_article():
    """Mock article data for testing."""
    return Article(
        title="Test Article",
        url="https://example.com/article/1",
        content="This is test article content with enough text to be processed.",
        published_date=datetime(2024, 1, 1, 12, 0, 0),
        images={},  # Article.images is dict[str, PILImage.Image]
    )


@pytest.fixture
def mock_empty_article():
    """Mock article with empty content."""
    return Article(
        title="Empty Article",
        url="https://example.com/empty",
        content="",
        published_date=datetime(2024, 1, 1, 12, 0, 0),
        images={},
    )


@pytest.fixture
def sample_article_feed(db_session):
    """Create a sample ArticleFeed for testing."""
    feed = ArticleFeed(
        url="https://example.com/feed.xml",
        title="Test Feed",
        description="A test RSS feed",
        tags=["test", "blog"],
        check_interval=3600,
        active=True,
        last_checked_at=None,  # Avoid timezone issues
    )
    db_session.add(feed)
    db_session.commit()
    return feed


@pytest.fixture
def inactive_article_feed(db_session):
    """Create an inactive ArticleFeed for testing."""
    feed = ArticleFeed(
        url="https://example.com/inactive.xml",
        title="Inactive Feed",
        description="An inactive RSS feed",
        tags=["test"],
        check_interval=3600,
        active=False,
    )
    db_session.add(feed)
    db_session.commit()
    return feed


@pytest.fixture
def mock_feed_item():
    """Mock feed item for testing."""
    item = Mock()
    item.url = "https://example.com/article/1"
    item.title = "Test Article"
    return item


@pytest.fixture
def mock_feed_parser():
    """Mock feed parser for testing."""
    parser = Mock()
    parser.parse_feed.return_value = [
        Mock(url="https://example.com/article/1", title="Test Article")
    ]
    return parser


@pytest.fixture
def mock_archive_fetcher():
    """Mock archive fetcher for testing."""
    fetcher = Mock()
    fetcher.max_pages = 100
    fetcher.fetch_all_items.return_value = [
        Mock(url="https://example.com/archive/1", title="Archive Article 1"),
        Mock(url="https://example.com/archive/2", title="Archive Article 2"),
    ]
    return fetcher


@patch("memory.workers.tasks.blogs.parse_webpage")
def test_sync_webpage_success(mock_parse, mock_article, db_session, qdrant):
    """Test successful webpage synchronization."""
    mock_parse.return_value = mock_article

    result = blogs.sync_webpage("https://example.com/article/1", ["test", "blog"])

    mock_parse.assert_called_once_with("https://example.com/article/1")

    # Verify the BlogPost was created in the database
    blog_post = (
        db_session.query(BlogPost)
        .filter_by(url="https://example.com/article/1")
        .first()
    )
    assert blog_post is not None
    assert blog_post.title == "Test Article"
    assert (
        blog_post.content
        == "This is test article content with enough text to be processed."
    )
    assert blog_post.modality == "blog"
    assert blog_post.mime_type == "text/markdown"
    assert blog_post.images == []  # Empty because mock article.images is {}
    assert "test" in blog_post.tags
    assert "blog" in blog_post.tags

    # Verify the result
    assert result["status"] == "processed"
    assert result["blogpost_id"] == blog_post.id
    assert result["title"] == "Test Article"


@patch("memory.workers.tasks.blogs.parse_webpage")
def test_sync_webpage_empty_content(mock_parse, mock_empty_article, db_session):
    """Test webpage sync with empty content."""
    mock_parse.return_value = mock_empty_article

    result = blogs.sync_webpage("https://example.com/empty")

    assert result == {
        "url": "https://example.com/empty",
        "title": "Empty Article",
        "status": "skipped_short_content",
        "content_length": 0,
    }


@patch("memory.workers.tasks.blogs.parse_webpage")
def test_sync_webpage_already_exists(mock_parse, mock_article, db_session):
    """Test webpage sync when content already exists."""
    mock_parse.return_value = mock_article

    # Add existing blog post with same content hash
    from memory.workers.tasks.content_processing import create_content_hash

    existing_post = BlogPost(
        url="https://example.com/article/1",
        title="Test Article",
        content="This is test article content with enough text to be processed.",
        sha256=create_content_hash(
            "This is test article content with enough text to be processed."
        ),
        modality="blog",
        tags=["test"],
        mime_type="text/markdown",
        size=65,
    )
    db_session.add(existing_post)
    db_session.commit()

    result = blogs.sync_webpage("https://example.com/article/1", ["test"])

    assert result["status"] == "already_exists"
    assert result["blogpost_id"] == existing_post.id

    # Verify no duplicate was created
    blog_posts = (
        db_session.query(BlogPost).filter_by(url="https://example.com/article/1").all()
    )
    assert len(blog_posts) == 1


@patch("memory.workers.tasks.blogs.get_feed_parser")
def test_sync_article_feed_success(
    mock_get_parser, sample_article_feed, mock_feed_parser, db_session
):
    """Test successful article feed synchronization."""
    mock_get_parser.return_value = mock_feed_parser

    with patch("memory.workers.tasks.blogs.sync_webpage") as mock_sync_webpage:
        mock_sync_webpage.delay.return_value = Mock(id="task-123")

        result = blogs.sync_article_feed(sample_article_feed.id)

        assert result["status"] == "completed"
        assert result["feed_id"] == sample_article_feed.id
        assert result["feed_title"] == "Test Feed"
        assert result["feed_url"] == "https://example.com/feed.xml"
        assert result["articles_found"] == 1
        assert result["new_articles"] == 1
        assert result["errors"] == 0
        assert result["task_ids"] == ["task-123"]

        # Verify sync_webpage was called with correct arguments
        mock_sync_webpage.delay.assert_called_once_with(
            "https://example.com/article/1", ["test", "blog"]
        )

    # Verify last_checked_at was updated
    db_session.refresh(sample_article_feed)
    assert sample_article_feed.last_checked_at is not None


def test_sync_article_feed_not_found(db_session):
    """Test sync with non-existent feed ID."""
    result = blogs.sync_article_feed(99999)

    assert result == {"status": "error", "error": "Feed not found or inactive"}


def test_sync_article_feed_inactive(inactive_article_feed, db_session):
    """Test sync with inactive feed."""
    result = blogs.sync_article_feed(inactive_article_feed.id)

    assert result == {"status": "error", "error": "Feed not found or inactive"}


@patch("memory.workers.tasks.blogs.get_feed_parser")
def test_sync_article_feed_no_parser(mock_get_parser, sample_article_feed, db_session):
    """Test sync when no parser is available."""
    mock_get_parser.return_value = None

    result = blogs.sync_article_feed(sample_article_feed.id)

    assert result == {"status": "error", "error": "No parser available for feed"}


@patch("memory.workers.tasks.blogs.get_feed_parser")
def test_sync_article_feed_with_existing_articles(
    mock_get_parser, sample_article_feed, db_session
):
    """Test sync when some articles already exist."""
    # Create existing blog post
    existing_post = BlogPost(
        url="https://example.com/article/1",
        title="Existing Article",
        content="Existing content",
        sha256=b"existing_hash" + bytes(24),
        modality="blog",
        tags=["test"],
        mime_type="text/markdown",
        size=100,
    )
    db_session.add(existing_post)
    db_session.commit()

    # Mock parser with multiple items
    mock_parser = Mock()
    mock_parser.parse_feed.return_value = [
        Mock(url="https://example.com/article/1", title="Existing Article"),
        Mock(url="https://example.com/article/2", title="New Article"),
    ]
    mock_get_parser.return_value = mock_parser

    with patch("memory.workers.tasks.blogs.sync_webpage") as mock_sync_webpage:
        mock_sync_webpage.delay.return_value = Mock(id="task-456")

        result = blogs.sync_article_feed(sample_article_feed.id)

        assert result["articles_found"] == 2
        assert result["new_articles"] == 1  # Only one new article
        assert result["task_ids"] == ["task-456"]

        # Verify sync_webpage was only called for the new article
        mock_sync_webpage.delay.assert_called_once_with(
            "https://example.com/article/2", ["test", "blog"]
        )


@patch("memory.workers.tasks.blogs.get_feed_parser")
def test_sync_article_feed_parser_error(
    mock_get_parser, sample_article_feed, db_session
):
    """Test sync when parser raises an exception."""
    mock_parser = Mock()
    mock_parser.parse_feed.side_effect = Exception("Parser error")
    mock_get_parser.return_value = mock_parser

    result = blogs.sync_article_feed(sample_article_feed.id)

    assert result["status"] == "completed"
    assert result["articles_found"] == 0
    assert result["new_articles"] == 0
    assert result["errors"] == 1


@patch("memory.workers.tasks.blogs.sync_article_feed")
def test_sync_all_article_feeds(mock_sync_delay, db_session):
    """Test synchronization of all active feeds."""
    # Create multiple feeds
    feed1 = ArticleFeed(
        url="https://example.com/feed1.xml",
        title="Feed 1",
        active=True,
        check_interval=3600,
    )
    feed2 = ArticleFeed(
        url="https://example.com/feed2.xml",
        title="Feed 2",
        active=True,
        check_interval=3600,
    )
    feed3 = ArticleFeed(
        url="https://example.com/feed3.xml",
        title="Feed 3",
        active=False,  # Inactive
        check_interval=3600,
    )

    db_session.add_all([feed1, feed2, feed3])
    db_session.commit()

    mock_sync_delay.delay.side_effect = [Mock(id="task-1"), Mock(id="task-2")]

    result = blogs.sync_all_article_feeds()

    assert len(result) == 2  # Only active feeds
    assert result[0]["feed_id"] == feed1.id
    assert result[0]["task_id"] == "task-1"
    assert result[1]["feed_id"] == feed2.id
    assert result[1]["task_id"] == "task-2"


@patch("memory.workers.tasks.blogs.get_archive_fetcher")
def test_sync_website_archive_success(
    mock_get_fetcher, mock_archive_fetcher, db_session
):
    """Test successful website archive synchronization."""
    mock_get_fetcher.return_value = mock_archive_fetcher

    with patch("memory.workers.tasks.blogs.sync_webpage") as mock_sync_webpage:
        mock_sync_webpage.delay.side_effect = [Mock(id="task-1"), Mock(id="task-2")]

        result = blogs.sync_website_archive("https://example.com", ["archive"], 50)

        assert result["status"] == "completed"
        assert result["website_url"] == "https://example.com"
        assert result["articles_found"] == 2
        assert result["new_articles"] == 2
        assert result["task_ids"] == ["task-1", "task-2"]
        assert result["max_pages_processed"] == 50
        assert mock_archive_fetcher.max_pages == 50

        # Verify sync_webpage was called for both articles
        assert mock_sync_webpage.delay.call_count == 2
        mock_sync_webpage.delay.assert_any_call(
            "https://example.com/archive/1", ["archive"]
        )
        mock_sync_webpage.delay.assert_any_call(
            "https://example.com/archive/2", ["archive"]
        )


@patch("memory.workers.tasks.blogs.get_archive_fetcher")
def test_sync_website_archive_no_fetcher(mock_get_fetcher, db_session):
    """Test archive sync when no fetcher is available."""
    mock_get_fetcher.return_value = None

    result = blogs.sync_website_archive("https://example.com")

    assert result == {"status": "error", "error": "No archive fetcher available"}


@patch("memory.workers.tasks.blogs.get_archive_fetcher")
def test_sync_website_archive_with_existing_articles(mock_get_fetcher, db_session):
    """Test archive sync when some articles already exist."""
    # Create existing blog post
    existing_post = BlogPost(
        url="https://example.com/archive/1",
        title="Existing Archive Article",
        content="Existing content",
        sha256=b"existing_hash" + bytes(24),
        modality="blog",
        tags=["archive"],
        mime_type="text/markdown",
        size=100,
    )
    db_session.add(existing_post)
    db_session.commit()

    # Mock fetcher
    mock_fetcher = Mock()
    mock_fetcher.max_pages = 100
    mock_fetcher.fetch_all_items.return_value = [
        Mock(url="https://example.com/archive/1", title="Existing Archive Article"),
        Mock(url="https://example.com/archive/2", title="New Archive Article"),
    ]
    mock_get_fetcher.return_value = mock_fetcher

    with patch("memory.workers.tasks.blogs.sync_webpage") as mock_sync_webpage:
        mock_sync_webpage.delay.return_value = Mock(id="task-new")

        result = blogs.sync_website_archive("https://example.com", ["archive"])

        assert result["articles_found"] == 2
        assert result["new_articles"] == 1  # Only one new article
        assert result["task_ids"] == ["task-new"]

        # Verify sync_webpage was only called for the new article
        mock_sync_webpage.delay.assert_called_once_with(
            "https://example.com/archive/2", ["archive"]
        )


@patch("memory.workers.tasks.blogs.parse_webpage")
def test_sync_webpage_with_tags(mock_parse, mock_article, db_session, qdrant):
    """Test webpage sync with custom tags."""
    mock_parse.return_value = mock_article

    result = blogs.sync_webpage("https://example.com/article/1", ["custom", "tags"])

    # Verify the BlogPost was created with custom tags
    blog_post = (
        db_session.query(BlogPost)
        .filter_by(url="https://example.com/article/1")
        .first()
    )
    assert blog_post is not None
    assert "custom" in blog_post.tags
    assert "tags" in blog_post.tags
    assert result["status"] == "processed"


@patch("memory.workers.tasks.blogs.parse_webpage")
def test_sync_webpage_parse_error(mock_parse, db_session):
    """Test webpage sync when parsing fails."""
    mock_parse.side_effect = Exception("Parse error")

    # The safe_task_execution decorator should catch this
    result = blogs.sync_webpage("https://example.com/error")

    assert result["status"] == "error"
    assert "Parse error" in result["error"]


@pytest.mark.parametrize(
    "feed_tags,expected_tags",
    [
        (["feed", "tag"], ["feed", "tag"]),
        (None, []),
        ([], []),
    ],
)
@patch("memory.workers.tasks.blogs.get_feed_parser")
def test_sync_article_feed_tags_handling(
    mock_get_parser, feed_tags, expected_tags, db_session
):
    """Test that feed tags are properly passed to sync_webpage."""
    # Create feed with specific tags
    feed = ArticleFeed(
        url="https://example.com/feed.xml",
        title="Test Feed",
        tags=feed_tags,
        check_interval=3600,
        active=True,
        last_checked_at=None,  # Avoid timezone issues
    )
    db_session.add(feed)
    db_session.commit()

    mock_parser = Mock()
    mock_parser.parse_feed.return_value = [
        Mock(url="https://example.com/article/1", title="Test")
    ]
    mock_get_parser.return_value = mock_parser

    with patch("memory.workers.tasks.blogs.sync_webpage") as mock_sync_webpage:
        mock_sync_webpage.delay.return_value = Mock(id="task-123")

        blogs.sync_article_feed(feed.id)

        # Verify sync_webpage was called with correct tags
        mock_sync_webpage.delay.assert_called_once_with(
            "https://example.com/article/1", expected_tags
        )


def test_sync_all_article_feeds_no_active_feeds(db_session):
    """Test sync_all_article_feeds when no active feeds exist."""
    # Create only inactive feeds
    inactive_feed = ArticleFeed(
        url="https://example.com/inactive.xml",
        title="Inactive Feed",
        active=False,
        check_interval=3600,
    )
    db_session.add(inactive_feed)
    db_session.commit()

    result = blogs.sync_all_article_feeds()

    assert result == []


@patch("memory.workers.tasks.blogs.sync_webpage")
@patch("memory.workers.tasks.blogs.get_archive_fetcher")
def test_sync_website_archive_default_max_pages(
    mock_get_fetcher, mock_sync_delay, db_session
):
    """Test that default max_pages is used when not specified."""
    mock_fetcher = Mock()
    mock_fetcher.max_pages = 100  # Default value
    mock_fetcher.fetch_all_items.return_value = []
    mock_get_fetcher.return_value = mock_fetcher

    result = blogs.sync_website_archive("https://example.com")

    assert result["max_pages_processed"] == 100
    assert mock_fetcher.max_pages == 100  # Should be set to default


@patch("memory.workers.tasks.blogs.sync_webpage")
@patch("memory.workers.tasks.blogs.get_archive_fetcher")
def test_sync_website_archive_empty_results(
    mock_get_fetcher, mock_sync_delay, db_session
):
    """Test archive sync when no articles are found."""
    mock_fetcher = Mock()
    mock_fetcher.max_pages = 100
    mock_fetcher.fetch_all_items.return_value = []
    mock_get_fetcher.return_value = mock_fetcher

    result = blogs.sync_website_archive("https://example.com")

    assert result["articles_found"] == 0
    assert result["new_articles"] == 0
    assert result["task_ids"] == []


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
@patch("memory.workers.tasks.blogs.get_feed_parser")
def test_sync_article_feed_check_interval(
    mock_get_parser,
    check_interval_minutes,
    seconds_since_check,
    should_skip,
    db_session,
):
    """Test sync respects check interval with various timing scenarios."""
    from sqlalchemy import text

    # Mock parser to return None (no parser available) for non-skipped cases
    mock_get_parser.return_value = None

    # Create feed with specific check interval
    feed = ArticleFeed(
        url="https://example.com/interval-test.xml",
        title="Interval Test Feed",
        description="Feed for testing check intervals",
        tags=["test"],
        check_interval=check_interval_minutes,
        active=True,
    )
    db_session.add(feed)
    db_session.flush()

    # Set last_checked_at to specific time in the past
    last_checked_time = datetime.now(timezone.utc) - timedelta(
        seconds=seconds_since_check
    )
    db_session.execute(
        text(
            "UPDATE article_feeds SET last_checked_at = :timestamp WHERE id = :feed_id"
        ),
        {"timestamp": last_checked_time, "feed_id": feed.id},
    )
    db_session.commit()

    result = blogs.sync_article_feed(feed.id)

    if should_skip:
        assert result == {"status": "skipped_recent_check", "feed_id": feed.id}
        # get_feed_parser should not be called when skipping
        mock_get_parser.assert_not_called()
    else:
        # Should proceed with sync, but will fail due to no parser - that's expected
        assert result["status"] == "error"
        assert result["error"] == "No parser available for feed"
        # get_feed_parser should be called when not skipping
        mock_get_parser.assert_called_once()
