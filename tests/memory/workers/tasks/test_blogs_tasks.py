import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from memory.common.db.models import ArticleFeed, BlogPost, DelayedFeedItem
from memory.parsers.feeds import FeedItem
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
        last_sync_at=None,  # Avoid timezone issues
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
def test_sync_webpage_records_feed(
    mock_parse, mock_article, sample_article_feed, db_session, qdrant
):
    mock_parse.return_value = mock_article

    blogs.sync_webpage(
        "https://example.com/article/1", ["test"], feed_id=sample_article_feed.id
    )

    blog_post = db_session.query(BlogPost).filter_by(url=mock_article.url).one()
    assert blog_post.feed_id == sample_article_feed.id


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
    from memory.common.content_processing import create_content_hash

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
            "https://example.com/article/1", ["test", "blog"], sample_article_feed.id
        )

    # Verify last_sync_at was updated
    db_session.refresh(sample_article_feed)
    assert sample_article_feed.last_sync_at is not None


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
    db_session.refresh(sample_article_feed)
    assert sample_article_feed.sync_error == "No parser available for feed"


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
            "https://example.com/article/2", ["test", "blog"], sample_article_feed.id
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
    db_session.refresh(sample_article_feed)
    assert sample_article_feed.sync_error == "Parser error"
    assert sample_article_feed.sync_failing_since is not None


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

        feed = db_session.query(ArticleFeed).filter_by(url="https://example.com").one()
        assert mock_sync_webpage.delay.call_count == 2
        mock_sync_webpage.delay.assert_any_call(
            "https://example.com/archive/1", ["archive"], feed.id
        )
        mock_sync_webpage.delay.assert_any_call(
            "https://example.com/archive/2", ["archive"], feed.id
        )


@patch("memory.workers.tasks.blogs.get_archive_fetcher")
def test_sync_website_archive_no_fetcher(mock_get_fetcher, db_session):
    """Test archive sync when no fetcher is available."""
    mock_get_fetcher.return_value = None

    result = blogs.sync_website_archive("https://example.com")

    assert result == {"status": "error", "error": "No archive fetcher available"}


@patch("memory.workers.tasks.blogs.get_archive_fetcher")
def test_sync_website_archive_persists_feed_when_add_feed_true(
    mock_get_fetcher, mock_archive_fetcher, db_session
):
    """add_feed=True must register a new ArticleFeed row when none exists.

    Regression test: the `if add_feed` branch previously constructed the feed
    in memory but never called `session.add()` / `session.commit()`, so the
    row was discarded when the context manager exited. This is the
    "register feed for ongoing sync" feature being silently broken.
    """
    mock_get_fetcher.return_value = mock_archive_fetcher
    url = "https://example.com"

    # Sanity: no feed for this URL exists yet.
    assert (
        db_session.query(ArticleFeed).filter(ArticleFeed.url == url).first() is None
    )

    with patch("memory.workers.tasks.blogs.sync_webpage") as mock_sync_webpage:
        mock_sync_webpage.delay.side_effect = [Mock(id="t-1"), Mock(id="t-2")]
        blogs.sync_website_archive(url, ["archive"], 50, add_feed=True)

    db_session.expire_all()
    feed = db_session.query(ArticleFeed).filter(ArticleFeed.url == url).first()
    assert feed is not None
    assert feed.url == url
    assert feed.title == url
    assert feed.active is True


@patch("memory.workers.tasks.blogs.get_archive_fetcher")
def test_sync_website_archive_does_not_duplicate_existing_feed(
    mock_get_fetcher, mock_archive_fetcher, db_session
):
    """When a feed already exists, add_feed=True must be a no-op for the row."""
    mock_get_fetcher.return_value = mock_archive_fetcher
    url = "https://example.com"

    # Pre-existing feed with a custom title — must not be overwritten.
    existing = ArticleFeed(
        url=url,
        title="Pre-existing Feed",
        active=True,
        check_interval=60,
        tags=["pre"],
    )
    db_session.add(existing)
    db_session.commit()
    existing_id = existing.id

    with patch("memory.workers.tasks.blogs.sync_webpage") as mock_sync_webpage:
        mock_sync_webpage.delay.side_effect = [Mock(id="t-1"), Mock(id="t-2")]
        blogs.sync_website_archive(url, ["archive"], 50, add_feed=True)

    feeds = db_session.query(ArticleFeed).filter(ArticleFeed.url == url).all()
    assert len(feeds) == 1
    assert feeds[0].id == existing_id
    assert feeds[0].title == "Pre-existing Feed"


