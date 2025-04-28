"""
Import sub-modules so Celery can register their @app.task decorators.
"""
from memory.workers.tasks import text, photo, ocr, git, rss, docs, email   # noqa
from memory.workers.tasks.email import SYNC_ACCOUNT, SYNC_ALL_ACCOUNTS, PROCESS_EMAIL


__all__ = ["text", "photo", "ocr", "git", "rss", "docs", "email", "SYNC_ACCOUNT", "SYNC_ALL_ACCOUNTS", "PROCESS_EMAIL"]