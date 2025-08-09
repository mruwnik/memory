import logging

from memory.common import settings
from memory.common.celery_app import (
    app,
    CLEAN_ALL_COLLECTIONS,
    REINGEST_MISSING_CHUNKS,
    SYNC_ALL_COMICS,
    SYNC_ALL_ARTICLE_FEEDS,
    TRACK_GIT_CHANGES,
    SYNC_LESSWRONG,
)

logger = logging.getLogger(__name__)


app.conf.beat_schedule = {
    "clean-all-collections": {
        "task": CLEAN_ALL_COLLECTIONS,
        "schedule": settings.CLEAN_COLLECTION_INTERVAL,
    },
    "reingest-missing-chunks": {
        "task": REINGEST_MISSING_CHUNKS,
        "schedule": settings.CHUNK_REINGEST_INTERVAL,
    },
    "sync-mail-all": {
        "task": "memory.workers.tasks.email.sync_all_accounts",
        "schedule": settings.EMAIL_SYNC_INTERVAL,
    },
    "sync-all-comics": {
        "task": SYNC_ALL_COMICS,
        "schedule": settings.COMIC_SYNC_INTERVAL,
    },
    "sync-all-article-feeds": {
        "task": SYNC_ALL_ARTICLE_FEEDS,
        "schedule": settings.ARTICLE_FEED_SYNC_INTERVAL,
    },
    "sync-notes-changes": {
        "task": TRACK_GIT_CHANGES,
        "schedule": settings.NOTES_SYNC_INTERVAL,
    },
    "sync-lesswrong": {
        "task": SYNC_LESSWRONG,
        "schedule": settings.LESSWRONG_SYNC_INTERVAL,
    },
}
