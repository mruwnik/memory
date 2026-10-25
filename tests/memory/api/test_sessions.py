"""Tests for sessions API endpoints."""

import json
import logging
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from memory.api.sessions import iter_transcript_events
from memory.common import settings
from memory.common.db.models.sessions import CodingProject, Session


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


# ====== iter_transcript_events ======


def test_iter_transcript_events_skips_blank_and_warns_on_malformed_lines(tmp_path, caplog):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        "\n".join([json.dumps({"i": 0}), "", "not json", json.dumps({"i": 1}), "nope"]) + "\n"
    )

    with caplog.at_level(logging.WARNING, logger="memory.api.sessions"):
        events = list(iter_transcript_events(transcript))

    assert [e["i"] for e in events] == [0, 1]
    assert [r.getMessage() for r in caplog.records] == [
        f"transcript {transcript}: 2 unparseable JSON line(s) skipped"
    ]


# ====== POST /sessions/ingest/batch ======


@pytest.fixture
def sessions_storage_dir(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    with patch.object(settings, "SESSIONS_STORAGE_DIR", sessions_dir):
        yield sessions_dir


def event(**extra):
    return {"uuid": str(uuid4()), "timestamp": "2024-01-15T10:00:00Z", "type": "user", **extra}


def ingest(client, session_id, events, **fields):
    return client.post(
        "/sessions/ingest/batch",
        json={"session_id": session_id, "events": events, **fields},
    )


def test_ingest_batch_creates_session(client, db_session, user, sessions_storage_dir):
    session_id = str(uuid4())

    response = ingest(client, session_id, [event(message={"content": "Hello"})])

    assert response.status_code == 200
    assert response.json() == {
        "status": "accepted",
        "session_id": session_id,
        "accepted": 1,
        "duplicates": 0,
    }
    row = db_session.get(Session, UUID(session_id))
    assert row is not None
    assert row.user_id == user.id


def test_ingest_batch_appends_to_transcript_and_skips_duplicates(
    client, db_session, user, sessions_storage_dir
):
    session_id = str(uuid4())
    first, second = event(), event(type="assistant")

    ingest(client, session_id, [first])
    response = ingest(client, session_id, [first, second])

    assert response.json()["accepted"] == 1
    assert response.json()["duplicates"] == 1
    row = db_session.get(Session, UUID(session_id))
    lines = (sessions_storage_dir / row.transcript_path).read_text().strip().split("\n")
    assert [json.loads(line)["uuid"] for line in lines] == [first["uuid"], second["uuid"]]


def test_ingest_batch_creates_project_from_cwd(client, db_session, user, sessions_storage_dir):
    cwd = "/home/user/new-project"

    response = ingest(client, str(uuid4()), [event()], cwd=cwd, source="test-host")

    assert response.status_code == 200
    project = (
        db_session.query(CodingProject)
        .filter(CodingProject.user_id == user.id, CodingProject.directory == cwd)  # type: ignore[attr-defined]
        .one()
    )
    assert project.source == "test-host"


def test_ingest_batch_rejects_invalid_session_uuid(client, user, sessions_storage_dir):
    response = ingest(client, "not-a-valid-uuid", [event()])

    assert response.status_code == 400
    assert "Invalid session UUID" in response.json()["detail"]


def test_ingest_batch_links_a_subagent_to_its_parent(
    client, db_session, user, sessions_storage_dir
):
    parent_id, child_id = str(uuid4()), str(uuid4())
    ingest(client, parent_id, [event()])

    response = ingest(client, child_id, [event(type="assistant")], parent_session_id=parent_id)

    assert response.status_code == 200
    assert db_session.get(Session, UUID(child_id)).parent_session_id == UUID(parent_id)


def test_ingest_batch_records_git_branch_and_version(
    client, db_session, user, sessions_storage_dir
):
    session_id = str(uuid4())

    response = ingest(
        client,
        session_id,
        [event(git_branch="feature/new-feature", version="1.2.3")],
        cwd="/home/user/project",
    )

    assert response.status_code == 200
    row = db_session.get(Session, UUID(session_id))
    assert (row.git_branch, row.tool_version) == ("feature/new-feature", "1.2.3")
