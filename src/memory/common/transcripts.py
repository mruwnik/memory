"""Canonical list of transcript-provider names.

Lives in ``common`` so the api can validate ``provider`` values without
importing ``memory.workers.tasks.transcripts`` — the workers.tasks package
eagerly imports every task module on first touch (so Celery's @app.task
decorators register on worker boot), and several of those modules require
heavy dependencies (e.g. ``caldav`` in workers.tasks.calendar) that the
api image deliberately does not install.

The worker's PROVIDERS dispatch dict is asserted to match this tuple at
import time (see workers.tasks.transcripts) so the two cannot drift.
"""

SUPPORTED_PROVIDERS: list[str] = sorted(["fireflies"])
