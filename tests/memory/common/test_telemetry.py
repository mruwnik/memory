"""Tests for telemetry parsing and processing."""

import json
from datetime import datetime, timezone

import pytest

from memory.common.telemetry import (
    ParsedTelemetryEvent,
    extract_otlp_attributes,
    hash_prompt,
    normalize_metric_name,
    parse_otlp_json,
)


# =============================================================================
# extract_otlp_attributes tests
# =============================================================================


@pytest.mark.parametrize(
    "obj,expected",
    [
        ({}, {}),
        ({"attributes": []}, {}),
        (
            {"attributes": [{"key": "model", "value": {"stringValue": "claude-opus-4"}}]},
            {"model": "claude-opus-4"},
        ),
        (
            {"attributes": [{"key": "count", "value": {"intValue": "42"}}]},
            {"count": "42"},
        ),
        (
            {"attributes": [{"key": "cost", "value": {"doubleValue": 0.05}}]},
            {"cost": 0.05},
        ),
        (
            {"attributes": [{"key": "cached", "value": {"boolValue": True}}]},
            {"cached": True},
        ),
        (
            {
                "attributes": [
                    {"key": "model", "value": {"stringValue": "claude-opus-4"}},
                    {"key": "tokens", "value": {"intValue": "1000"}},
                    {"key": "cost", "value": {"doubleValue": 0.05}},
                ]
            },
            {"model": "claude-opus-4", "tokens": "1000", "cost": 0.05},
        ),
    ],
    ids=[
        "empty_object",
        "empty_attributes",
        "string_value",
        "int_value",
        "double_value",
        "bool_value",
        "multiple_attributes",
    ],
)
def test_extract_otlp_attributes(obj, expected):
    assert extract_otlp_attributes(obj) == expected


def test_extract_otlp_attributes_array_value():
    obj = {
        "attributes": [
            {
                "key": "tags",
                "value": {
                    "arrayValue": {
                        "values": [
                            {"stringValue": "tag1"},
                            {"stringValue": "tag2"},
                        ]
                    }
                },
            }
        ]
    }
    assert extract_otlp_attributes(obj) == {"tags": ["tag1", "tag2"]}


# =============================================================================
# normalize_metric_name tests
# =============================================================================


@pytest.mark.parametrize(
    "name,expected",
    [
        ("claude_code.token.usage", "token.usage"),
        ("claude_code.session.count", "session.count"),
        ("token.usage", "token.usage"),
        ("custom.metric", "custom.metric"),
    ],
    ids=[
        "strips_prefix_token",
        "strips_prefix_session",
        "preserves_without_prefix",
        "preserves_custom",
    ],
)
def test_normalize_metric_name(name, expected):
    assert normalize_metric_name(name) == expected


# =============================================================================
# parse_otlp_json tests
# =============================================================================


@pytest.mark.parametrize(
    "data",
    [b"", b"{}"],
    ids=["empty_bytes", "empty_json"],
)
def test_parse_otlp_json_empty(data):
    assert parse_otlp_json(data) == []


def test_parse_otlp_json_invalid():
    assert parse_otlp_json(b"not json") == []


def test_parse_otlp_json_metric_sum():
    payload = {
        "resourceMetrics": [
            {
                "resource": {"attributes": []},
                "scopeMetrics": [
                    {
                        "metrics": [
                            {
                                "name": "token.usage",
                                "sum": {
                                    "dataPoints": [
                                        {
                                            "asDouble": 1500.0,
                                            "attributes": [
                                                {
                                                    "key": "model",
                                                    "value": {"stringValue": "claude-opus-4"},
                                                },
                                                {
                                                    "key": "token_type",
                                                    "value": {"stringValue": "input"},
                                                },
                                            ],
                                        }
                                    ]
                                },
                            }
                        ]
                    }
                ],
            }
        ]
    }

    events = parse_otlp_json(json.dumps(payload).encode())

    assert len(events) == 1
    event = events[0]
    assert event.event_type == "metric"
    assert event.name == "token.usage"
    assert event.value == 1500.0
    assert event.source == "claude-opus-4"
    assert event.attributes["token_type"] == "input"


def test_parse_otlp_json_metric_gauge():
    payload = {
        "resourceMetrics": [
            {
                "scopeMetrics": [
                    {
                        "metrics": [
                            {
                                "name": "session.count",
                                "gauge": {
                                    "dataPoints": [{"asDouble": 5.0, "attributes": []}]
                                },
                            }
                        ]
                    }
                ]
            }
        ]
    }

    events = parse_otlp_json(json.dumps(payload).encode())

    assert len(events) == 1
    assert events[0].name == "session.count"
    assert events[0].value == 5.0


