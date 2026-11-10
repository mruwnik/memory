"""Transcript-provider names and meeting id namespacing.

Lives in ``common`` so the api can use them without importing
``memory.workers.tasks.transcripts`` — the workers.tasks package
eagerly imports every task module on first touch (so Celery's @app.task
decorators register on worker boot), and several of those modules require
heavy dependencies (e.g. ``caldav`` in workers.tasks.calendar) that the
api image deliberately does not install.

The worker's PROVIDERS dispatch dict is asserted to match POLLING_PROVIDERS
at import time (see workers.tasks.transcripts) so the two cannot drift.
"""

# Providers the worker polls with the account's API key.
POLLING_PROVIDERS: list[str] = sorted(["fireflies"])

# Push-only providers: no API key, content arrives at /transcripts/ingest/{id}.
PUSH_ONLY_PROVIDERS: list[str] = sorted(["generic"])

SUPPORTED_PROVIDERS: list[str] = sorted(POLLING_PROVIDERS + PUSH_ONLY_PROVIDERS)


def meeting_external_id(user_id: int, external_id: str | None) -> str | None:
    """The stored Meeting.external_id for an id submitted to POST /meetings.

    Stored meeting ids are unique across all users and sources, so a
    submitter's ids are namespaced to them: they can't collide with (or
    squat) another user's submissions or a transcript account's sync.
    """
    return external_id and f"api:{user_id}:{external_id}"


def submitted_external_id(user_id: int, stored: str) -> str | None:
    """The id ``user_id`` submitted to POST /meetings for a stored
    Meeting.external_id, or None when the meeting didn't come in that way."""
    prefix = f"api:{user_id}:"
    return stored.removeprefix(prefix) if stored.startswith(prefix) else None
