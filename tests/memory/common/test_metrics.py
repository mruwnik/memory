"""Tests for the metrics module - @profile decorator and metric recording."""

import asyncio
import queue
import threading
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest

from memory.common.metrics import (
    extract_params,
    serialize_labels,
    truncate_value,
    write_metric,
    _metric_queue,
    profile,
    record_gauge,
    record_metric,
    start_metrics_writer,
    stop_metrics_writer,
    MAX_PARAM_VALUE_LENGTH,
)


# ============== truncate_value tests ==============


@pytest.mark.parametrize(
    "value,max_length,expected",
    [
        (None, 500, None),
        ("short", 500, "short"),
        ("a" * 600, 500, "a" * 500 + "..."),
        (123, 500, 123),
        (12.5, 500, 12.5),
        (["list", "value"], 500, ["list", "value"]),
    ],
)
def test_truncate_value(value, max_length, expected):
    """Test value truncation for various types."""
    result = truncate_value(value, max_length)
    assert result == expected


def test_truncate_value_unserializable():
    """Test truncation of unserializable objects."""

    class Unserializable:
        def __str__(self):
            raise ValueError("Cannot convert")

    result = truncate_value(Unserializable())
    assert result == "<unserializable>"


# ============== extract_params tests ==============


def test_extract_params_false():
    """Test that False log_params returns empty dict."""

    def func(a, b, c):
        pass

    result = extract_params(func, (1, 2, 3), {}, False)
    assert result == {}


def test_extract_params_true():
    """Test that True log_params returns all params."""

    def func(a, b, c=10):
        pass

    result = extract_params(func, (1, 2), {}, True)
    assert result == {"a": 1, "b": 2, "c": 10}


def test_extract_params_list():
    """Test that a list of param names filters correctly."""

    def func(a, b, c, d=4):
        pass

    result = extract_params(func, (1, 2, 3), {}, ["a", "c"])
    assert result == {"a": 1, "c": 3}


def test_extract_params_with_kwargs():
    """Test param extraction with keyword arguments."""

    def func(a, b, c=10):
        pass

    result = extract_params(func, (1,), {"b": 2, "c": 20}, True)
    assert result == {"a": 1, "b": 2, "c": 20}


def test_extract_params_truncates_long_values():
    """Test that long values are truncated."""

    def func(data):
        pass

    long_string = "x" * 1000
    result = extract_params(func, (long_string,), {}, True)
    assert len(result["data"]) == MAX_PARAM_VALUE_LENGTH + 3  # +3 for "..."


def test_extract_params_signature_error():
    """Test graceful handling when signature binding fails."""

    def func(a, b):
        pass

    # Too few arguments - should return empty dict instead of raising
    result = extract_params(func, (1,), {}, True)
    assert result == {}


# ============== serialize_labels tests ==============


def test_serialize_labels_simple():
    """Test serialization of simple JSON-compatible values."""
    labels = {"string": "value", "int": 42, "float": 3.14, "bool": True}
    result = serialize_labels(labels)
    assert result == labels


def test_serialize_labels_converts_non_serializable():
    """Test that non-serializable values are converted to strings."""
    obj = object()
    labels = {"obj": obj, "normal": "value"}
    result = serialize_labels(labels)
    assert result["normal"] == "value"
    assert isinstance(result["obj"], str)


# ============== write_metric tests ==============


def test_write_metric_queues_metric():
    """Test that write_metric adds metric to queue."""
    # Clear the queue first
    while not _metric_queue.empty():
        try:
            _metric_queue.get_nowait()
        except queue.Empty:
            break

    write_metric(
        metric_type="test",
        name="test_metric",
        duration_ms=100.5,
        status="success",
        labels={"key": "value"},
        value=42.0,
    )

    metric = _metric_queue.get_nowait()
    assert metric["metric_type"] == "test"
    assert metric["name"] == "test_metric"
    assert metric["duration_ms"] == 100.5
    assert metric["status"] == "success"
    assert metric["labels"] == {"key": "value"}
    assert metric["value"] == 42.0
    assert isinstance(metric["timestamp"], datetime)


def test_write_metric_full_queue():
    """Test that full queue doesn't block (drops metric with warning)."""
    # This is hard to test without mocking, but we can verify no exception is raised
    with patch.object(_metric_queue, "put_nowait", side_effect=queue.Full):
        # Should not raise
        write_metric(metric_type="test", name="test")


# ============== record_metric tests ==============


def test_record_metric_starts_writer():
    """Test that record_metric starts the background writer if not running."""
    with (
        patch("memory.common.metrics._writer_started", False),
        patch("memory.common.metrics.start_metrics_writer") as mock_start,
        patch("memory.common.metrics.write_metric"),
    ):
        record_metric(metric_type="test", name="test_metric")
        mock_start.assert_called_once()


def test_record_metric_calls_write_metric():
    """Test that record_metric forwards to write_metric."""
    with (
        patch("memory.common.metrics._writer_started", True),
        patch("memory.common.metrics.write_metric") as mock_write,
    ):
        record_metric(
            metric_type="task",
            name="my_task",
            duration_ms=50.0,
            status="success",
            labels={"key": "val"},
            value=1.0,
        )

        mock_write.assert_called_once_with(
            "task", "my_task", 50.0, "success", {"key": "val"}, 1.0
        )


# ============== record_gauge tests ==============