def test_parse_otlp_json_log_event():
    payload = {
        "resourceLogs": [
            {
                "scopeLogs": [
                    {
                        "logRecords": [
                            {
                                "body": {"stringValue": "tool executed"},
                                "attributes": [
                                    {
                                        "key": "event.name",
                                        "value": {"stringValue": "tool_result"},
                                    },
                                    {
                                        "key": "session_id",
                                        "value": {"stringValue": "sess-123"},
                                    },
                                ],
                            }
                        ]
                    }
                ]
            }
        ]
    }

    events = parse_otlp_json(json.dumps(payload).encode())

    assert len(events) == 1
    event = events[0]
    assert event.event_type == "log"
    assert event.name == "tool_result"
    assert event.body == "tool executed"
    assert event.attributes.get("session_id") == "sess-123"


def test_parse_otlp_json_user_prompt_hashes_body():
    """User prompts should have their bodies hashed for privacy."""
    payload = {
        "resourceLogs": [
            {
                "scopeLogs": [
                    {
                        "logRecords": [
                            {
                                "body": {"stringValue": "my secret prompt"},
                                "attributes": [
                                    {
                                        "key": "event.name",
                                        "value": {"stringValue": "user_prompt"},
                                    },
                                ],
                            }
                        ]
                    }
                ]
            }
        ]
    }

    events = parse_otlp_json(json.dumps(payload).encode())

    assert len(events) == 1
    event = events[0]
    assert event.name == "user_prompt"
    assert event.body != "my secret prompt"
    assert event.body == hash_prompt("my secret prompt")
    assert len(event.body) == 64


def test_parse_otlp_json_tool_result_extracts_tool_name():
    payload = {
        "resourceLogs": [
            {
                "scopeLogs": [
                    {
                        "logRecords": [
                            {
                                "body": {"stringValue": "success"},
                                "attributes": [
                                    {
                                        "key": "event.name",
                                        "value": {"stringValue": "tool_result"},
                                    },
                                    {
                                        "key": "tool_name",
                                        "value": {"stringValue": "Read"},
                                    },
                                ],
                            }
                        ]
                    }
                ]
            }
        ]
    }

    events = parse_otlp_json(json.dumps(payload).encode())

    assert len(events) == 1
    assert events[0].name == "tool_result"
    assert events[0].tool_name == "Read"


def test_parse_otlp_json_strips_claude_code_prefix():
    payload = {
        "resourceMetrics": [
            {
                "scopeMetrics": [
                    {
                        "metrics": [
                            {
                                "name": "claude_code.token.usage",
                                "gauge": {
                                    "dataPoints": [{"asDouble": 100.0, "attributes": []}]
                                },
                            }
                        ]
                    }
                ]
            }
        ]
    }

    events = parse_otlp_json(json.dumps(payload).encode())

    assert len(events) == 1
    assert events[0].name == "token.usage"


# =============================================================================
# hash_prompt tests
# =============================================================================


def test_hash_prompt_returns_64_char_hex():
    result = hash_prompt("test prompt")
    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)


def test_hash_prompt_deterministic():
    assert hash_prompt("test") == hash_prompt("test")


def test_hash_prompt_different_inputs():
    assert hash_prompt("test1") != hash_prompt("test2")


def test_hash_prompt_empty_string():
    result = hash_prompt("")
    assert len(result) == 64


# =============================================================================
# ParsedTelemetryEvent tests
# =============================================================================


def test_parsed_telemetry_event_defaults():
    event = ParsedTelemetryEvent(
        timestamp=datetime.now(timezone.utc),
        event_type="metric",
        name="test",
    )
    assert event.value is None
    assert event.session_id is None
    assert event.source is None
    assert event.tool_name is None
    assert event.attributes == {}
    assert event.body is None


def test_parsed_telemetry_event_all_fields():
    ts = datetime.now(timezone.utc)
    event = ParsedTelemetryEvent(
        timestamp=ts,
        event_type="log",
        name="tool_result",
        value=100.0,
        session_id="sess-123",
        source="claude-opus-4",
        tool_name="Read",
        attributes={"key": "value"},
        body="result body",
    )
    assert event.timestamp == ts
    assert event.event_type == "log"
    assert event.name == "tool_result"
    assert event.value == 100.0
    assert event.session_id == "sess-123"
    assert event.source == "claude-opus-4"
    assert event.tool_name == "Read"
    assert event.attributes == {"key": "value"}
    assert event.body == "result body"
