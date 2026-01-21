"""
OpenTelemetry parsing and telemetry data handling.

Parses OTLP JSON format (metrics and logs) from telemetry exports.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from memory.common.db.connection import make_session
from memory.common.db.models import TelemetryEvent

logger = logging.getLogger(__name__)


@dataclass
class ParsedTelemetryEvent:
    """A parsed telemetry event from OTLP data."""

    timestamp: datetime
    event_type: str  # 'metric' or 'log'
    name: str
    value: float | None = None
    session_id: str | None = None
    source: str | None = None
    tool_name: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    body: str | None = None


def parse_otlp_timestamp(nano: int | str | None) -> datetime:
    """Convert nanosecond Unix timestamp to datetime."""
    if nano is None:
        return datetime.now(timezone.utc)
    if isinstance(nano, str):
        nano = int(nano)
    return datetime.fromtimestamp(nano / 1_000_000_000, tz=timezone.utc)


def extract_otlp_attributes(obj: dict) -> dict[str, Any]:
    """Extract OTLP-style attributes array into dict."""
    result = {}
    for attr in obj.get("attributes", []):
        key = attr.get("key", "")
        value = attr.get("value", {})
        # OTLP values are typed: stringValue, intValue, doubleValue, boolValue, etc.
        for v_type in ["stringValue", "intValue", "doubleValue", "boolValue"]:
            if v_type in value:
                result[key] = value[v_type]
                break
        # Handle arrayValue
        if "arrayValue" in value:
            result[key] = [
                v.get("stringValue") or v.get("intValue") or v.get("doubleValue")
                for v in value["arrayValue"].get("values", [])
            ]
    return result


def normalize_metric_name(name: str) -> str:
    """Normalize metric names to shorter form.

    Strips 'claude_code.' prefix if present.
    """
    if name.startswith("claude_code."):
        return name[len("claude_code.") :]
    return name


def hash_prompt(prompt: str) -> str:
    """Create a SHA-256 hash of a prompt for privacy-preserving storage."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def parse_metric_datapoint(
    dp: dict,
    name: str,
    resource_attrs: dict,
    session_id: str | None,
) -> ParsedTelemetryEvent:
    """Parse a single metric datapoint into a telemetry event."""
    dp_attrs = extract_otlp_attributes(dp)
    all_attrs = {**resource_attrs, **dp_attrs}

    # Check datapoint attributes for session_id if not in resource attributes
    effective_session_id = session_id
    if not effective_session_id:
        effective_session_id = dp_attrs.get("session.id") or dp_attrs.get("session_id")

    return ParsedTelemetryEvent(
        timestamp=parse_otlp_timestamp(dp.get("timeUnixNano")),
        event_type="metric",
        name=name,
        value=dp.get("asDouble") or dp.get("asInt"),
        session_id=effective_session_id,
        source=all_attrs.get("model"),
        tool_name=all_attrs.get("tool_name") or all_attrs.get("tool"),
        attributes=all_attrs,
        body=None,
    )


def parse_log_record(
    log_record: dict,
    resource_attrs: dict,
    session_id: str | None,
) -> ParsedTelemetryEvent:
    """Parse a single log record into a telemetry event."""
    log_attrs = extract_otlp_attributes(log_record)
    all_attrs = {**resource_attrs, **log_attrs}

    # Event name comes from attributes
    event_name = (
        all_attrs.get("event.name")
        or all_attrs.get("event_name")
        or "unknown"
    )
    event_name = normalize_metric_name(event_name)

    # Body can be string or structured
    body_obj = log_record.get("body", {})
    body = body_obj.get("stringValue") if isinstance(body_obj, dict) else None

    # For user_prompt events, hash the body for privacy
    if event_name == "user_prompt" and body:
        body = hash_prompt(body)

    return ParsedTelemetryEvent(
        timestamp=parse_otlp_timestamp(log_record.get("timeUnixNano")),
        event_type="log",
        name=event_name,
        value=all_attrs.get("duration_ms") or all_attrs.get("cost"),
        session_id=session_id or all_attrs.get("session.id") or all_attrs.get("session_id"),
        source=all_attrs.get("model"),
        tool_name=all_attrs.get("tool_name") or all_attrs.get("tool"),
        attributes=all_attrs,
        body=body,
    )


