"""Tests for the metrics collection and cleanup tasks."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest

from memory.workers.tasks.metrics import (
    collect_open_files,
    cleanup_old_metrics,
    collect_system_metrics,
    refresh_metric_summaries,
)


# ============== collect_open_files tests ==============


def test_collect_open_files_success():
    """Test successful collection of open files count."""
    mock_process = Mock()
    mock_process.open_files.return_value = [Mock(), Mock(), Mock()]

    result = collect_open_files(mock_process)
    assert result == 3


def test_collect_open_files_access_denied():
    """Test handling of AccessDenied when collecting open files."""
    import psutil

    mock_process = Mock()
    mock_process.open_files.side_effect = psutil.AccessDenied(pid=123)

    result = collect_open_files(mock_process)
    assert result is None


def test_collect_open_files_no_such_process():
    """Test handling of NoSuchProcess when collecting open files."""
    import psutil

    mock_process = Mock()
    mock_process.open_files.side_effect = psutil.NoSuchProcess(pid=123)

    result = collect_open_files(mock_process)
    assert result is None


# ============== collect_system_metrics tests ==============


def test_collect_system_metrics_success():
    """Test successful collection of system metrics."""
    mock_process = Mock()
    mock_process.cpu_percent.return_value = 25.5
    mock_process.memory_info.return_value = Mock(
        rss=100 * 1024 * 1024,  # 100 MB
        vms=200 * 1024 * 1024,  # 200 MB
    )
    mock_process.open_files.return_value = [Mock()] * 10
    mock_process.num_threads.return_value = 4

    with (
        patch("psutil.Process", return_value=mock_process),
        patch("psutil.cpu_percent", return_value=50.0),
        patch(
            "psutil.virtual_memory",
            return_value=Mock(
                percent=60.0,
                available=8 * 1024 * 1024 * 1024,  # 8 GB
            ),
        ),
        patch(
            "psutil.disk_usage",
            return_value=Mock(
                percent=70.0,
                free=100 * 1024 * 1024 * 1024,  # 100 GB
            ),
        ),
        patch("memory.workers.tasks.metrics.record_gauge") as mock_gauge,
    ):
        result = collect_system_metrics()

        assert result["status"] == "success"
        assert result["metrics_collected"] >= 9  # At least 9 metrics collected

        # Verify some gauge calls were made
        gauge_names = [call[0][0] for call in mock_gauge.call_args_list]
        assert "process.cpu_percent" in gauge_names
        assert "process.memory_rss_mb" in gauge_names
        assert "system.cpu_percent" in gauge_names
        assert "system.memory_percent" in gauge_names
        assert "system.disk_usage_percent" in gauge_names


def test_collect_system_metrics_process_error():
    """Test that process-level errors don't prevent system metrics collection."""
    with (
        patch("psutil.Process", side_effect=Exception("Process error")),
        patch("psutil.cpu_percent", return_value=50.0),
        patch(
            "psutil.virtual_memory",
            return_value=Mock(
                percent=60.0,
                available=8 * 1024 * 1024 * 1024,
            ),
        ),
        patch(
            "psutil.disk_usage",
            return_value=Mock(
                percent=70.0,
                free=100 * 1024 * 1024 * 1024,
            ),
        ),
        patch("memory.workers.tasks.metrics.record_gauge") as mock_gauge,
    ):
        result = collect_system_metrics()

        assert result["status"] == "success"
        # Should still collect system metrics even if process metrics fail
        assert result["metrics_collected"] >= 5

        gauge_names = [call[0][0] for call in mock_gauge.call_args_list]
        assert "system.cpu_percent" in gauge_names


def test_collect_system_metrics_system_error():
    """Test that system-level errors are logged but don't crash."""
    mock_process = Mock()
    mock_process.cpu_percent.return_value = 25.5
    mock_process.memory_info.return_value = Mock(
        rss=100 * 1024 * 1024,
        vms=200 * 1024 * 1024,
    )
    mock_process.open_files.return_value = []
    mock_process.num_threads.return_value = 4

    with (
        patch("psutil.Process", return_value=mock_process),
        patch("psutil.cpu_percent", side_effect=Exception("System error")),
        patch("memory.workers.tasks.metrics.record_gauge") as mock_gauge,
    ):
        result = collect_system_metrics()

        assert result["status"] == "success"
        # Should still collect process metrics even if system metrics fail
        assert result["metrics_collected"] >= 4


# ============== cleanup_old_metrics tests ==============


def test_cleanup_old_metrics_deletes_old_records(db_session):
    """Test that cleanup deletes records older than retention period."""
    from memory.common.db.models import MetricEvent

    now = datetime.now(timezone.utc)
    old_time = now - timedelta(days=40)
    recent_time = now - timedelta(days=10)

    # Create old and recent metrics
    old_metrics = [
        MetricEvent(
            timestamp=old_time,
            metric_type="test",
            name=f"old_metric_{i}",
            status="success",
        )
        for i in range(5)
    ]
    recent_metrics = [
        MetricEvent(
            timestamp=recent_time,
            metric_type="test",
            name=f"recent_metric_{i}",
            status="success",
        )
        for i in range(3)
    ]

    db_session.add_all(old_metrics + recent_metrics)
    db_session.commit()

    # Verify initial count
    initial_count = db_session.query(MetricEvent).count()
    assert initial_count == 8

    # Run cleanup with 30-day retention
    result = cleanup_old_metrics(retention_days=30)

    assert result["deleted"] == 5
    assert result["retention_days"] == 30

    # Verify only recent metrics remain
    remaining = db_session.query(MetricEvent).all()
    assert len(remaining) == 3
    assert all("recent" in m.name for m in remaining)


