from datetime import datetime, timedelta
import logging

from memory.parsers.lesswrong import fetch_lesswrong_posts, LessWrongPost
from memory.common.db.connection import make_session
from memory.common.db.models import ForumPost
from memory.common.celery_app import app, SYNC_LESSWRONG, SYNC_LESSWRONG_POST
from memory.workers.tasks.content_processing import (
    check_content_exists,
    create_content_hash,
    create_task_result,
    process_content_item,
    safe_task_execution,
)

logger = logging.getLogger(__name__)


@app.task(name=SYNC_LESSWRONG_POST)
@safe_task_execution
def sync_lesswrong_post(
    post: LessWrongPost,
    tags: list[str] = [],
):
    logger.info(f"Syncing LessWrong post {post['url']}")
    sha256 = create_content_hash(post["content"])

    post["tags"] = list(set(post["tags"] + tags))
    post_obj = ForumPost(
        embed_status="RAW",
        size=len(post["content"].encode("utf-8")),
        modality="forum",
        mime_type="text/markdown",
        sha256=sha256,
        **{k: v for k, v in post.items() if hasattr(ForumPost, k)},
    )

    with make_session() as session:
        existing_post = check_content_exists(
            session, ForumPost, url=post_obj.url, sha256=sha256
        )
        if existing_post:
            logger.info(f"LessWrong post already exists: {existing_post.title}")
            return create_task_result(existing_post, "already_exists", url=post_obj.url)

        return process_content_item(post_obj, session)


@app.task(name=SYNC_LESSWRONG)
@safe_task_execution
def sync_lesswrong(
    since: str = (datetime.now() - timedelta(days=30)).isoformat(),
    min_karma: int = 10,
    limit: int = 50,
    cooldown: float = 0.5,
    max_items: int = 1000,
    af: bool = False,
    tags: list[str] = [],
):
    logger.info(f"Syncing LessWrong posts since {since}")
    start_date = datetime.fromisoformat(since)
    posts = fetch_lesswrong_posts(start_date, min_karma, limit, cooldown, max_items, af)

    posts_num, new_posts = 0, 0
    with make_session() as session:
        for post in posts:
            if not check_content_exists(session, ForumPost, url=post["url"]):
                new_posts += 1
                sync_lesswrong_post.delay(post, tags)

            if posts_num >= max_items:
                break
            posts_num += 1

    return {
        "posts_num": posts_num,
        "new_posts": new_posts,
        "since": since,
        "min_karma": min_karma,
        "max_items": max_items,
        "af": af,
    }
