import logging

from memory.common import settings
from memory.workers.celery_app import app
from memory.workers.tasks import CLEAN_ALL_COLLECTIONS, REINGEST_MISSING_CHUNKS

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
        "task": "memory.workers.tasks.comic.sync_all_comics",
        "schedule": settings.COMIC_SYNC_INTERVAL,
    },
    "sync-all-article-feeds": {
        "task": "memory.workers.tasks.blogs.sync_all_article_feeds",
        "schedule": settings.ARTICLE_FEED_SYNC_INTERVAL,
    },
}
