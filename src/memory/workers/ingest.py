import logging

from memory.common import settings
from memory.workers.celery_app import app
from memory.workers.tasks import CLEAN_ALL_COLLECTIONS, REINGEST_MISSING_CHUNKS

logger = logging.getLogger(__name__)


app.conf.beat_schedule = {
    "sync-mail-all": {
        "task": "memory.workers.tasks.email.sync_all_accounts",
        "schedule": settings.EMAIL_SYNC_INTERVAL,
    },
    "clean-all-collections": {
        "task": CLEAN_ALL_COLLECTIONS,
        "schedule": settings.CLEAN_COLLECTION_INTERVAL,
    },
    "reingest-missing-chunks": {
        "task": REINGEST_MISSING_CHUNKS,
        "schedule": settings.CHUNK_REINGEST_INTERVAL,
    },
}
