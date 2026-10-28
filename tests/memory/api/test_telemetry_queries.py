import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from memory.api import telemetry
from memory.api.telemetry import session_label
from memory.common import settings
from memory.common.db.models import APIKey, CodingProject, HumanUser, Session, TelemetryEvent
from memory.common.telemetry import ParsedTelemetryEvent, write_events_to_db

NOW = datetime.now(timezone.utc)


@pytest.fixture
def other_user(db_session):
    other = HumanUser(
        name="Other User",
        email="other-telemetry@example.com",
        password_hash="bcrypt_hash_placeholder",
    )
    db_session.add(other)
    db_session.commit()
    return other


def add_key(db_session, user_id: int, name: str) -> APIKey:
    key = APIKey.create(user_id=user_id, name=name)
    db_session.add(key)
    db_session.commit()
    return key


def add_event(db_session, user_id: int, name: str, value: float, session_id: str = "s", api_key_id: int | None = None, minutes_ago: int = 5):
    db_session.add(
        TelemetryEvent(
            timestamp=NOW - timedelta(minutes=minutes_ago),
            user_id=user_id,
            event_type="metric",
            name=name,
            value=value,
            session_id=session_id,
            api_key_id=api_key_id,
        )
    )
    db_session.commit()


def window() -> dict:
    return {"from": (NOW - timedelta(hours=1)).isoformat(), "to": (NOW + timedelta(minutes=1)).isoformat()}


@pytest.mark.parametrize(
    "summary, project_dir, started_at, expected",
    [
        ("Fixed the login redirect bug. Then added tests.", None, None, "Fixed the login redirect bug"),
        ("Refactored the scheduler; moved retries into Celery.", None, None, "Refactored the scheduler"),
        (
            "Investigated why the nightly backup job silently skipped every other table without logging",
            None,
            None,
            "Investigated why the nightly backup job…",
        ),
        ("   ", "/Users/dan/code/memory", datetime(2026, 10, 24, 9, tzinfo=timezone.utc), "memory · 2026-10-24"),
        (None, "/Users/dan/code/memory/", None, "memory"),
        (None, None, None, "ec2a0d98"),
        ("? What now. More", None, None, "What now"),
        ("...", "/Users/dan/code/memory", None, "memory"),
        ("?!", None, None, "ec2a0d98"),
        (". ; hmm", None, None, "hmm"),
        ("# Fix the parser\nThen more text", None, None, "Fix the parser"),
        ("- bullet one\n- bullet two", None, None, "bullet one"),
        ("* item one. two", None, None, "item one"),
        ("First line without stop\nSecond line.", None, None, "First line without stop"),
        ("\n\n  \n", None, None, "ec2a0d98"),
    ],
)
def test_session_label(summary, project_dir, started_at, expected):
    assert session_label("ec2a0d98-dbca-4399-92fc-516ad323da36", summary, project_dir, started_at) == expected


def test_session_stats_aggregates_per_session_with_names(client, db_session, user):
    project = CodingProject(user_id=user.id, directory="/home/u/memory", name="memory", source="host")
    db_session.add(project)
    db_session.commit()
    named = Session(id=uuid4(), user_id=user.id, coding_project_id=project.id, summary="Added API key grouping. More.")
    db_session.add(named)
    db_session.commit()
    add_event(db_session, user.id, "token.usage", 100, str(named.id), minutes_ago=30)
    add_event(db_session, user.id, "token.usage", 50, str(named.id), minutes_ago=10)
    add_event(db_session, user.id, "cost.usage", 0.25, str(named.id))
    add_event(db_session, user.id, "token.usage", 10, "not-a-uuid")

    response = client.get("/telemetry/sessions", params=window())

    assert response.status_code == 200, response.text
    sessions = response.json()["sessions"]
    assert [
        (s["session_id"], s["label"], s["summary"], s["project"], s["total_tokens"], s["total_cost"], s["event_count"])
        for s in sessions
    ] == [
        (str(named.id), "Added API key grouping", "Added API key grouping. More.", "/home/u/memory", 150, 0.25, 2),
        ("not-a-uuid", "not-a-uu", None, None, 10, 0, 1),
    ]
    assert sessions[0]["first_seen"] < sessions[0]["last_seen"]


