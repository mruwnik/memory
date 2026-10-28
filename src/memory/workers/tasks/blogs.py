import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable, cast

from memory.common.db.connection import make_session
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert

from memory.common.db.models import ArticleFeed, BlogPost, DelayedFeedItem
from memory.parsers.blogs import parse_webpage
from memory.parsers.feeds import FeedItem, get_feed_parser
from memory.parsers.archives import get_archive_fetcher
from memory.common.celery_app import (
    app,
    SYNC_WEBPAGE,
    SYNC_ARTICLE_FEED,
    ADD_ARTICLE_FEED,
    SYNC_WEBSITE_ARCHIVE,
)
from memory.common.content_processing import (
    check_content_exists,
    create_content_hash,
    create_task_result,
    process_content_item,
)
from memory.common.jobs import tracked_task

logger = logging.getLogger(__name__)


@app.task(name=SYNC_WEBPAGE)
@tracked_task
def sync_webpage(
    url: str, tags: Iterable[str] | None = None, feed_id: int | None = None
) -> dict:
    """
    Synchronize a webpage from a URL.

    Args:
        url: URL of the webpage to parse and store
        tags: Additional tags to apply to the content
        feed_id: ArticleFeed the page came from; the post inherits its access
            control and owner

    Returns:
        dict: Summary of what was processed
    """
    tags = tags or []
    logger.info(f"Syncing webpage: {url}")
    article = parse_webpage(url)
    logger.debug(f"Article: {article.title} - {article.url}")

    if not article.content:
        logger.warning(f"Article content too short or empty: {url}")
        return {
            "url": url,
            "title": article.title,
            "status": "skipped_short_content",
            "content_length": 0,
        }

    blog_post = BlogPost(
        url=article.url,
        title=article.title,
        published=article.published_date,
        content=article.content,
        sha256=create_content_hash(article.content),
        modality="blog",
        tags=tags,
        mime_type="text/markdown",
        size=len(article.content.encode("utf-8")),
        images=[image for image in article.images],
        feed_id=feed_id,
    )

    with make_session() as session:
        existing_post = check_content_exists(
            session, BlogPost, url=article.url, sha256=blog_post.sha256
        )
        if existing_post:
            logger.info(f"Blog post already exists: {existing_post.title}")
            return create_task_result(existing_post, "already_exists", url=article.url)

        return process_content_item(blog_post, session)


def delay_entry(session, feed: ArticleFeed, item: FeedItem, now: datetime) -> None:
    """Park a new entry until its feed's ingest_delay has passed since it was published.

    Parked rather than skipped: the feed may 304 or drop the entry before then. An entry
    already waiting keeps its first due time.
    """
    published = item.published_date if isinstance(item.published_date, datetime) else now
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    due_at = min(published, now) + timedelta(minutes=feed.ingest_delay)
    session.execute(
        insert(DelayedFeedItem)
        .values(url=item.url, feed_id=feed.id, due_at=due_at)
        .on_conflict_do_nothing()
    )


def pop_due_entries(session, feed_id: int, now: datetime) -> list[str]:
    """Remove and return the feed's parked entries that are now due."""
    return list(
        session.execute(
            delete(DelayedFeedItem)
            .where(DelayedFeedItem.feed_id == feed_id, DelayedFeedItem.due_at <= now)
            .returning(DelayedFeedItem.url)
        ).scalars()
    )