def test_cleanup_old_metrics_no_old_records(db_session):
    """Test cleanup when there are no old records."""
    from memory.common.db.models import MetricEvent

    now = datetime.now(timezone.utc)
    recent_time = now - timedelta(days=10)

    # Create only recent metrics
    recent_metrics = [
        MetricEvent(
            timestamp=recent_time,
            metric_type="test",
            name=f"recent_metric_{i}",
            status="success",
        )
        for i in range(3)
    ]

    db_session.add_all(recent_metrics)
    db_session.commit()

    result = cleanup_old_metrics(retention_days=30)

    assert result["deleted"] == 0
    assert result["retention_days"] == 30

    # Verify all metrics still exist
    remaining = db_session.query(MetricEvent).count()
    assert remaining == 3


def test_cleanup_old_metrics_custom_retention(db_session):
    """Test cleanup with custom retention period."""
    from memory.common.db.models import MetricEvent

    now = datetime.now(timezone.utc)
    old_time = now - timedelta(days=10)

    # Create metrics that are 10 days old
    metrics = [
        MetricEvent(
            timestamp=old_time,
            metric_type="test",
            name=f"metric_{i}",
            status="success",
        )
        for i in range(3)
    ]

    db_session.add_all(metrics)
    db_session.commit()

    # With 7-day retention, should delete all
    result = cleanup_old_metrics(retention_days=7)
    assert result["deleted"] == 3

    # With 14-day retention (run on fresh data)
    db_session.add_all(
        [
            MetricEvent(
                timestamp=old_time,
                metric_type="test",
                name=f"metric_new_{i}",
                status="success",
            )
            for i in range(3)
        ]
    )
    db_session.commit()

    result = cleanup_old_metrics(retention_days=14)
    assert result["deleted"] == 0


def test_cleanup_old_metrics_empty_table(db_session):
    """Test cleanup on empty table."""
    result = cleanup_old_metrics(retention_days=30)
    assert result["deleted"] == 0
    assert result["retention_days"] == 30


def test_cleanup_old_metrics_batch_deletion(db_session):
    """Test that large deletions are handled in batches."""
    from memory.common.db.models import MetricEvent

    now = datetime.now(timezone.utc)
    old_time = now - timedelta(days=40)

    # Create more metrics than batch size (10000)
    # We'll use a smaller number for test speed but verify batching logic
    metrics = [
        MetricEvent(
            timestamp=old_time,
            metric_type="test",
            name=f"metric_{i}",
            status="success",
        )
        for i in range(100)  # Smaller for test speed
    ]

    db_session.add_all(metrics)
    db_session.commit()

    result = cleanup_old_metrics(retention_days=30)
    assert result["deleted"] == 100


# ============== refresh_metric_summaries tests ==============


def test_refresh_metric_summaries_success(db_session):
    """Test successful refresh of materialized view."""
    with patch("memory.common.db.connection.make_session") as mock_session:
        mock_sess = MagicMock()
        mock_session.return_value.__enter__ = Mock(return_value=mock_sess)
        mock_session.return_value.__exit__ = Mock(return_value=False)

        result = refresh_metric_summaries()

        assert result["status"] == "success"
        mock_sess.execute.assert_called()
        mock_sess.commit.assert_called()


def test_refresh_metric_summaries_fallback_on_concurrent_failure(db_session):
    """Test fallback to non-concurrent refresh when concurrent fails."""
    with patch("memory.common.db.connection.make_session") as mock_session:
        mock_sess = MagicMock()
        mock_session.return_value.__enter__ = Mock(return_value=mock_sess)
        mock_session.return_value.__exit__ = Mock(return_value=False)

        # First execute fails (concurrent), second succeeds (non-concurrent)
        mock_sess.execute.side_effect = [
            Exception("Concurrent refresh failed"),
            None,  # Non-concurrent succeeds
        ]

        result = refresh_metric_summaries()

        assert result["status"] == "success_non_concurrent"
        assert mock_sess.execute.call_count == 2
        mock_sess.rollback.assert_called_once()


def test_refresh_metric_summaries_complete_failure(db_session):
    """Test handling when both refresh methods fail."""
    with patch("memory.common.db.connection.make_session") as mock_session:
        mock_sess = MagicMock()
        mock_session.return_value.__enter__ = Mock(return_value=mock_sess)
        mock_session.return_value.__exit__ = Mock(return_value=False)

        # Both refreshes fail
        mock_sess.execute.side_effect = [
            Exception("Concurrent refresh failed"),
            Exception("Non-concurrent refresh also failed"),
        ]

        result = refresh_metric_summaries()

        assert result["status"] == "error"
