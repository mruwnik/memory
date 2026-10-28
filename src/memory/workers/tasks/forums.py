from datetime import datetime, timedelta, timezone
import logging
import time
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from memory.parsers.lesswrong import (
    LessWrongComment,
    LessWrongPost,
    fetch_comment_counts,
    fetch_lesswrong_posts,
    fetch_post_comments,
    post_guid_from_url,
)
from memory.common.db.connection import make_session
from memory.common.db.models import ForumComment, ForumPost, ForumSyncConfig
from memory.common.db.models.sources import LESSWRONG
from memory.common.celery_app import (
    app,
    SYNC_LESSWRONG,
    SYNC_LESSWRONG_COMMENTS,
    SYNC_LESSWRONG_IF_DUE,
    SYNC_LESSWRONG_POST,
)
from memory.common.content_processing import (
    check_content_exists,
    clear_item_chunks,
    create_content_hash,
    create_task_result,
    process_content_item,
)
from memory.common.jobs import tracked_task

logger = logging.getLogger(__name__)

ENGAGEMENT_FIELDS = ("karma", "votes", "score", "comments")
# Beat ticks drift by a few seconds, so a sync that is due "now" may look just short of due.
DUE_SLACK = timedelta(minutes=5)
COUNT_BATCH_SIZE = 50
# Comments share the posts' collection; the tag lets a search filter on them.
COMMENT_TAG = "comment"
# Bounds the comment-count requests one run spends on the older-post recheck backlog.
MAX_COUNT_CHECKS_PER_RUN = 2000


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def naive_utc(value: datetime) -> datetime:
    """The LW parser compares against naive UTC publication dates."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


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


def comment_recheck_due(
    published_at: datetime,
    checked_at: datetime | None,
    now: datetime,
    divisor: int,
    max_interval: timedelta,
) -> bool:
    """Older posts get new comments ever more rarely, so they are rechecked ever more rarely."""
    if checked_at is None:
        return True
    interval = min(max((now - published_at) / divisor, timedelta(days=1)), max_interval)
    return now - checked_at >= interval


@app.task(name=SYNC_LESSWRONG_POST)
@tracked_task
def sync_lesswrong_post(
    post: LessWrongPost,
    tags: list[str] | None = None,
):
    tags = tags or []
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


def comment_hash(comment: LessWrongComment) -> bytes:
    # The id keeps identical short comments ("Thanks!") on different posts apart.
    return create_content_hash(comment["content"], comment["comment_id"])


def upsert_comment(
    session: Session, post: ForumPost, comment: LessWrongComment, existing: ForumComment | None
) -> str:
    sha256 = comment_hash(comment)
    if existing:
        existing.karma = comment["karma"]
        existing.votes = comment["votes"]
        if existing.sha256 == sha256:
            return "unchanged"
        clear_item_chunks(existing, session)
        existing.content = comment["content"]
        existing.sha256 = sha256
        existing.size = len(comment["content"].encode("utf-8"))
        process_content_item(existing, session)
        return "edited"

    comment_item = ForumComment(
        post=post,
        comment_id=comment["comment_id"],
        parent_comment_id=comment["parent_comment_id"],
        url=comment["url"],
        author=comment["author"],
        karma=comment["karma"],
        votes=comment["votes"],
        posted_at=comment["posted_at"] and datetime.fromisoformat(comment["posted_at"]),
        content=comment["content"],
        sha256=sha256,
        size=len(comment["content"].encode("utf-8")),
        modality="forum",
        mime_type="text/markdown",
        embed_status="RAW",
        tags=[*post.tags, COMMENT_TAG],
    )
    try:
        process_content_item(comment_item, session)
    except IntegrityError:
        # A manual and a scheduled sync can store the same comment at once; theirs stands.
        session.rollback()
        logger.debug(f"Forum comment for post {post.id} was stored concurrently")
        return "skipped"
    return "new"


def delete_comment(session: Session, comment: ForumComment) -> None:
    clear_item_chunks(comment, session)
    session.delete(comment)


@app.task(name=SYNC_LESSWRONG_COMMENTS)
@tracked_task
def sync_lesswrong_comments(
    post_id: int, guid: str, comment_count: int, min_karma: int
) -> dict[str, Any]:
    """Mirror a post's comments: add new ones, re-embed edited ones, drop ones gone from LW."""
    comments, live_ids = fetch_post_comments(guid, min_karma=min_karma)
    with make_session() as session:
        post = session.get(ForumPost, post_id)
        if not post:
            return {"post_id": post_id, "status": "post_missing"}

        stored = {c.comment_id: c for c in post.forum_comments}
        outcomes = [
            upsert_comment(session, post, comment, stored.get(comment["comment_id"]))
            for comment in comments
        ]
        # Comments under the karma floor are still live, so they stay. A truncated
        # fetch (no ids) can't tell a deleted comment from one past the limit.
        gone = set(stored) - live_ids if live_ids is not None else set()
        for comment_id in gone:
            delete_comment(session, stored[comment_id])

        post.synced_comment_count = comment_count
        post.comments_checked_at = utcnow()
        session.commit()

    return {
        "post_id": post_id,
        **{outcome: outcomes.count(outcome) for outcome in ("new", "edited", "unchanged", "skipped")},
        "deleted": len(gone),
    }


