"""API endpoints for Article Feed management."""

from typing import cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, HttpUrl
from sqlalchemy.orm import Session

from memory.common.db.connection import get_session
from memory.common.db.models import User
from memory.common.db.models.sources import ArticleFeed
from memory.api.auth import get_current_user

router = APIRouter(prefix="/article-feeds", tags=["article-feeds"])


class ArticleFeedCreate(BaseModel):
    url: HttpUrl
    title: str | None = None
    description: str | None = None
    tags: list[str] = []
    check_interval: int = 1440  # 24 hours in minutes
    active: bool = True


class ArticleFeedUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    check_interval: int | None = None
    active: bool | None = None


class ArticleFeedResponse(BaseModel):
    id: int
    url: str
    title: str | None
    description: str | None
    tags: list[str]
    check_interval: int
    last_checked_at: str | None
    active: bool
    created_at: str
    updated_at: str


class FeedDiscoveryResponse(BaseModel):
    url: str
    title: str | None
    description: str | None


def feed_to_response(feed: ArticleFeed) -> ArticleFeedResponse:
    """Convert an ArticleFeed model to a response model."""
    return ArticleFeedResponse(
        id=cast(int, feed.id),
        url=cast(str, feed.url),
        title=cast(str | None, feed.title),
        description=cast(str | None, feed.description),
        tags=list(feed.tags or []),
        check_interval=cast(int, feed.check_interval),
        last_checked_at=feed.last_checked_at.isoformat()
        if feed.last_checked_at
        else None,
        active=cast(bool, feed.active),
        created_at=feed.created_at.isoformat() if feed.created_at else "",
        updated_at=feed.updated_at.isoformat() if feed.updated_at else "",
    )


@router.get("")
def list_feeds(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[ArticleFeedResponse]:
    """List all article feeds."""
    feeds = db.query(ArticleFeed).all()
    return [feed_to_response(feed) for feed in feeds]


@router.post("")
def create_feed(
    data: ArticleFeedCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ArticleFeedResponse:
    """Create a new article feed."""
    url_str = str(data.url)

    # Check for duplicate URL
    existing = db.query(ArticleFeed).filter(ArticleFeed.url == url_str).first()
    if existing:
        raise HTTPException(status_code=400, detail="Feed with this URL already exists")

    feed = ArticleFeed(
        url=url_str,
        title=data.title or url_str,
        description=data.description,
        tags=data.tags,
        check_interval=data.check_interval,
        active=data.active,
    )
    db.add(feed)
    db.commit()
    db.refresh(feed)

    return feed_to_response(feed)


@router.get("/{feed_id}")
def get_feed(
    feed_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ArticleFeedResponse:
    """Get a single article feed."""
    feed = db.get(ArticleFeed, feed_id)
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    return feed_to_response(feed)


@router.patch("/{feed_id}")
def update_feed(
    feed_id: int,
    updates: ArticleFeedUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ArticleFeedResponse:
    """Update an article feed."""
    feed = db.get(ArticleFeed, feed_id)
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")

    if updates.title is not None:
        feed.title = updates.title
    if updates.description is not None:
        feed.description = updates.description
    if updates.tags is not None:
        feed.tags = updates.tags
    if updates.check_interval is not None:
        feed.check_interval = updates.check_interval
    if updates.active is not None:
        feed.active = updates.active

    db.commit()
    db.refresh(feed)

    return feed_to_response(feed)


@router.delete("/{feed_id}")
def delete_feed(
    feed_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Delete an article feed."""
    feed = db.get(ArticleFeed, feed_id)
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")

    db.delete(feed)
    db.commit()

    return {"status": "deleted"}


@router.post("/{feed_id}/sync")
def trigger_sync(
    feed_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Manually trigger a sync for an article feed."""
    from memory.common.celery_app import app, SYNC_ARTICLE_FEED

    feed = db.get(ArticleFeed, feed_id)
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")

    task = app.send_task(
        SYNC_ARTICLE_FEED,
        args=[feed_id],
    )

    return {"task_id": task.id, "status": "scheduled"}


@router.post("/discover")
def discover_feed(
    url: HttpUrl,
    user: User = Depends(get_current_user),
) -> FeedDiscoveryResponse:
    """Auto-discover feed metadata from a URL."""
    from memory.parsers.feeds import get_feed_parser

    url_str = str(url)
    parser = get_feed_parser(url_str)

    if not parser:
        raise HTTPException(status_code=400, detail="Could not parse feed from URL")

    return FeedDiscoveryResponse(
        url=url_str,
        title=getattr(parser, "title", None),  # type: ignore[arg-type]
        description=getattr(parser, "description", None),  # type: ignore[arg-type]
    )