@patch("memory.workers.tasks.blogs.get_archive_fetcher")
def test_sync_website_archive_skips_feed_creation_when_add_feed_false(
    mock_get_fetcher, mock_archive_fetcher, db_session
):
    """add_feed=False must leave ArticleFeed table untouched."""
    mock_get_fetcher.return_value = mock_archive_fetcher
    url = "https://example.com"

    with patch("memory.workers.tasks.blogs.sync_webpage") as mock_sync_webpage:
        mock_sync_webpage.delay.side_effect = [Mock(id="t-1"), Mock(id="t-2")]
        blogs.sync_website_archive(url, ["archive"], 50, add_feed=False)

    assert (
        db_session.query(ArticleFeed).filter(ArticleFeed.url == url).first() is None
    )


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

        feed = db_session.query(ArticleFeed).filter_by(url="https://example.com").one()
        mock_sync_webpage.delay.assert_called_once_with(
            "https://example.com/archive/2", ["archive"], feed.id
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
    """Test webpage sync when parsing fails — Celery wrapper re-raises so
    the worker can retry."""
    mock_parse.side_effect = Exception("Parse error")

    with pytest.raises(Exception, match="Parse error"):
        blogs.sync_webpage("https://example.com/error")


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
        last_sync_at=None,  # Avoid timezone issues
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
            "https://example.com/article/1", expected_tags, feed.id
        )


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


def sync_feed(feed_id, *entries):
    """Sync the feed with a parser listing ``entries``; returns the urls queued for ingest."""
    parser = Mock()
    parser.parse_feed.return_value = list(entries)
    with (
        patch("memory.workers.tasks.blogs.get_feed_parser", return_value=parser),
        patch("memory.workers.tasks.blogs.sync_webpage") as sync_webpage,
    ):
        sync_webpage.delay.return_value = Mock(id="task")
        blogs.sync_article_feed(feed_id)
    return [c.args[0] for c in sync_webpage.delay.call_args_list]


def entry(url, hours_ago):
    return FeedItem(title="Post", url=url, published_date=datetime.now(timezone.utc) - timedelta(hours=hours_ago))


@pytest.fixture
def delayed_feed(db_session):
    feed = ArticleFeed(url="https://example.com/paywalled.xml", ingest_delay=24 * 60)
    db_session.add(feed)
    db_session.commit()
    return feed


@pytest.mark.parametrize(
    "ingest_delay, published_hours_ago, ingested",
    [
        (0, 1, True),
        (24 * 60, 1, False),
        (24 * 60, 25, True),
    ],
)
def test_new_entries_wait_out_the_ingest_delay(
    db_session, ingest_delay, published_hours_ago, ingested
):
    feed = ArticleFeed(url="https://example.com/feed.xml", ingest_delay=ingest_delay)
    db_session.add(feed)
    db_session.commit()

    queued = sync_feed(feed.id, entry("https://example.com/a", published_hours_ago))

    assert (queued == ["https://example.com/a"]) is ingested


def test_a_waiting_entry_is_fetched_once_due_even_after_the_feed_drops_it(db_session, delayed_feed):
    assert sync_feed(delayed_feed.id, entry("https://example.com/a", 1)) == []
    db_session.query(DelayedFeedItem).update({"due_at": datetime.now(timezone.utc)})
    db_session.commit()

    assert sync_feed(delayed_feed.id) == ["https://example.com/a"]
    assert sync_feed(delayed_feed.id) == []
    assert db_session.query(DelayedFeedItem).count() == 0


def test_an_entry_seen_again_while_waiting_keeps_its_first_due_time(db_session, delayed_feed):
    sync_feed(delayed_feed.id, entry("https://example.com/a", 1))
    first_due = db_session.query(DelayedFeedItem).one().due_at

    assert sync_feed(delayed_feed.id, entry("https://example.com/a", 1)) == []
    db_session.expire_all()
    assert db_session.query(DelayedFeedItem).one().due_at == first_due
