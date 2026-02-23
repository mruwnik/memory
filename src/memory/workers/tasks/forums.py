from datetime import datetime, timedelta
import logging
from memory.parsers.lesswrong import fetch_lesswrong_posts, LessWrongPost
from memory.common.db.connection import make_session
from memory.common.db.models import ForumPost
from memory.common.celery_app import app, SYNC_LESSWRONG, SYNC_LESSWRONG_POST
from memory.common.content_processing import (
    check_content_exists,
    create_content_hash,
    create_task_result,
    process_content_item,
    safe_task_execution,
)

logger = logging.getLogger(__name__)

ENGAGEMENT_FIELDS = ("karma", "votes", "score", "comments")


def update_engagement_metrics(
    existing: ForumPost, post: LessWrongPost
) -> bool:
    """Update karma/votes/score/comments on an existing post. Returns True if changed."""
    changed = False
    for field in ENGAGEMENT_FIELDS:
        new_val = post.get(field)
        if new_val is not None and getattr(existing, field) != new_val:
            setattr(existing, field, new_val)
            changed = True
    return changed


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
    since: str | None = None,
    until: str | None = None,
    min_karma: int = 10,
    limit: int = 50,
    cooldown: float = 0.5,
    max_items: int = 1000,
    af: bool = False,
    tags: list[str] = [],
):
    if until:
        end_date = datetime.fromisoformat(until)
    else:
        end_date = datetime.now() - timedelta(hours=8)

    logger.info(f"Syncing LessWrong posts since {since}")

    if since:
        start_date = datetime.fromisoformat(since)
    else:
        start_date = end_date - timedelta(days=30)

    posts = fetch_lesswrong_posts(
        since=start_date,
        until=end_date,
        min_karma=min_karma,
        limit=limit,
        cooldown=cooldown,
        max_items=max_items,
        af=af,
    )

    posts_num, new_posts, updated_posts = 0, 0, 0
    with make_session() as session:
        for post in posts:
            existing = check_content_exists(session, ForumPost, url=post["url"])
            if existing:
                if update_engagement_metrics(existing, post):
                    updated_posts += 1
            else:
                new_posts += 1
                sync_lesswrong_post.delay(post, tags)  # type: ignore[attr-defined]

            if posts_num >= max_items:
                break
            posts_num += 1

        if updated_posts:
            session.commit()
            logger.info(f"Updated engagement metrics for {updated_posts} existing posts")

    return {
        "posts_num": posts_num,
        "new_posts": new_posts,
        "updated_posts": updated_posts,
        "since": since,
        "min_karma": min_karma,
        "max_items": max_items,
        "af": af,
    }