def parse_resource_metrics(resource_metric: dict) -> list[ParsedTelemetryEvent]:
    """Parse metrics from a single resource block."""
    events: list[ParsedTelemetryEvent] = []
    resource_attrs = extract_otlp_attributes(resource_metric.get("resource", {}))
    session_id = resource_attrs.get("session.id") or resource_attrs.get("session_id")

    for scope_metric in resource_metric.get("scopeMetrics", []):
        for metric in scope_metric.get("metrics", []):
            raw_name = metric.get("name", "unknown")
            name = normalize_metric_name(raw_name)

            # Sum metrics (counters)
            if "sum" in metric:
                for dp in metric["sum"].get("dataPoints", []):
                    events.append(parse_metric_datapoint(dp, name, resource_attrs, session_id))

            # Gauge metrics
            if "gauge" in metric:
                for dp in metric["gauge"].get("dataPoints", []):
                    events.append(parse_metric_datapoint(dp, name, resource_attrs, session_id))

    return events


def parse_resource_logs(resource_log: dict) -> list[ParsedTelemetryEvent]:
    """Parse logs from a single resource block."""
    events: list[ParsedTelemetryEvent] = []
    resource_attrs = extract_otlp_attributes(resource_log.get("resource", {}))
    session_id = resource_attrs.get("session.id") or resource_attrs.get("session_id")

    for scope_log in resource_log.get("scopeLogs", []):
        for log_record in scope_log.get("logRecords", []):
            events.append(parse_log_record(log_record, resource_attrs, session_id))

    return events


def parse_otlp_json(data: bytes | str) -> list[ParsedTelemetryEvent]:
    """
    Parse OTLP JSON format (both metrics and logs).

    OTLP JSON structure for metrics:
    {
        "resourceMetrics": [{
            "resource": {"attributes": [...]},
            "scopeMetrics": [{
                "metrics": [{
                    "name": "token.usage",
                    "sum": {"dataPoints": [{"asInt": 1000, "attributes": [...]}]}
                }]
            }]
        }]
    }

    OTLP JSON structure for logs:
    {
        "resourceLogs": [{
            "resource": {"attributes": [...]},
            "scopeLogs": [{
                "logRecords": [{
                    "timeUnixNano": "...",
                    "body": {"stringValue": "..."},
                    "attributes": [...]
                }]
            }]
        }]
    }
    """
    if isinstance(data, bytes):
        data = data.decode("utf-8")

    try:
        payload = json.loads(data)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse OTLP JSON: {e}")
        return []

    events: list[ParsedTelemetryEvent] = []

    # Handle metrics
    for resource_metric in payload.get("resourceMetrics", []):
        events.extend(parse_resource_metrics(resource_metric))

    # Handle logs/events
    for resource_log in payload.get("resourceLogs", []):
        events.extend(parse_resource_logs(resource_log))

    return events


def write_events_to_db(events: list[ParsedTelemetryEvent], user_id: int) -> int:
    """Write parsed events to the database.

    Args:
        events: List of parsed telemetry events
        user_id: ID of the user who reported these events

    Returns:
        Number of events written
    """
    if not events:
        return 0

    with make_session() as session:
        for event in events:
            db_event = TelemetryEvent(
                timestamp=event.timestamp,
                user_id=user_id,
                event_type=event.event_type,
                name=event.name,
                value=event.value,
                session_id=event.session_id,
                source=event.source,
                tool_name=event.tool_name,
                attributes=event.attributes,
                body=event.body,
            )
            session.add(db_event)
        session.commit()

    return len(events)
