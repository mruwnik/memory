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
    github,
    maintenance,
    notes,
    observations,
    people,
    scheduled_calls,
)  # noqa

__all__ = [
    "backup",
    "blogs",
    "comic",
    "discord",
    "ebook",
    "email",
    "forums",
    "github",
    "maintenance",
    "notes",
    "observations",
    "people",
    "scheduled_calls",
]
