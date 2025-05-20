"""
Import sub-modules so Celery can register their @app.task decorators.
"""

from memory.workers.tasks import docs, email, comic  # noqa
from memory.workers.tasks.email import SYNC_ACCOUNT, SYNC_ALL_ACCOUNTS, PROCESS_EMAIL
from memory.workers.tasks.maintenance import (
    CLEAN_ALL_COLLECTIONS,
    CLEAN_COLLECTION,
    REINGEST_MISSING_CHUNKS,
)


__all__ = [
    "docs",
    "email",
    "comic",
    "SYNC_ACCOUNT",
    "SYNC_ALL_ACCOUNTS",
    "PROCESS_EMAIL",
    "CLEAN_ALL_COLLECTIONS",
    "CLEAN_COLLECTION",
    "REINGEST_MISSING_CHUNKS",
]
