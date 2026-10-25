"""Tests for sessions API endpoints."""

import json
from unittest.mock import patch

import pytest


# ====== extract_tool_calls_from_message — hermetic ======


def test_extract_tool_calls_empty_for_no_tool_use():
    from memory.api.sessions import extract_tool_calls_from_message

    msg = {
        "content": [{"type": "text", "text": "no tools"}],
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }
    assert extract_tool_calls_from_message(msg) == []


def test_extract_tool_calls_empty_for_missing_usage():
    from memory.api.sessions import extract_tool_calls_from_message

    msg = {"content": [{"type": "tool_use", "name": "Bash"}]}
    assert extract_tool_calls_from_message(msg) == []


def test_extract_tool_calls_single_tool_takes_full_token_count():
    from memory.api.sessions import extract_tool_calls_from_message

    msg = {
        "content": [{"type": "tool_use", "name": "Bash"}],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 25,
            "cache_creation_input_tokens": 10,
        },
    }
    [call] = extract_tool_calls_from_message(msg)
    assert call.tool_name == "Bash"
    assert call.input_tokens == 100
    assert call.output_tokens == 50
    assert call.cache_read_tokens == 25
    assert call.cache_creation_tokens == 10
    assert call.total_tokens == 185


def test_extract_tool_calls_distributes_remainder_to_first_tools():
    """100 tokens / 3 tools should sum exactly to 100, not 99 (rounding)."""
    from memory.api.sessions import extract_tool_calls_from_message

    msg = {
        "content": [
            {"type": "tool_use", "name": "Read"},
            {"type": "tool_use", "name": "Bash"},
            {"type": "tool_use", "name": "WebSearch"},
        ],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 7,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
    }
    calls = extract_tool_calls_from_message(msg)
    # 100 / 3 = 34, 33, 33  (remainder 1 → first gets one extra)
    assert [c.input_tokens for c in calls] == [34, 33, 33]
    # 7 / 3 = 3, 2, 2 (remainder 1)
    assert [c.output_tokens for c in calls] == [3, 2, 2]
    # Sum exactly preserved
    assert sum(c.input_tokens for c in calls) == 100
    assert sum(c.output_tokens for c in calls) == 7


def test_extract_tool_calls_handles_missing_name():
    from memory.api.sessions import extract_tool_calls_from_message

    msg = {
        "content": [{"type": "tool_use"}],
        "usage": {"input_tokens": 10},
    }
    [call] = extract_tool_calls_from_message(msg)
    assert call.tool_name == "unknown"


def test_extract_tool_calls_ignores_non_tool_blocks():
    from memory.api.sessions import extract_tool_calls_from_message

    msg = {
        "content": [
            {"type": "text", "text": "hi"},
            {"type": "tool_use", "name": "Bash"},
            "stray-string",  # malformed entry
            {"type": "image"},
        ],
        "usage": {"input_tokens": 30, "output_tokens": 0},
    }
    [call] = extract_tool_calls_from_message(msg)
    assert call.tool_name == "Bash"
    assert call.input_tokens == 30


def test_extract_tool_calls_telemetry_and_aggregate_paths_agree():
    """The two callers (TelemetryEvent emission + aggregate stats) must
    produce identical per-tool numbers for the same input — that was the
    original bug: telemetry used divmod, aggregate used //, so 100 / 3
    came out as (34,33,33) vs (33,33,33), losing 1 token in aggregate.
    """
    from memory.api.sessions import extract_tool_calls_from_message

    # Re-derive what extract_tool_usage_from_transcript would tally
    # (same helper). The aggregate's per-tool sum must equal the
    # message's reported total.
    msg = {
        "content": [
            {"type": "tool_use", "name": "Read"},
            {"type": "tool_use", "name": "Bash"},
            {"type": "tool_use", "name": "WebSearch"},
        ],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 25,
            "cache_creation_input_tokens": 10,
        },
    }
    calls = extract_tool_calls_from_message(msg)
    aggregate_input = sum(c.input_tokens for c in calls)
    aggregate_output = sum(c.output_tokens for c in calls)
    assert aggregate_input == 100
    assert aggregate_output == 50
    assert sum(c.total_tokens for c in calls) == 100 + 50 + 25 + 10


# ====== parse_iso_datetime (moved here from the deleted parse_event_timestamp wrapper) ======


@pytest.mark.parametrize(
    "raw,expected_iso",
    [
        ("2026-05-07T01:23:45Z", "2026-05-07T01:23:45+00:00"),
        ("2026-05-07T01:23:45+00:00", "2026-05-07T01:23:45+00:00"),
        ("2026-05-07T01:23:45.500Z", "2026-05-07T01:23:45.500000+00:00"),
    ],
)
def test_parse_iso_datetime_accepts_valid(raw, expected_iso):
    from memory.common.dates import parse_iso_datetime

    parsed = parse_iso_datetime(raw)
    assert parsed is not None
    assert parsed.isoformat() == expected_iso


@pytest.mark.parametrize(
    "raw",
    ["", None, "not a timestamp", "2026-13-01T00:00:00Z"],
)
def test_parse_iso_datetime_returns_none_on_invalid(raw):
    from memory.common.dates import parse_iso_datetime

    assert parse_iso_datetime(raw) is None


# ====== iter_transcript_events streaming ======


def test_iter_transcript_events_does_not_load_whole_file(tmp_path):
    """Regression: a paginated transcript read of an offset deep into a
    large file must not load the whole file into memory before slicing.
    The previous implementation called ``Path.read_text().splitlines()``
    — O(file_size) RAM. The streaming generator is O(start) and can
    short-circuit at ``end``.

    We can't directly assert "didn't load the whole file" via memory
    instrumentation in a test, but we CAN assert: when we ask for
    events 5–10 with end=11 (exclusive), the generator stops reading
    before line 11 and never touches lines beyond that. We test by
    making the late portion of the file invalid JSON; with the bug,
    the generator would have read those lines and emitted a warning.
    With the fix, it short-circuits before reaching them.
    """
    from memory.api.sessions import iter_transcript_events

    transcript = tmp_path / "huge.jsonl"
    lines = [json.dumps({"i": i, "type": "x"}) for i in range(15)]
    # Append corrupt lines beyond the requested window. Streaming
    # version must never see them.
    lines.extend(["this is not json", "neither is this"])
    transcript.write_text("\n".join(lines) + "\n")

    import logging

    with patch.object(
        logging.getLogger("memory.api.sessions"), "warning"
    ) as mock_warn:
        events = list(iter_transcript_events(transcript, start=5, end=11))

    assert [e["i"] for e in events] == [5, 6, 7, 8, 9, 10]
    # Streaming must short-circuit before reaching the corrupt tail —
    # so no JSONDecodeError-warning should fire.
    mock_warn.assert_not_called()


def test_safe_loads_still_returns_a_list(tmp_path):
    """The list-returning wrapper kept for read_transcript still works."""
    from memory.api.sessions import safe_loads

    transcript = tmp_path / "small.jsonl"
    transcript.write_text(
        "\n".join(json.dumps({"i": i}) for i in range(3)) + "\n"
    )

    items = safe_loads(transcript)
    assert isinstance(items, list)
    assert [e["i"] for e in items] == [0, 1, 2]
