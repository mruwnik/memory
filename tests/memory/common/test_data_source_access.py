"""Tests for data-source access-control propagation helpers.

These helpers wire the API-side mutation surface (PATCH endpoints on
``EmailAccount`` / ``SlackChannel`` / etc.) to the Celery worker that
re-resolves Qdrant payloads when a data-source's project_id or
sensitivity changes. The propagation task already existed but was never
enqueued; these helpers are what fixes that.

The tests are hermetic — no broker, no DB. They exercise:

* ``mark_access_control_changed_if_needed`` correctly detects changes
  on each access-control column independently and bumps
  ``config_version`` exactly once per call.
* ``enqueue_access_control_propagation`` validates source_type, calls
  ``celery_app.send_task`` with the right (task_name, args), and is
  resilient to broker outages (logs but does not raise).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from memory.common.celery_app import UPDATE_SOURCE_ACCESS_CONTROL
from memory.common.data_source_access import (
    SUPPORTED_SOURCE_TYPES,
    enqueue_access_control_propagation,
    mark_access_control_changed_if_needed,
)


def make_source(
    *,
    project_id: int | None = None,
    sensitivity: str = "basic",
    config_version: int = 1,
    source_id: int = 42,
) -> SimpleNamespace:
    """Build a minimal stand-in for a data-source row."""
    return SimpleNamespace(
        id=source_id,
        project_id=project_id,
        sensitivity=sensitivity,
        config_version=config_version,
    )


# ====== mark_access_control_changed_if_needed ======


def test_mark_no_change_returns_false_no_bump():
    src = make_source(project_id=7, sensitivity="basic", config_version=3)
    changed = mark_access_control_changed_if_needed(
        src, snapshot_project_id=7, snapshot_sensitivity="basic"
    )
    assert changed is False
    assert src.config_version == 3  # unbumped


def test_mark_project_id_changed_bumps_version():
    src = make_source(project_id=7, sensitivity="basic", config_version=3)
    changed = mark_access_control_changed_if_needed(
        src, snapshot_project_id=5, snapshot_sensitivity="basic"
    )
    assert changed is True
    assert src.config_version == 4


def test_mark_sensitivity_changed_bumps_version():
    src = make_source(
        project_id=7, sensitivity="confidential", config_version=3
    )
    changed = mark_access_control_changed_if_needed(
        src, snapshot_project_id=7, snapshot_sensitivity="basic"
    )
    assert changed is True
    assert src.config_version == 4


def test_mark_both_changed_bumps_only_once():
    """A single PATCH that touches both columns must bump config_version
    exactly once — bumping twice would falsely bypass a stale-job that
    other workers had already started against the same row."""
    src = make_source(project_id=99, sensitivity="public", config_version=3)
    changed = mark_access_control_changed_if_needed(
        src, snapshot_project_id=7, snapshot_sensitivity="basic"
    )
    assert changed is True
    assert src.config_version == 4


@pytest.mark.parametrize(
    "snap_pid, snap_sens, new_pid, new_sens, expected",
    [
        # None → int (initial assignment) is a real change.
        (None, "basic", 7, "basic", True),
        # int → None (clear) is a real change.
        (7, "basic", None, "basic", True),
        # "basic" → "" is a real change.
        (7, "basic", 7, "", True),
        # Whitespace differences are real (we don't normalize).
        (7, "basic", 7, "basic ", True),
    ],
)
def test_mark_edge_value_transitions(snap_pid, snap_sens, new_pid, new_sens, expected):
    src = make_source(project_id=new_pid, sensitivity=new_sens, config_version=1)
    assert (
        mark_access_control_changed_if_needed(
            src, snapshot_project_id=snap_pid, snapshot_sensitivity=snap_sens
        )
        is expected
    )


def test_mark_handles_missing_config_version_attribute():
    """Defensive: a source row whose config_version column was migrated in
    later might come back from the ORM with the attribute unset / None.
    We treat that as 0 and bump to 1 rather than raising."""
    src = SimpleNamespace(
        id=1, project_id=None, sensitivity="basic", config_version=None
    )
    changed = mark_access_control_changed_if_needed(
        src, snapshot_project_id=99, snapshot_sensitivity="basic"
    )
    assert changed is True
    assert src.config_version == 1


# ====== enqueue_access_control_propagation ======


@pytest.mark.parametrize("source_type", sorted(SUPPORTED_SOURCE_TYPES))
def test_enqueue_sends_task_with_correct_args(source_type):
    src = make_source(
        project_id=7, sensitivity="confidential", config_version=4, source_id=42
    )
    fake_send = MagicMock()
    with patch(
        "memory.common.data_source_access.celery_app.send_task", fake_send
    ):
        enqueue_access_control_propagation(source_type, src)

    fake_send.assert_called_once_with(
        UPDATE_SOURCE_ACCESS_CONTROL,
        args=[source_type, 42, 4],
    )


def test_enqueue_unknown_source_type_raises():
    """Typo'd source_type must not silently enqueue a job the worker can't
    resolve — fail loudly at the call site."""
    src = make_source()
    fake_send = MagicMock()
    with patch(
        "memory.common.data_source_access.celery_app.send_task", fake_send
    ):
        with pytest.raises(ValueError, match="Unknown source_type"):
            enqueue_access_control_propagation("not_a_real_type", src)
    fake_send.assert_not_called()


def test_enqueue_swallows_broker_failure(caplog):
    """If the broker is unreachable, we log and continue. The user's PATCH
    already succeeded — failing the request now would surprise them, and
    the next config change to the same row enqueues a fresh task that
    covers the same items anyway."""
    src = make_source(config_version=2)
    fake_send = MagicMock(side_effect=ConnectionError("broker down"))
    with patch(
        "memory.common.data_source_access.celery_app.send_task", fake_send
    ):
        with caplog.at_level("ERROR", logger="memory.common.data_source_access"):
            # Must not raise.
            enqueue_access_control_propagation("email_account", src)

    fake_send.assert_called_once()
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "Failed to enqueue access-control propagation" in log_text
    assert "email_account" in log_text


def test_enqueue_uses_committed_config_version():
    """Regression: the task arg is read from ``source.config_version``
    AFTER the caller's commit — it must reflect the bumped value, not
    the snapshot. The fix is procedural (caller pattern), but we pin
    that the helper does what it says."""
    src = make_source(config_version=99)
    fake_send = MagicMock()
    with patch(
        "memory.common.data_source_access.celery_app.send_task", fake_send
    ):
        enqueue_access_control_propagation("slack_channel", src)
    args = fake_send.call_args[1]["args"]
    assert args[2] == 99
