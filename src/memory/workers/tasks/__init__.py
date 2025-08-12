"""
Import sub-modules so Celery can register their @app.task decorators.
"""

from memory.workers.tasks import (
    email,
    comic,
    blogs,
    ebook,
    forums,
    maintenance,
    notes,
    observations,
    scheduled_calls,
)  # noqa


__all__ = [
    "email",
    "comic",
    "blogs",
    "ebook",
    "forums",
    "maintenance",
    "notes",
    "observations",
    "scheduled_calls",
]