def due_comment_counts(
    config: ForumSyncConfig, window_start: datetime, wanted: int, cooldown: float
) -> tuple[list[tuple[int, str, int]], list[int]]:
    """Live comment counts of older posts due a recheck, until ``wanted`` need a fetch.

    Returns (to_fetch, unchanged): to_fetch as (post id, guid, live count), unchanged as post ids.
    """
    now = utcnow()
    with make_session() as session:
        rows = (
            session.query(
                ForumPost.id,
                ForumPost.url,
                ForumPost.published_at,
                ForumPost.comments_checked_at,
                ForumPost.synced_comment_count,
            )
            .filter(
                ForumPost.published_at < window_start,
                ForumPost.published_at >= now - timedelta(days=config.comment_max_age_days),
            )
            .order_by(ForumPost.published_at.desc())
            .all()
        )
    due = [
        (post_id, guid, synced or 0)
        for post_id, url, published_at, checked_at, synced in rows
        if (guid := post_guid_from_url(url or ""))
        and comment_recheck_due(
            published_at,
            checked_at,
            now,
            config.comment_recheck_divisor,
            timedelta(days=config.comment_recheck_max_days),
        )
    ][:MAX_COUNT_CHECKS_PER_RUN]
    # With a karma floor, comments stored nowhere may have since earned their way in.
    # Posts still in the listing window only refetch on a count change; they get
    # this once they age out of it.
    refetch_unchanged = config.min_comment_karma > 0

    to_fetch: list[tuple[int, str, int]] = []
    unchanged: list[int] = []
    for start in range(0, len(due), COUNT_BATCH_SIZE):
        if len(to_fetch) >= wanted:
            break
        batch = due[start : start + COUNT_BATCH_SIZE]
        try:
            counts = fetch_comment_counts([guid for _, guid, _ in batch])
        except Exception:
            # Its posts stay due, so the next run asks again.
            logger.exception("Failed to fetch LessWrong comment counts")
            continue
        for post_id, guid, synced in batch:
            live = counts.get(guid)
            # A post LW no longer returns is marked checked, so it isn't asked about every run.
            if live is None or (live == synced and not (refetch_unchanged and live)):
                unchanged.append(post_id)
            else:
                to_fetch.append((post_id, guid, live))
        time.sleep(cooldown)
    return to_fetch, unchanged


def mark_comments_checked(post_ids: list[int]) -> None:
    if not post_ids:
        return
    with make_session() as session:
        session.query(ForumPost).filter(ForumPost.id.in_(post_ids)).update(
            {ForumPost.comments_checked_at: utcnow()}, synchronize_session=False
        )
        session.commit()


def queue_comment_fetches(
    candidates: list[tuple[int, str, int]], min_karma: int, cooldown: float
) -> None:
    # Staggered so the fetches keep the cooldown between LW requests however many workers run them.
    for i, (post_id, guid, live_count) in enumerate(candidates):
        sync_lesswrong_comments.apply_async(  # type: ignore[attr-defined]
            args=[post_id, guid, live_count, min_karma], countdown=i * cooldown
        )


