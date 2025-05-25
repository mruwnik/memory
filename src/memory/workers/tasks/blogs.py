import logging
from typing import Iterable

from memory.common.db.connection import make_session
from memory.common.db.models import BlogPost
from memory.common.parsers.blogs import parse_webpage
from memory.workers.celery_app import app
from memory.workers.tasks.content_processing import (
    check_content_exists,
    create_content_hash,
    create_task_result,
    process_content_item,
    safe_task_execution,
)

logger = logging.getLogger(__name__)

SYNC_WEBPAGE = "memory.workers.tasks.blogs.sync_webpage"


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
    article = parse_webpage(url)

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
            session, BlogPost, url=url, sha256=create_content_hash(article.content)
        )
        if existing_post:
            logger.info(f"Blog post already exists: {existing_post.title}")
            return create_task_result(existing_post, "already_exists", url=url)

        return process_content_item(blog_post, "blog", session, tags)
