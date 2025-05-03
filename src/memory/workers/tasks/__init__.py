"""
Import sub-modules so Celery can register their @app.task decorators.
"""
from memory.workers.tasks import docs, email   # noqa
from memory.workers.tasks.email import SYNC_ACCOUNT, SYNC_ALL_ACCOUNTS, PROCESS_EMAIL


__all__ = ["docs", "email", "SYNC_ACCOUNT", "SYNC_ALL_ACCOUNTS", "PROCESS_EMAIL"]