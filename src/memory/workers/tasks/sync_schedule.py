from datetime import datetime, timezone

from sqlalchemy import func, or_, update

from memory.common.celery_app import (
    SYNC_ACCOUNT,
    SYNC_ARTICLE_FEED,
    SYNC_CALENDAR_ACCOUNT,
    SYNC_DUE_SOURCES,
    SYNC_GITHUB_REPO,
    SYNC_GOOGLE_FOLDER,
    SYNC_LESSWRONG,
    app,
)
from memory.common.db.connection import make_session
from memory.common.db.models import (
    ArticleFeed,
    CalendarAccount,
    EmailAccount,
    ForumSyncConfig,
    GithubRepo,
    GoogleFolder,
)

# Each polled source model and the task that syncs one of its rows, given the row's id.
POLLED_SOURCES = [
    (ArticleFeed, SYNC_ARTICLE_FEED),
    (EmailAccount, SYNC_ACCOUNT),
    (GithubRepo, SYNC_GITHUB_REPO),
    (GoogleFolder, SYNC_GOOGLE_FOLDER),
    (CalendarAccount, SYNC_CALENDAR_ACCOUNT),
]


def claim_due(session, model, key, now: datetime) -> list:
    """Stamp every due row as queued and return their keys.

    One UPDATE, so a row is claimed once even if two ticks overlap.
    """
    interval = func.make_interval(0, 0, 0, 0, 0, model.check_interval)
    return list(
        session.execute(
            update(model)
            .where(
                model.active,
                or_(model.sync_queued_at.is_(None), model.sync_queued_at + interval <= now),
            )
            .values(sync_queued_at=now)
            .returning(key)
        ).scalars()
    )


@app.task(name=SYNC_DUE_SOURCES)
def sync_due_sources() -> int:
    """Beat entry point: queue a sync for every polled source whose check_interval has passed."""
    now = datetime.now(timezone.utc)
    queued = 0
    with make_session() as session:
        for model, task in POLLED_SOURCES:
            for source_id in claim_due(session, model, model.id, now):
                app.send_task(task, args=[source_id])
                queued += 1
        # The forum sync reads its settings from the config row, so it takes no arguments.
        for _ in claim_due(session, ForumSyncConfig, ForumSyncConfig.forum, now):
            app.send_task(SYNC_LESSWRONG, args=[])
            queued += 1
        session.commit()
    return queued
