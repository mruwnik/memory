"""
Import sub-modules so Celery can register their @app.task decorators.
"""

from memory.workers.tasks import email, comic, blogs, ebook, forums  # noqa
from memory.workers.tasks.blogs import (
    SYNC_WEBPAGE,
    SYNC_ARTICLE_FEED,
    SYNC_ALL_ARTICLE_FEEDS,
    SYNC_WEBSITE_ARCHIVE,
)
from memory.workers.tasks.comic import SYNC_ALL_COMICS, SYNC_SMBC, SYNC_XKCD
from memory.workers.tasks.ebook import SYNC_BOOK
from memory.workers.tasks.email import SYNC_ACCOUNT, SYNC_ALL_ACCOUNTS, PROCESS_EMAIL
from memory.workers.tasks.forums import SYNC_LESSWRONG, SYNC_LESSWRONG_POST
from memory.workers.tasks.maintenance import (
    CLEAN_ALL_COLLECTIONS,
    CLEAN_COLLECTION,
    REINGEST_MISSING_CHUNKS,
    REINGEST_CHUNK,
    REINGEST_ITEM,
)


__all__ = [
    "email",
    "comic",
    "blogs",
    "ebook",
    "forums",
    "SYNC_WEBPAGE",
    "SYNC_ARTICLE_FEED",
    "SYNC_ALL_ARTICLE_FEEDS",
    "SYNC_WEBSITE_ARCHIVE",
    "SYNC_ALL_COMICS",
    "SYNC_SMBC",
    "SYNC_XKCD",
    "SYNC_BOOK",
    "SYNC_ACCOUNT",
    "SYNC_LESSWRONG",
    "SYNC_LESSWRONG_POST",
    "SYNC_ALL_ACCOUNTS",
    "PROCESS_EMAIL",
    "CLEAN_ALL_COLLECTIONS",
    "CLEAN_COLLECTION",
    "REINGEST_MISSING_CHUNKS",
    "REINGEST_CHUNK",
    "REINGEST_ITEM",
]