@app.task(name=SYNC_ARTICLE_FEED)
@tracked_task
def sync_article_feed(feed_id: int) -> dict:
    """
    Synchronize articles from a specific ArticleFeed.

    Args:
        feed_id: ID of the ArticleFeed to sync

    Returns:
        dict: Summary of sync operation including stats
    """
    from memory.common.ssrf import UnsafeURLError, validate_public_url

    with make_session() as session:
        feed = session.query(ArticleFeed).filter(ArticleFeed.id == feed_id).first()
        if not feed or not cast(bool, feed.active):
            logger.warning(f"Feed {feed_id} not found or inactive")
            return {"status": "error", "error": "Feed not found or inactive"}

        # Defense in depth: re-validate at fetch time. Even if the URL
        # passed the gate at create_feed/sync time, DNS could have been
        # rebound to a private range, or a row could have been written
        # before the gate existed.
        try:
            validate_public_url(cast(str, feed.url))
        except UnsafeURLError as exc:
            logger.warning(f"Feed {feed_id} URL rejected by SSRF gate: {exc}")
            feed.record_sync(f"URL not allowed: {exc}")
            session.commit()
            return {"status": "error", "error": f"URL not allowed: {exc}"}

        logger.info(f"Syncing feed: {feed.title} ({feed.url})")

        parser = get_feed_parser(feed.url, feed.last_sync_at)
        if not parser:
            logger.error(f"No parser available for feed: {feed.url}")
            feed.record_sync("No parser available for feed")
            session.commit()
            return {"status": "error", "error": "No parser available for feed"}

        articles_found = 0
        new_urls: list[str] = []
        errors = 0
        error: Exception | None = None
        now = datetime.now(timezone.utc)

        try:
            for feed_item in parser.parse_feed():
                if not feed_item.url:
                    continue

                articles_found += 1

                if check_content_exists(session, BlogPost, url=feed_item.url):
                    continue

                if feed.ingest_delay:
                    delay_entry(session, feed, feed_item, now)
                else:
                    new_urls.append(feed_item.url)

        except Exception as e:
            logger.error(f"Error parsing feed {feed.url}: {e}")
            errors += 1
            error = e

        new_urls += pop_due_entries(session, feed.id, now)
        feed_tags = list(feed.tags or [])
        task_ids = [sync_webpage.delay(url, feed_tags, feed.id).id for url in new_urls]  # type: ignore[attr-defined]

        feed.last_sync_at = now
        feed.record_sync(error)
        session.commit()

        return {
            "status": "completed",
            "feed_id": feed_id,
            "feed_title": feed.title,
            "feed_url": feed.url,
            "articles_found": articles_found,
            "new_articles": len(new_urls),
            "errors": errors,
            "task_ids": task_ids,
        }


@app.task(name=ADD_ARTICLE_FEED)
@tracked_task
def add_article_feed(
    url: str,
    title: str | None = None,
    description: str | None = None,
    tags: Iterable[str] | None = None,
    active: bool = True,
    check_interval: int = 60 * 24,  # 24 hours
) -> dict:
    """
    Add a new ArticleFeed.

    Args:
        url: URL of the feed
        title: Title of the feed (optional)
        description: Description of the feed (optional)
        tags: Tags to apply to the feed
        active: Whether the feed is active
        check_interval: Interval in minutes to check the feed

    Returns:
        dict: Summary of the added feed
    """
    tags = tags or []
    with make_session() as session:
        feed = session.query(ArticleFeed).filter(ArticleFeed.url == url).first()
        if feed:
            logger.info(f"Feed already exists: {url}")
            return {"status": "error", "error": "Feed already exists"}

        feed = ArticleFeed(
            url=url,
            title=title or url,
            description=description,
            active=active,
            check_interval=check_interval,
            tags=tags,
        )
        session.add(feed)
        session.commit()
        return {"status": "success", "feed_id": feed.id}


@app.task(name=SYNC_WEBSITE_ARCHIVE)
@tracked_task
def sync_website_archive(
    url: str,
    tags: Iterable[str] | None = None,
    max_pages: int = 100,
    add_feed: bool = True,
) -> dict:
    """
    Synchronize all articles from a website's archive.

    Args:
        url: Base URL of the website to sync
        tags: Additional tags to apply to all articles
        max_pages: Maximum number of pages to process

    Returns:
        dict: Summary of archive sync operation
    """
    tags = tags or []
    logger.info(f"Starting archive sync for: {url}")

    feed_id = None
    if add_feed:
        with make_session() as session:
            feed = session.query(ArticleFeed).filter(ArticleFeed.url == url).first()
            if not feed:
                feed = ArticleFeed(
                    url=url,
                    title=url,
                    active=True,
                )
                session.add(feed)
                session.commit()
            feed_id = feed.id

    # Get archive fetcher for the website
    fetcher = get_archive_fetcher(url)
    if not fetcher:
        logger.error(f"No archive fetcher available for: {url}")
        return {"status": "error", "error": "No archive fetcher available"}

    # Override max_pages if provided
    fetcher.max_pages = max_pages

    articles_found = 0
    new_articles = 0
    task_ids = []

    with make_session() as session:
        for feed_item in fetcher.fetch_all_items():
            articles_found += 1

            existing = check_content_exists(session, BlogPost, url=feed_item.url)
            if existing:
                continue

            task_ids.append(sync_webpage.delay(feed_item.url, list(tags), feed_id).id)  # type: ignore[attr-defined]
            new_articles += 1

            logger.info(f"Scheduled sync for: {feed_item.title} ({feed_item.url})")

    return {
        "status": "completed",
        "website_url": url,
        "articles_found": articles_found,
        "new_articles": new_articles,
        "task_ids": task_ids,
        "max_pages_processed": fetcher.max_pages,
    }