def test_record_gauge():
    """Test that record_gauge calls record_metric with system type."""
    with patch("memory.common.metrics.record_metric") as mock_record:
        record_gauge("cpu_percent", 75.5, labels={"host": "server1"})

        mock_record.assert_called_once_with(
            metric_type="system",
            name="cpu_percent",
            value=75.5,
            labels={"host": "server1"},
        )


# ============== @profile decorator tests ==============


def test_profile_sync_function_success():
    """Test profiling a successful sync function."""
    with patch("memory.common.metrics.record_metric") as mock_record:

        @profile("task")
        def my_task(x, y):
            return x + y

        result = my_task(1, 2)

        assert result == 3
        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args[1]
        assert call_kwargs["metric_type"] == "task"
        assert call_kwargs["name"] == "my_task"
        assert call_kwargs["status"] == "success"
        assert call_kwargs["duration_ms"] > 0


def test_profile_sync_function_failure():
    """Test profiling a failing sync function."""
    with patch("memory.common.metrics.record_metric") as mock_record:

        @profile("task")
        def failing_task():
            raise ValueError("oops")

        with pytest.raises(ValueError, match="oops"):
            failing_task()

        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args[1]
        assert call_kwargs["status"] == "failure"


def test_profile_async_function_success():
    """Test profiling a successful async function."""
    with patch("memory.common.metrics.record_metric") as mock_record:

        @profile("mcp_call")
        async def my_async_task(query):
            await asyncio.sleep(0.01)
            return f"result: {query}"

        result = asyncio.run(my_async_task("test"))

        assert result == "result: test"
        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args[1]
        assert call_kwargs["metric_type"] == "mcp_call"
        assert call_kwargs["name"] == "my_async_task"
        assert call_kwargs["status"] == "success"
        assert call_kwargs["duration_ms"] >= 10  # At least 10ms from sleep


def test_profile_async_function_failure():
    """Test profiling a failing async function."""
    with patch("memory.common.metrics.record_metric") as mock_record:

        @profile("mcp_call")
        async def failing_async():
            raise RuntimeError("async error")

        with pytest.raises(RuntimeError, match="async error"):
            asyncio.run(failing_async())

        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args[1]
        assert call_kwargs["status"] == "failure"


def test_profile_with_custom_name():
    """Test profiling with a custom metric name."""
    with patch("memory.common.metrics.record_metric") as mock_record:

        @profile("search", name="custom_search_name")
        def search(query):
            return []

        search("test")

        call_kwargs = mock_record.call_args[1]
        assert call_kwargs["name"] == "custom_search_name"


def test_profile_with_log_params_list():
    """Test profiling with selective param logging."""
    with patch("memory.common.metrics.record_metric") as mock_record:

        @profile("task", log_params=["account_id"])
        def sync_account(account_id, since, limit=100):
            pass

        sync_account(42, "2024-01-01", limit=50)

        call_kwargs = mock_record.call_args[1]
        assert call_kwargs["labels"] == {"account_id": 42}


def test_profile_with_log_params_true():
    """Test profiling with all params logged."""
    with patch("memory.common.metrics.record_metric") as mock_record:

        @profile("search", log_params=True)
        def execute_search(query, filters=None):
            pass

        execute_search("test query", filters={"type": "email"})

        call_kwargs = mock_record.call_args[1]
        assert call_kwargs["labels"] == {
            "query": "test query",
            "filters": {"type": "email"},
        }


def test_profile_with_extra_labels():
    """Test profiling with static extra labels."""
    with patch("memory.common.metrics.record_metric") as mock_record:

        @profile("task", extra_labels={"queue": "email", "version": "2"})
        def email_task():
            pass

        email_task()

        call_kwargs = mock_record.call_args[1]
        assert call_kwargs["labels"]["queue"] == "email"
        assert call_kwargs["labels"]["version"] == "2"


def test_profile_merges_extra_labels_and_params():
    """Test that extra_labels and log_params are merged."""
    with patch("memory.common.metrics.record_metric") as mock_record:

        @profile("task", log_params=["id"], extra_labels={"static": "value"})
        def my_task(id, other):
            pass

        my_task(123, "ignored")

        call_kwargs = mock_record.call_args[1]
        assert call_kwargs["labels"] == {"static": "value", "id": 123}


def test_profile_preserves_function_metadata():
    """Test that the decorator preserves function name and docstring."""

    @profile("task")
    def documented_function():
        """This is a docstring."""
        pass

    assert documented_function.__name__ == "documented_function"
    assert documented_function.__doc__ == "This is a docstring."


# ============== Background writer tests ==============


def test_start_stop_metrics_writer():
    """Test starting and stopping the background writer."""
    # Make sure writer is stopped
    stop_metrics_writer(timeout=1.0)

    # Start it
    start_metrics_writer()

    # Import the module state
    from memory.common import metrics

    assert metrics._writer_started is True
    assert metrics._writer_thread is not None
    assert metrics._writer_thread.is_alive()

    # Stop it
    stop_metrics_writer(timeout=2.0)

    assert metrics._writer_started is False


def test_start_metrics_writer_idempotent():
    """Test that calling start_metrics_writer multiple times is safe."""
    stop_metrics_writer(timeout=1.0)

    start_metrics_writer()
    start_metrics_writer()  # Should be safe
    start_metrics_writer()  # Should be safe

    from memory.common import metrics

    assert metrics._writer_started is True

    stop_metrics_writer(timeout=2.0)
