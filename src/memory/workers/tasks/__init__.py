"""
Import sub-modules so Celery can register their @app.task decorators.
"""

from memory.workers.tasks import (
    backup,
    blogs,
    comic,
    discord,
    ebook,
    email,
    forums,
    maintenance,
    notes,
    observations,
    scheduled_calls,
)  # noqa

__all__ = [
    "backup",
    "email",
    "comic",
    "blogs",
    "ebook",
    "discord",
    "forums",
    "maintenance",
    "notes",
    "observations",
    "scheduled_calls",
]