@app.task(name=SYNC_LESSWRONG)
@tracked_task
def sync_lesswrong(
    since: str | None = None,
    until: str | None = None,
    min_karma: int | None = None,
    limit: int = 50,
    cooldown: float = 0.5,
    max_items: int | None = None,
    af: bool | None = None,
    tags: list[str] | None = None,
):
    """Sync LessWrong posts and their comments. Arguments left unset come from the forum config."""
    started = utcnow()
    try:
        result = run_sync(since, until, min_karma, limit, cooldown, max_items, af, tags or [])
    except Exception as e:
        record_run(started, None, e)
        raise
    record_run(started, result, None)
    return result


def record_run(started: datetime, result: dict | None, error: BaseException | None) -> None:
    with make_session() as session:
        config = ForumSyncConfig.load(session, LESSWRONG)
        config.last_sync_at = started
        config.last_sync_result = result
        config.record_sync(error)
        session.commit()


def run_sync(
    since: str | None,
    until: str | None,
    min_karma: int | None,
    limit: int,
    cooldown: float,
    max_items: int | None,
    af: bool | None,
    tags: list[str],
) -> dict[str, Any]:
    with make_session() as session:
        config = ForumSyncConfig.load(session, LESSWRONG)
        session.commit()
        session.refresh(config)
        session.expunge(config)

    min_karma = config.min_karma if min_karma is None else min_karma
    max_items = config.max_items if max_items is None else max_items
    af = config.af if af is None else af

    end_date = naive_utc(datetime.fromisoformat(until) if until else utcnow() - timedelta(hours=8))
    if since:
        start_date = naive_utc(datetime.fromisoformat(since))
    else:
        start_date = end_date - timedelta(days=config.days_back)
    logger.info(f"Syncing LessWrong posts since {start_date}")

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
    changed: list[tuple[int, str, int]] = []
    unchanged: list[int] = []
    with make_session() as session:
        for post in posts:
            existing = check_content_exists(session, ForumPost, url=post["url"])
            if existing:
                guid = post_guid_from_url(post["url"])
                live = post["comments"] or 0
                if guid and (existing.synced_comment_count or 0) != live:
                    changed.append((existing.id, guid, live))
                else:
                    unchanged.append(existing.id)
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

    result: dict[str, Any] = {
        "posts_num": posts_num,
        "new_posts": new_posts,
        "updated_posts": updated_posts,
        "since": since,
        "min_karma": min_karma,
        "max_items": max_items,
        "af": af,
        "comment_counts_checked": 0,
        "comment_fetches": 0,
    }
    if not config.comments_enabled:
        return result

    # The in-window posts come first: they are the ones most likely to be gaining comments.
    wanted = config.max_comment_fetches - len(changed)
    if wanted > 0:
        old_changed, old_unchanged = due_comment_counts(
            config, start_date.replace(tzinfo=timezone.utc), wanted, cooldown
        )
        result["comment_counts_checked"] = len(old_changed) + len(old_unchanged)
        changed += old_changed
        unchanged += old_unchanged

    mark_comments_checked(unchanged)
    to_fetch = changed[: config.max_comment_fetches]
    queue_comment_fetches(to_fetch, config.min_comment_karma, cooldown)
    result["comment_fetches"] = len(to_fetch)
    return result


@app.task(name=SYNC_LESSWRONG_IF_DUE)
def sync_lesswrong_if_due():
    """Beat entry point: runs the sync when the forum config says it is due."""
    with make_session() as session:
        ForumSyncConfig.load(session, LESSWRONG)
        session.commit()
        # Locked and stamped before running, so a later tick doesn't start a second sync alongside this one.
        config = (
            session.query(ForumSyncConfig)
            .filter_by(forum=LESSWRONG)
            .with_for_update()
            .populate_existing()
            .one()
        )
        now = utcnow()
        due = config.active and (
            config.last_sync_at is None
            or now - config.last_sync_at >= timedelta(minutes=config.check_interval) - DUE_SLACK
        )
        if due:
            config.last_sync_at = now
        session.commit()
    if not due:
        return {"status": "not_due"}
    return sync_lesswrong()