def test_session_stats_hides_names_of_other_users_sessions(client, db_session, other_user):
    theirs = Session(id=uuid4(), user_id=other_user.id, summary="Secret work on payroll.")
    db_session.add(theirs)
    db_session.commit()
    add_event(db_session, other_user.id, "token.usage", 100, str(theirs.id))

    response = client.get("/telemetry/sessions", params=window())

    assert [(s["label"], s["summary"]) for s in response.json()["sessions"]] == [(str(theirs.id)[:8], None)]


def test_session_stats_filters_by_api_key(client, db_session, user):
    key = add_key(db_session, user.id, "laptop")
    add_event(db_session, user.id, "token.usage", 100, "with-key", api_key_id=key.id)
    add_event(db_session, user.id, "token.usage", 100, "no-key")

    response = client.get("/telemetry/sessions", params={**window(), "api_key_id": key.id})

    assert [s["session_id"] for s in response.json()["sessions"]] == ["with-key"]


def test_ingest_records_authenticating_api_key(client, db_session, user):
    key = add_key(db_session, user.id, "laptop")
    payload = {
        "resourceMetrics": [
            {
                "resource": {"attributes": []},
                "scopeMetrics": [
                    {"metrics": [{"name": "token.usage", "sum": {"dataPoints": [{"asDouble": 42.0, "attributes": []}]}}]}
                ],
            }
        ]
    }

    response = client.post(
        "/telemetry/v1/metrics",
        content=json.dumps(payload),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key.key}"},
    )

    assert response.status_code == 200, response.text
    db_session.expire_all()
    assert [(e.value, e.api_key_id) for e in db_session.query(TelemetryEvent).all()] == [(42.0, key.id)]


def test_metrics_group_by_api_key(client, db_session, user):
    laptop = add_key(db_session, user.id, "laptop")
    server = add_key(db_session, user.id, "server")
    add_event(db_session, user.id, "token.usage", 100, api_key_id=laptop.id)
    add_event(db_session, user.id, "token.usage", 30, api_key_id=laptop.id)
    add_event(db_session, user.id, "token.usage", 7, api_key_id=server.id)

    response = client.get(
        "/telemetry/metrics",
        params={**window(), "metric": "token.usage", "granularity": 1440, "group_by": "api_key_id"},
    )

    assert sorted((d["api_key_id"], d["sum"]) for d in response.json()["data"]) == [(laptop.id, 130), (server.id, 7)]


def test_metrics_filter_by_api_key(client, db_session, user):
    laptop = add_key(db_session, user.id, "laptop")
    add_event(db_session, user.id, "token.usage", 100, api_key_id=laptop.id)
    add_event(db_session, user.id, "token.usage", 7)

    response = client.get(
        "/telemetry/metrics",
        params={**window(), "metric": "token.usage", "granularity": 1440, "group_by": [], "api_key_id": laptop.id},
    )

    assert [d["sum"] for d in response.json()["data"]] == [100]


def test_raw_filter_by_api_key(client, db_session, user):
    laptop = add_key(db_session, user.id, "laptop")
    add_event(db_session, user.id, "token.usage", 100, api_key_id=laptop.id)
    add_event(db_session, user.id, "token.usage", 7)

    response = client.get("/telemetry/raw", params={**window(), "api_key_id": laptop.id})

    assert [(e["value"], e["api_key_id"]) for e in response.json()["events"]] == [(100, laptop.id)]


def test_api_keys_lists_only_keys_with_telemetry(client, db_session, user, other_user):
    used = add_key(db_session, user.id, "laptop")
    add_key(db_session, user.id, "unused")
    theirs = add_key(db_session, other_user.id, "their-server")
    add_event(db_session, user.id, "token.usage", 1, api_key_id=used.id)
    add_event(db_session, other_user.id, "token.usage", 1, api_key_id=theirs.id)

    response = client.get("/telemetry/api-keys")

    assert sorted((k["id"], k["name"], k["user_id"]) for k in response.json()) == [
        (used.id, "laptop", user.id),
        (theirs.id, "their-server", other_user.id),
    ]
    assert all("key" not in k for k in response.json())


