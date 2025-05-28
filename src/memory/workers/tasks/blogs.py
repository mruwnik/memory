import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable, cast

from memory.common.db.connection import make_session
from memory.common.db.models import ArticleFeed, BlogPost
from memory.parsers.blogs import parse_webpage
from memory.parsers.feeds import get_feed_parser
from memory.parsers.archives import get_archive_fetcher
from memory.workers.celery_app import app, BLOGS_ROOT
from memory.workers.tasks.content_processing import (
    check_content_exists,
    create_content_hash,
    create_task_result,
    process_content_item,
    safe_task_execution,
)

logger = logging.getLogger(__name__)

SYNC_WEBPAGE = f"{BLOGS_ROOT}.sync_webpage"
SYNC_ARTICLE_FEED = f"{BLOGS_ROOT}.sync_article_feed"
SYNC_ALL_ARTICLE_FEEDS = f"{BLOGS_ROOT}.sync_all_article_feeds"
SYNC_WEBSITE_ARCHIVE = f"{BLOGS_ROOT}.sync_website_archive"


@app.task(name=SYNC_WEBPAGE)
@safe_task_execution
def sync_webpage(url: str, tags: Iterable[str] = []) -> dict:
    """
    Synchronize a webpage from a URL.

    Args:
        url: URL of the webpage to parse and store
        tags: Additional tags to apply to the content

    Returns:
        dict: Summary of what was processed
    """
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
    )

    with make_session() as session:
        existing_post = check_content_exists(
            session, BlogPost, url=article.url, sha256=blog_post.sha256
        )
        if existing_post:
            logger.info(f"Blog post already exists: {existing_post.title}")
            return create_task_result(existing_post, "already_exists", url=article.url)

        return process_content_item(blog_post, session)


@app.task(name=SYNC_ARTICLE_FEED)
@safe_task_execution
def sync_article_feed(feed_id: int) -> dict:
    """
    Synchronize articles from a specific ArticleFeed.

    Args:
        feed_id: ID of the ArticleFeed to sync

    Returns:
        dict: Summary of sync operation including stats
    """
    with make_session() as session:
        feed = session.query(ArticleFeed).filter(ArticleFeed.id == feed_id).first()
        if not feed or not cast(bool, feed.active):
            logger.warning(f"Feed {feed_id} not found or inactive")
            return {"status": "error", "error": "Feed not found or inactive"}

        last_checked_at = cast(datetime | None, feed.last_checked_at)
        if last_checked_at and datetime.now(timezone.utc) - last_checked_at < timedelta(
            minutes=cast(int, feed.check_interval)
        ):
            logger.info(f"Feed {feed_id} checked too recently, skipping")
            return {"status": "skipped_recent_check", "feed_id": feed_id}

        logger.info(f"Syncing feed: {feed.title} ({feed.url})")

        parser = get_feed_parser(cast(str, feed.url), last_checked_at)
        if not parser:
            logger.error(f"No parser available for feed: {feed.url}")
            return {"status": "error", "error": "No parser available for feed"}

        articles_found = 0
        new_articles = 0
        errors = 0
        task_ids = []

        try:
            for feed_item in parser.parse_feed():
                articles_found += 1

                existing = check_content_exists(session, BlogPost, url=feed_item.url)
                if existing:
                    continue

                feed_tags = cast(list[str] | None, feed.tags) or []
                task_ids.append(sync_webpage.delay(feed_item.url, feed_tags).id)
                new_articles += 1

                logger.info(f"Scheduled sync for: {feed_item.title} ({feed_item.url})")

        except Exception as e:
            logger.error(f"Error parsing feed {feed.url}: {e}")
            errors += 1

        feed.last_checked_at = datetime.now(timezone.utc)  # type: ignore
        session.commit()

        return {
            "status": "completed",
            "feed_id": feed_id,
            "feed_title": feed.title,
            "feed_url": feed.url,
            "articles_found": articles_found,
            "new_articles": new_articles,
            "errors": errors,
            "task_ids": task_ids,
        }


@app.task(name=SYNC_ALL_ARTICLE_FEEDS)
def sync_all_article_feeds() -> list[dict]:
    """
    Trigger sync for all active ArticleFeeds.

    Returns:
        List of task results for each feed sync
    """
    with make_session() as session:
        active_feeds = session.query(ArticleFeed).filter(ArticleFeed.active).all()

        results = [
            {
                "feed_id": feed.id,
                "feed_title": feed.title,
                "feed_url": feed.url,
                "task_id": sync_article_feed.delay(feed.id).id,
            }
            for feed in active_feeds
        ]
        logger.info(f"Scheduled sync for {len(results)} active feeds")
        return results


@app.task(name=SYNC_WEBSITE_ARCHIVE)
@safe_task_execution
def sync_website_archive(
    url: str, tags: Iterable[str] = [], max_pages: int = 100
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
    logger.info(f"Starting archive sync for: {url}")

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

    for feed_item in fetcher.fetch_all_items():
        articles_found += 1

        with make_session() as session:
            existing = check_content_exists(session, BlogPost, url=feed_item.url)
            if existing:
                continue

        task_ids.append(sync_webpage.delay(feed_item.url, list(tags)).id)
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
