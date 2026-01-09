"""Tests for the metrics API endpoints."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from memory.api.metrics import router
from memory.common.db.models import MetricEvent


@pytest.fixture
def client():
    """Create a test client for the metrics router."""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def sample_metrics(db_session):
    """Create sample metrics for testing."""
    now = datetime.now(timezone.utc)

    metrics = [
        # Task metrics
        MetricEvent(
            timestamp=now - timedelta(hours=1),
            metric_type="task",
            name="sync_account",
            duration_ms=150.5,
            status="success",
            labels={"account_id": 1},
        ),
        MetricEvent(
            timestamp=now - timedelta(hours=2),
            metric_type="task",
            name="sync_account",
            duration_ms=200.0,
            status="success",
            labels={"account_id": 2},
        ),
        MetricEvent(
            timestamp=now - timedelta(hours=3),
            metric_type="task",
            name="sync_account",
            duration_ms=500.0,
            status="failure",
            labels={"account_id": 3},
        ),
        MetricEvent(
            timestamp=now - timedelta(hours=1),
            metric_type="task",
            name="process_email",
            duration_ms=50.0,
            status="success",
            labels={},
        ),
        # MCP call metrics
        MetricEvent(
            timestamp=now - timedelta(hours=1),
            metric_type="mcp_call",
            name="search_knowledge_base",
            duration_ms=300.0,
            status="success",
            labels={"user_id": 1},
        ),
        MetricEvent(
            timestamp=now - timedelta(hours=2),
            metric_type="mcp_call",
            name="search_knowledge_base",
            duration_ms=450.0,
            status="success",
            labels={"user_id": 1},
        ),
        MetricEvent(
            timestamp=now - timedelta(hours=1),
            metric_type="mcp_call",
            name="observe",
            duration_ms=100.0,
            status="success",
            labels={},
        ),
        # System metrics
        MetricEvent(
            timestamp=now - timedelta(minutes=30),
            metric_type="system",
            name="process.cpu_percent",
            value=25.5,
            labels={},
        ),
        MetricEvent(
            timestamp=now - timedelta(minutes=60),
            metric_type="system",
            name="process.cpu_percent",
            value=30.0,
            labels={},
        ),
        MetricEvent(
            timestamp=now - timedelta(minutes=30),
            metric_type="system",
            name="system.memory_percent",
            value=60.0,
            labels={},
        ),
    ]

    db_session.add_all(metrics)
    db_session.commit()
    return metrics


# ============== GET /api/metrics/summary tests ==============


def test_get_metrics_summary_all(client, sample_metrics, db_session):
    """Test getting summary of all metrics."""
    with patch("memory.api.metrics.make_session", return_value=db_session):
        response = client.get("/api/metrics/summary")

    assert response.status_code == 200
    data = response.json()

    assert "period_hours" in data
    assert "since" in data
    assert "metrics" in data
    assert len(data["metrics"]) > 0


def test_get_metrics_summary_by_type(client, sample_metrics, db_session):
    """Test filtering summary by metric type."""
    with patch("memory.api.metrics.make_session", return_value=db_session):
        response = client.get("/api/metrics/summary?metric_type=task")

    assert response.status_code == 200
    data = response.json()

    # All metrics should be tasks
    for metric in data["metrics"]:
        assert metric["metric_type"] == "task"


def test_get_metrics_summary_by_name(client, sample_metrics, db_session):
    """Test filtering summary by metric name."""
    with patch("memory.api.metrics.make_session", return_value=db_session):
        response = client.get("/api/metrics/summary?name=sync_account")

    assert response.status_code == 200
    data = response.json()

    # All metrics should be sync_account
    for metric in data["metrics"]:
        assert metric["name"] == "sync_account"


def test_get_metrics_summary_custom_hours(client, sample_metrics, db_session):
    """Test summary with custom time range."""
    with patch("memory.api.metrics.make_session", return_value=db_session):
        response = client.get("/api/metrics/summary?hours=1")

    assert response.status_code == 200
    data = response.json()
    assert data["period_hours"] == 1


def test_get_metrics_summary_aggregates_correctly(client, sample_metrics, db_session):
    """Test that summary aggregates counts correctly."""
    with patch("memory.api.metrics.make_session", return_value=db_session):
        response = client.get("/api/metrics/summary?metric_type=task&name=sync_account")

    assert response.status_code == 200
    data = response.json()

    # Should have one entry for sync_account
    assert len(data["metrics"]) == 1
    metric = data["metrics"][0]

    assert metric["count"] == 3  # 3 sync_account events
    assert metric["success_count"] == 2
    assert metric["failure_count"] == 1


# ============== GET /api/metrics/tasks tests ==============


def test_get_task_metrics(client, sample_metrics, db_session):
    """Test getting task metrics."""
    with patch("memory.api.metrics.make_session", return_value=db_session):
        response = client.get("/api/metrics/tasks")

    assert response.status_code == 200
    data = response.json()

    assert "period_hours" in data
    assert "count" in data
    assert "events" in data

    # All events should be tasks
    for event in data["events"]:
        assert "name" in event
        assert "duration_ms" in event
        assert "status" in event


def test_get_task_metrics_by_name(client, sample_metrics, db_session):
    """Test filtering task metrics by name."""
    with patch("memory.api.metrics.make_session", return_value=db_session):
        response = client.get("/api/metrics/tasks?name=sync_account")

    assert response.status_code == 200
    data = response.json()

    for event in data["events"]:
        assert event["name"] == "sync_account"


def test_get_task_metrics_limit(client, sample_metrics, db_session):
    """Test limiting task metrics."""
    with patch("memory.api.metrics.make_session", return_value=db_session):
        response = client.get("/api/metrics/tasks?limit=2")

    assert response.status_code == 200
    data = response.json()
    assert len(data["events"]) <= 2


# ============== GET /api/metrics/mcp tests ==============


def test_get_mcp_metrics(client, sample_metrics, db_session):
    """Test getting MCP call metrics."""
    with patch("memory.api.metrics.make_session", return_value=db_session):
        response = client.get("/api/metrics/mcp")

    assert response.status_code == 200
    data = response.json()

    assert "period_hours" in data
    assert "count" in data
    assert "events" in data


def test_get_mcp_metrics_by_name(client, sample_metrics, db_session):
    """Test filtering MCP metrics by tool name."""
    with patch("memory.api.metrics.make_session", return_value=db_session):
        response = client.get("/api/metrics/mcp?name=search_knowledge_base")

    assert response.status_code == 200
    data = response.json()

    for event in data["events"]:
        assert event["name"] == "search_knowledge_base"


# ============== GET /api/metrics/system tests ==============


def test_get_system_metrics(client, sample_metrics, db_session):
    """Test getting system metrics."""
    with patch("memory.api.metrics.make_session", return_value=db_session):
        response = client.get("/api/metrics/system")

    assert response.status_code == 200
    data = response.json()

    assert "period_hours" in data
    assert "latest" in data
    assert "history" in data

    # Latest should have most recent values
    assert "process.cpu_percent" in data["latest"]
    assert data["latest"]["process.cpu_percent"] == 25.5  # Most recent


def test_get_system_metrics_by_name(client, sample_metrics, db_session):
    """Test filtering system metrics by name."""
    with patch("memory.api.metrics.make_session", return_value=db_session):
        response = client.get("/api/metrics/system?name=process.cpu_percent")

    assert response.status_code == 200
    data = response.json()

    for event in data["history"]:
        assert event["name"] == "process.cpu_percent"


# ============== GET /api/metrics/raw tests ==============


def test_get_raw_metrics(client, sample_metrics, db_session):
    """Test getting raw metric events."""
    with patch("memory.api.metrics.make_session", return_value=db_session):
        response = client.get("/api/metrics/raw")

    assert response.status_code == 200
    data = response.json()

    assert "total" in data
    assert "offset" in data
    assert "limit" in data
    assert "events" in data


def test_get_raw_metrics_filter_by_type(client, sample_metrics, db_session):
    """Test filtering raw metrics by type."""
    with patch("memory.api.metrics.make_session", return_value=db_session):
        response = client.get("/api/metrics/raw?metric_type=mcp_call")

    assert response.status_code == 200
    data = response.json()

    for event in data["events"]:
        assert event["metric_type"] == "mcp_call"


def test_get_raw_metrics_pagination(client, sample_metrics, db_session):
    """Test pagination of raw metrics."""
    with patch("memory.api.metrics.make_session", return_value=db_session):
        # Get first page
        response1 = client.get("/api/metrics/raw?limit=3&offset=0")
        data1 = response1.json()

        # Get second page
        response2 = client.get("/api/metrics/raw?limit=3&offset=3")
        data2 = response2.json()

    assert response1.status_code == 200
    assert response2.status_code == 200

    # Pages should have different events
    ids1 = {e["id"] for e in data1["events"]}
    ids2 = {e["id"] for e in data2["events"]}
    assert ids1.isdisjoint(ids2)


def test_get_raw_metrics_includes_all_fields(client, sample_metrics, db_session):
    """Test that raw metrics include all fields."""
    with patch("memory.api.metrics.make_session", return_value=db_session):
        response = client.get("/api/metrics/raw?limit=1")

    assert response.status_code == 200
    data = response.json()

    event = data["events"][0]
    assert "id" in event
    assert "timestamp" in event
    assert "metric_type" in event
    assert "name" in event
    assert "duration_ms" in event
    assert "status" in event
    assert "labels" in event
    assert "value" in event


# ============== Edge cases ==============


def test_empty_metrics(client, db_session):
    """Test endpoints with no metrics."""
    with patch("memory.api.metrics.make_session", return_value=db_session):
        summary_resp = client.get("/api/metrics/summary")
        tasks_resp = client.get("/api/metrics/tasks")
        mcp_resp = client.get("/api/metrics/mcp")
        system_resp = client.get("/api/metrics/system")
        raw_resp = client.get("/api/metrics/raw")

    assert summary_resp.status_code == 200
    assert summary_resp.json()["metrics"] == []

    assert tasks_resp.status_code == 200
    assert tasks_resp.json()["events"] == []

    assert mcp_resp.status_code == 200
    assert mcp_resp.json()["events"] == []

    assert system_resp.status_code == 200
    assert system_resp.json()["latest"] == {}

    assert raw_resp.status_code == 200
    assert raw_resp.json()["events"] == []


def test_hours_validation(client, db_session):
    """Test that hours parameter is validated."""
    with patch("memory.api.metrics.make_session", return_value=db_session):
        # Too low
        response = client.get("/api/metrics/summary?hours=0")
        assert response.status_code == 422

        # Too high
        response = client.get("/api/metrics/summary?hours=1000")
        assert response.status_code == 422
