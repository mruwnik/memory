"""Dispatch access-control reconciliation when a data source's config changes.

``update_source_access_control`` resolves a data source's ``project_id`` /
``sensitivity`` down onto its content items (SQL rows + Qdrant payloads).
That task is only useful if something *dispatches* it — historically nothing
did, so the resolution never ran.

This module closes that gap with a single chokepoint: a ``before_flush``
listener bumps ``config_version`` whenever a data source's ``project_id`` or
``sensitivity`` actually changes, and an ``after_commit`` listener then
dispatches ``update_source_access_control`` for the affected sources. Doing
it as an ORM event (rather than per-endpoint) means no config-mutating code
path can forget to trigger reconciliation.

A failed dispatch is swallowed (logged, not raised): the commit has already
happened, an API request must not fail because the broker is briefly
unreachable, and ``reconcile_all_access_control`` (the periodic beat task) is
the backstop that re-dispatches anything missed.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect
from sqlalchemy.orm import Session
from sqlalchemy import event

from memory.common.db.models.discord import DiscordChannel, DiscordServer
from memory.common.db.models.slack import SlackChannel, SlackWorkspace
from memory.common.db.models.sources import (
    ArticleFeed,
    CalendarAccount,
    EmailAccount,
    GoogleFolder,
    TranscriptAccount,
)

logger = logging.getLogger(__name__)

# source_type string -> data-source model. The string keys are the contract
# with update_source_access_control / get_items_for_source. Each model's
# primary key (``id``) is exactly the value those consumers expect as
# ``source_id``.
ACCESS_CONTROLLED_SOURCE_MODELS: dict[str, type] = {
    "email_account": EmailAccount,
    "slack_channel": SlackChannel,
    "slack_workspace": SlackWorkspace,
    "discord_channel": DiscordChannel,
    "discord_server": DiscordServer,
    "calendar_account": CalendarAccount,
    "google_folder": GoogleFolder,
    "article_feed": ArticleFeed,
    "transcript_account": TranscriptAccount,
}

_SOURCE_TYPE_BY_MODEL: dict[type, str] = {
    model: source_type
    for source_type, model in ACCESS_CONTROLLED_SOURCE_MODELS.items()
}

# Fields whose change means belonging content must be re-resolved.
ACCESS_CONTROL_FIELDS = ("project_id", "sensitivity")

# session.info key holding (source_type, source_id, config_version) tuples
# staged by before_flush for after_commit to dispatch.
PENDING_DISPATCH_KEY = "_ac_source_dispatch"


def ac_field_value_changed(state, field: str) -> bool:
    """Whether ``field`` was set to a genuinely different *value*.

    ``History.has_changes()`` alone is not enough: SQLAlchemy decides a
    scalar set is "unchanged" by *identity* (``new is old``), so assigning an
    equal-but-distinct object — exactly what the source-config update
    endpoints do, ``account.project_id = updates.project_id`` unconditionally
    — reports a change. Comparing ``added`` vs ``deleted`` by value collapses
    those no-ops, so a PATCH that only renames a source doesn't trigger a
    full (idempotent but expensive) reconciliation. When the old value is
    unknown (expired attribute) ``deleted`` is empty and this conservatively
    reports a change.
    """
    history = state.attrs[field].history
    return history.has_changes() and history.added != history.deleted


@event.listens_for(Session, "before_flush")
def bump_config_version_on_ac_change(session, flush_context, instances):
    """Bump ``config_version`` for data sources whose access control changed.

    Only existing rows (``session.dirty``) are considered — a brand-new
    source has no content items yet, so there is nothing to reconcile until
    items are ingested (and the periodic sweep covers that). The bumped
    version both invalidates in-flight stale jobs and is the argument the
    dispatched task validates against.
    """
    pending = session.info.setdefault(PENDING_DISPATCH_KEY, [])
    for obj in session.dirty:
        source_type = _SOURCE_TYPE_BY_MODEL.get(type(obj))
        if source_type is None:
            continue

        state = inspect(obj)
        if not any(
            ac_field_value_changed(state, field)
            for field in ACCESS_CONTROL_FIELDS
        ):
            continue

        obj.config_version = (obj.config_version or 0) + 1
        pending.append((source_type, obj.id, obj.config_version))


@event.listens_for(Session, "after_commit")
def dispatch_ac_reconciliation(session):
    """Dispatch update_source_access_control for sources changed in this txn."""
    pending = session.info.pop(PENDING_DISPATCH_KEY, [])
    if not pending:
        return

    # Collapse repeats (a source touched by several flushes in one txn) to
    # the last — highest — config_version; earlier dispatches would just be
    # rejected as stale anyway.
    latest: dict[tuple[str, object], int] = {}
    for source_type, source_id, config_version in pending:
        latest[(source_type, source_id)] = config_version

    # Imported lazily: keeps this models-layer module from importing the
    # Celery app at definition time.
    from memory.common.celery_app import app, UPDATE_SOURCE_ACCESS_CONTROL

    for (source_type, source_id), config_version in latest.items():
        try:
            app.send_task(
                UPDATE_SOURCE_ACCESS_CONTROL,
                args=[source_type, source_id, config_version],
            )
        except Exception:
            # Commit already succeeded — never fail the caller over a
            # dispatch hiccup. reconcile_all_access_control re-dispatches.
            logger.exception(
                "Failed to dispatch access-control reconciliation for %s %s",
                source_type,
                source_id,
            )


@event.listens_for(Session, "after_rollback")
def clear_pending_dispatch_on_rollback(session):
    """Drop staged dispatches when the transaction rolls back."""
    session.info.pop(PENDING_DISPATCH_KEY, None)