def test_api_keys_non_admin_sees_only_own_keys(regular_client, db_session, user, other_user):
    used = add_key(db_session, user.id, "laptop")
    theirs = add_key(db_session, other_user.id, "their-server")
    add_event(db_session, user.id, "token.usage", 1, api_key_id=used.id)
    add_event(db_session, other_user.id, "token.usage", 1, api_key_id=theirs.id)

    response = regular_client.get("/telemetry/api-keys")

    assert [k["id"] for k in response.json()] == [used.id]


def test_session_stats_caps_number_of_sessions(client, db_session, user, monkeypatch):
    monkeypatch.setattr(telemetry, "MAX_SESSIONS", 2)
    for name, value in [("small", 1), ("big", 30), ("mid", 20)]:
        add_event(db_session, user.id, "token.usage", value, name)

    response = client.get("/telemetry/sessions", params=window())

    assert [s["session_id"] for s in response.json()["sessions"]] == ["big", "mid"]


def test_write_events_survives_deleted_api_key(db_session, user):
    key = add_key(db_session, user.id, "gone")
    key_id = key.id
    db_session.delete(key)
    db_session.commit()
    event = ParsedTelemetryEvent(event_type="metric", name="token.usage", value=1.0, timestamp=NOW)

    stored = write_events_to_db([event], user.id, key_id)

    assert stored == 1
    db_session.expire_all()
    assert [e.api_key_id for e in db_session.query(TelemetryEvent).all()] == [None]


def add_tool_call(db_session, user_id: int, tool: str, input_tokens: int, output_tokens: int, session_id: str = "s", minutes_ago: int = 5):
    db_session.add(
        TelemetryEvent(
            timestamp=NOW - timedelta(minutes=minutes_ago),
            user_id=user_id,
            event_type="metric",
            name="tool.token.usage",
            value=input_tokens + output_tokens,
            session_id=session_id,
            tool_name=tool,
            attributes={"input_tokens": input_tokens, "output_tokens": output_tokens, "cache_read_input_tokens": 1},
        )
    )
    db_session.commit()


def test_tool_usage_aggregates_tool_calls(client, db_session, user, other_user):
    for tokens in (10, 20, 30, 40):
        add_tool_call(db_session, user.id, "Bash", tokens, 1, session_id="a")
    add_tool_call(db_session, user.id, "Read", 5, 0, session_id="b")
    add_tool_call(db_session, user.id, "Bash", 1000, 0, minutes_ago=120)
    add_tool_call(db_session, other_user.id, "Bash", 1000, 0)

    response = client.get("/sessions/stats/tool-usage", params={**window(), "user_id": user.id})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["session_count"] == 2
    assert [
        (t["tool_name"], t["call_count"], t["input_tokens"], t["output_tokens"], t["cache_read_tokens"], t["total_tokens"])
        for t in body["tools"]
    ] == [("Bash", 4, 100, 4, 4, 108), ("Read", 1, 5, 0, 1, 6)]
    assert body["tools"][0]["per_call"] == pytest.approx({"median": 26, "p75": 33.5, "p90": 38, "p99": 40.7, "min": 11, "max": 41})


def test_tool_usage_does_not_read_transcripts(client, db_session, user, tmp_path, monkeypatch):
    """The page's other panels read telemetry_events only; tool usage must agree with them."""
    monkeypatch.setattr(settings, "SESSIONS_STORAGE_DIR", tmp_path)
    session = Session(id=uuid4(), user_id=user.id, transcript_path="t.jsonl")
    db_session.add(session)
    db_session.commit()
    event = {
        "type": "assistant",
        "timestamp": (NOW - timedelta(minutes=5)).isoformat(),
        "message": {"content": [{"type": "tool_use", "name": "Bash"}], "usage": {"input_tokens": 100}},
    }
    (tmp_path / "t.jsonl").write_text(json.dumps(event) + "\n")

    response = client.get("/sessions/stats/tool-usage", params=window())

    assert response.status_code == 200, response.text
    assert response.json()["tools"] == []
