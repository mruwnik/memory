"""
Import sub-modules so Celery can register their @app.task decorators.
"""

from memory.workers.tasks import (
    backup,
    blogs,
    calendar,
    comic,
    discord,
    ebook,
    email,
    forums,
    github,
    google_drive,
    maintenance,
    meetings,
    notes,
    observations,
    people,
    photo,
    proactive,
    scheduled_calls,
)  # noqa

__all__ = [
    "backup",
    "blogs",
    "calendar",
    "comic",
    "discord",
    "ebook",
    "email",
    "forums",
    "github",
    "google_drive",
    "maintenance",
    "meetings",
    "notes",
    "observations",
    "people",
    "photo",
    "proactive",
    "scheduled_calls",
]
