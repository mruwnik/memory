"""Helpers for propagating data-source access-control changes.

Each data-source row (EmailAccount, SlackChannel, DiscordServer, …) carries
``project_id`` and ``sensitivity`` columns that determine which content
*items* ingested under that source are visible to which users. These
values are denormalized into the Qdrant payload at ingest time
(``project_id`` and ``sensitivity`` are stored on each chunk) so the
search-time access filter can run as a fast Qdrant query without a
JOIN to the data-source tables.

The ``UPDATE_SOURCE_ACCESS_CONTROL`` Celery task exists to re-resolve
those payloads when a data source's config changes. It uses
``config_version`` for stale-job detection: every relevant column update
must bump the row's version so the worker can refuse already-superseded
jobs.

This module wires the API-side mutation surface to the worker-side
propagation surface:

1. ``mark_access_control_changed_if_needed`` — call after applying new
   column values, BEFORE ``db.commit()``. It compares against snapshot
   values, bumps ``config_version`` if either changed, and returns a
   bool so the caller can decide whether to fire the task post-commit.

2. ``enqueue_access_control_propagation`` — call AFTER ``db.commit()``,
   only when the helper above returned True. Sends the Celery task with
   the just-committed ``config_version``.

Splitting the two phases avoids enqueueing a task whose state the DB
hasn't actually committed yet (and would race with subsequent updates).
"""

from __future__ import annotations

import logging
from typing import Any

from memory.common.celery_app import UPDATE_SOURCE_ACCESS_CONTROL, app as celery_app

logger = logging.getLogger(__name__)


# Allowed ``source_type`` strings, matched against the Celery task's
# ``get_data_source_model`` table. Kept as a frozenset so a typo at the
# call site fails loudly rather than enqueuing a job the worker can't
# resolve.
SUPPORTED_SOURCE_TYPES = frozenset(
    {
        "email_account",
        "slack_channel",
        "slack_workspace",
        "discord_channel",
        "discord_server",
        "calendar_account",
        "google_folder",
        "article_feed",
        "transcript_account",
    }
)


def _value_changed(current: Any, snapshot: Any) -> bool:
    """``current != snapshot``, treating None / "" / 0 as distinct values.

    Plain ``!=`` is fine here — the historical pattern of using ``or
    DEFAULT`` to coerce ``None`` to ``"basic"`` (etc.) at read sites is
    a separate problem; for the purpose of "did the operator change
    something?" the raw column comparison is what we want.
    """
    return current != snapshot


def mark_access_control_changed_if_needed(
    source: Any,
    *,
    snapshot_project_id: Any,
    snapshot_sensitivity: Any,
) -> bool:
    """Bump ``config_version`` on ``source`` if either access-control column
    has changed since the snapshot.

    Caller pattern::

        snap_pid, snap_sens = source.project_id, source.sensitivity
        # ... apply updates to source ...
        changed = mark_access_control_changed_if_needed(
            source,
            snapshot_project_id=snap_pid,
            snapshot_sensitivity=snap_sens,
        )
        db.commit()
        if changed:
            enqueue_access_control_propagation("discord_server", source)

    Returns True iff a change was detected (and ``config_version`` bumped).
    """
    pid_changed = _value_changed(source.project_id, snapshot_project_id)
    sens_changed = _value_changed(source.sensitivity, snapshot_sensitivity)
    if not (pid_changed or sens_changed):
        return False
    # Bump the version inside the same transaction as the actual mutation
    # so the worker's stale-job detector sees the new value when the task
    # eventually runs.
    current_version = getattr(source, "config_version", None) or 0
    source.config_version = current_version + 1
    return True


def enqueue_access_control_propagation(
    source_type: str,
    source: Any,
) -> None:
    """Enqueue ``UPDATE_SOURCE_ACCESS_CONTROL`` for the just-committed source.

    ``source.id`` and ``source.config_version`` must reflect the *committed*
    state — the worker will re-read the row and refuse the job if the
    version has moved on (either because a newer mutation came in while
    the task was queued, or because Celery delivered an older retry).

    The send_task call is best-effort: if the broker is unreachable, we
    log and continue rather than rolling back the API mutation. The
    operator already saw their PATCH succeed; a missed propagation is
    a self-healing problem (the next change to the same row enqueues a
    fresh task that resolves the entire row's items, including any
    items the missed task would have touched).
    """
    if source_type not in SUPPORTED_SOURCE_TYPES:
        raise ValueError(
            f"Unknown source_type {source_type!r}; "
            f"expected one of {sorted(SUPPORTED_SOURCE_TYPES)}"
        )
    try:
        celery_app.send_task(
            UPDATE_SOURCE_ACCESS_CONTROL,
            args=[source_type, source.id, source.config_version],
        )
    except Exception as exc:
        # Don't fail the user's request just because the broker is down;
        # surface it loudly in logs so the operator can replay manually.
        logger.error(
            "Failed to enqueue access-control propagation for %s id=%s "
            "config_version=%s: %s",
            source_type,
            source.id,
            source.config_version,
            exc,
        )
