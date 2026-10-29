import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from memory.api import usage
from memory.api.usage import session_label
from memory.common import settings
from memory.common.db.models import (
    AgentRun,
    APIKey,
    CodingProject,
    HumanUser,
    LLMProviderKey,
    LLMSpend,
    Session,
    TelemetryEvent,
)
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

    response = client.get("/usage/sessions", params=window())

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

    response = client.get("/usage/sessions", params=window())

    assert [(s["label"], s["summary"]) for s in response.json()["sessions"]] == [(str(theirs.id)[:8], None)]


def test_session_stats_filters_by_api_key(client, db_session, user):
    key = add_key(db_session, user.id, "laptop")
    add_event(db_session, user.id, "token.usage", 100, "with-key", api_key_id=key.id)
    add_event(db_session, user.id, "token.usage", 100, "no-key")

    response = client.get("/usage/sessions", params={**window(), "api_key_id": key.id})

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
        "/usage/metrics",
        params={**window(), "metric": "token.usage", "granularity": 1440, "group_by": "api_key_id"},
    )

    assert sorted((d["api_key_id"], d["sum"]) for d in response.json()["data"]) == [(laptop.id, 130), (server.id, 7)]


def test_metrics_filter_by_api_key(client, db_session, user):
    laptop = add_key(db_session, user.id, "laptop")
    add_event(db_session, user.id, "token.usage", 100, api_key_id=laptop.id)
    add_event(db_session, user.id, "token.usage", 7)

    response = client.get(
        "/usage/metrics",
        params={**window(), "metric": "token.usage", "granularity": 1440, "group_by": [], "api_key_id": laptop.id},
    )

    assert [d["sum"] for d in response.json()["data"]] == [100]


def test_raw_filter_by_api_key(client, db_session, user):
    laptop = add_key(db_session, user.id, "laptop")
    add_event(db_session, user.id, "token.usage", 100, api_key_id=laptop.id)
    add_event(db_session, user.id, "token.usage", 7)

    response = client.get("/usage/raw", params={**window(), "api_key_id": laptop.id})

    assert [(e["value"], e["api_key_id"]) for e in response.json()["events"]] == [(100, laptop.id)]


def test_api_keys_lists_only_keys_with_telemetry(client, db_session, user, other_user):
    used = add_key(db_session, user.id, "laptop")
    add_key(db_session, user.id, "unused")
    theirs = add_key(db_session, other_user.id, "their-server")
    add_event(db_session, user.id, "token.usage", 1, api_key_id=used.id)
    add_event(db_session, other_user.id, "token.usage", 1, api_key_id=theirs.id)

    response = client.get("/usage/api-keys")

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

    response = regular_client.get("/usage/api-keys")

    assert [k["id"] for k in response.json()] == [used.id]


def test_session_stats_caps_number_of_sessions(client, db_session, user, monkeypatch):
    monkeypatch.setattr(usage, "MAX_SESSIONS", 2)
    for name, value in [("small", 1), ("big", 30), ("mid", 20)]:
        add_event(db_session, user.id, "token.usage", value, name)

    response = client.get("/usage/sessions", params=window())

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


@pytest.mark.parametrize(
    "name", ["mcp.call", "settings.update", "settings.restart", "team.external_membership_snapshot"]
)
def test_ingest_drops_events_named_like_audit_records(db_session, user, name):
    events = [
        ParsedTelemetryEvent(event_type="log", name=name, timestamp=NOW),
        ParsedTelemetryEvent(event_type="metric", name="token.usage", value=1.0, timestamp=NOW),
    ]

    stored = write_events_to_db(events, user.id)

    assert stored == 1
    db_session.expire_all()
    assert [e.name for e in db_session.query(TelemetryEvent).all()] == ["token.usage"]


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

    response = client.get("/usage/tools", params={**window(), "user_id": user.id})

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

    response = client.get("/usage/tools", params=window())

    assert response.status_code == 200, response.text
    assert response.json()["tools"] == []


def add_provider_key(db_session, user_id: int, label: str) -> LLMProviderKey:
    key = LLMProviderKey(user_id=user_id, provider="anthropic", label=label)
    key.key = f"sk-{label}"
    db_session.add(key)
    db_session.commit()
    return key


def add_spend(db_session, key_id: int | None, user_id: int | None, cost: str, agent: str = "system:summarize", model: str = "claude-haiku", at: datetime = NOW, kind: str = "memory"):
    db_session.add(
        LLMSpend(
            kind=kind,
            provider_key_id=key_id,
            user_id=user_id,
            agent=agent,
            model=model,
            hour=at.replace(minute=0, second=0, microsecond=0),
            cost_usd=Decimal(cost),
            calls=1,
            input_tokens=10,
            output_tokens=2,
            cache_read_tokens=3,
            cache_write_tokens=1,
        )
    )
    db_session.commit()


def add_run(db_session, user_id: int, cost: str, key_id: int | None = None, at: datetime = NOW):
    db_session.add(
        AgentRun(
            user_id=user_id,
            agent="prebuilt:research",
            spec={"model": "claude-sonnet", "system_prompt": "x"},
            input="hi",
            status="done",
            usage={"input_tokens": 100, "output_tokens": 20, "cache_read_tokens": 30, "cache_write_tokens": 10},
            cost_usd=Decimal(cost),
            max_cost_usd=Decimal(5),
            trigger_kind="manual",
            provider_key_id=key_id,
            created_at=at,
        )
    )
    db_session.commit()


def add_claude_code(db_session, user_id: int, cost: float):
    rows = [("cost.usage", cost, {})] + [
        ("token.usage", value, {"type": kind})
        for kind, value in [("input", 1000), ("output", 200), ("cacheRead", 300), ("cacheCreation", 100)]
    ]
    db_session.add_all(
        TelemetryEvent(timestamp=NOW, user_id=user_id, event_type="metric", name=name, value=value, source="claude-opus", attributes=attrs)
        for name, value, attrs in rows
    )
    db_session.commit()


def usage_params(days: int = 40, **params) -> dict:
    return {
        "from": (NOW - timedelta(days=days)).isoformat(),
        "to": (NOW + timedelta(minutes=1)).isoformat(),
        "interval": "month",
        **params,
    }


def totals(response) -> list[tuple]:
    assert response.status_code == 200, response.text
    return sorted(
        (
            tuple(row[g] for g in response.json()["group_by"]),
            round(row["cost_usd"], 6),
            row["input_tokens"],
            row["output_tokens"],
            row["cache_read_tokens"],
            row["cache_write_tokens"],
            row["tokens"],
        )
        for row in response.json()["data"]
    )


def costs(response) -> list[float]:
    return [t[1] for t in totals(response)]


def test_usage_combines_the_three_sources_by_kind(client, db_session, user):
    key = add_provider_key(db_session, user.id, "mine")
    add_spend(db_session, key.id, user.id, "0.5")
    add_run(db_session, user.id, "1.25", key.id)
    add_spend(db_session, key.id, user.id, "4", agent="prebuilt:research", kind="run")  # a purged run
    add_claude_code(db_session, user.id, 2.0)
    add_event(db_session, user.id, "tool.token.usage", 999)

    response = client.get("/usage", params=usage_params(group_by="kind"))

    assert totals(response) == [
        (("claude-code",), 2.0, 1000, 200, 300, 100, 1600),
        (("memory",), 0.5, 10, 2, 3, 1, 16),
        (("run",), 5.25, 110, 22, 33, 11, 176),
    ]


@pytest.mark.parametrize(
    "group_by, expected",
    [
        ("agent", lambda me, them: [("prebuilt:research",), ("system:search",), ("system:summarize",)]),
        ("model", lambda me, them: [("claude-haiku",), ("claude-sonnet",)]),
        ("user", lambda me, them: sorted([(me.id,), (them.id,)])),
        ("kind", lambda me, them: [("memory",), ("run",)]),
    ],
)
def test_usage_groups_by_dimension(client, db_session, user, other_user, group_by, expected):
    key = add_provider_key(db_session, user.id, "mine")
    add_spend(db_session, key.id, user.id, "0.5")
    add_spend(db_session, key.id, other_user.id, "0.5", agent="system:search")
    add_run(db_session, user.id, "1.25", key.id)

    response = client.get("/usage", params=usage_params(group_by=group_by))

    assert [t[0] for t in totals(response)] == expected(user, other_user)


def test_usage_groups_by_several_dimensions(client, db_session, user):
    key = add_provider_key(db_session, user.id, "mine")
    add_spend(db_session, key.id, user.id, "0.5")
    add_spend(db_session, key.id, user.id, "0.25", model="claude-sonnet")

    response = client.get("/usage", params=usage_params(group_by=["agent", "model"]))

    assert [(t[0], t[1]) for t in totals(response)] == [
        (("system:summarize", "claude-haiku"), 0.5),
        (("system:summarize", "claude-sonnet"), 0.25),
    ]


def test_usage_groups_by_key_and_labels_keys_and_users(client, db_session, user):
    mine = add_provider_key(db_session, user.id, "mine")
    spare = add_provider_key(db_session, user.id, "spare")
    add_spend(db_session, mine.id, user.id, "0.5")
    add_run(db_session, user.id, "1.25", spare.id)

    response = client.get("/usage", params=usage_params(group_by=["key", "user"]))

    assert [(t[0], t[1]) for t in totals(response)] == [((mine.id, user.id), 0.5), ((spare.id, user.id), 1.25)]
    assert response.json()["keys"] == {str(mine.id): "mine", str(spare.id): "spare"}
    assert response.json()["users"] == {str(user.id): "Test User"}


@pytest.mark.parametrize(
    "interval, expected",
    [
        ("hour", ["2026-03-10T12:00:00+00:00", "2026-03-10T13:00:00+00:00", "2026-03-12T09:00:00+00:00"]),
        ("day", ["2026-03-10T00:00:00+00:00", "2026-03-12T00:00:00+00:00"]),
        ("month", ["2026-03-01T00:00:00+00:00"]),
    ],
)
def test_usage_buckets_by_interval(client, db_session, user, interval, expected):
    for at in ("2026-03-10T12:05", "2026-03-10T13:30", "2026-03-12T09:00"):
        add_run(db_session, user.id, "1", at=datetime.fromisoformat(at).replace(tzinfo=timezone.utc))

    response = client.get(
        "/usage", params={"from": "2026-03-01T00:00:00Z", "to": "2026-03-31T00:00:00Z", "interval": interval}
    )

    assert response.status_code == 200, response.text
    assert [row["timestamp"] for row in response.json()["data"]] == expected


def test_usage_keeps_to_the_time_window(client, db_session, user):
    add_run(db_session, user.id, "1")
    add_run(db_session, user.id, "7", at=NOW - timedelta(days=10))

    assert costs(client.get("/usage", params=usage_params(days=5))) == [1.0]


@pytest.mark.parametrize("params", [{"group_by": "secret"}, {"interval": "minute"}])
def test_usage_rejects_unknown_parameters(client, params):
    assert client.get("/usage", params=params).status_code == 422


def seed_mixed_ownership(db_session, user, other_user) -> None:
    """Costs are powers of two, so each total names the rows it includes."""
    mine = add_provider_key(db_session, user.id, "mine")
    theirs = add_provider_key(db_session, other_user.id, "theirs")
    add_spend(db_session, theirs.id, user.id, "1")  # spent for me on someone else's key, e.g. the system key
    add_spend(db_session, mine.id, other_user.id, "2")
    add_spend(db_session, theirs.id, other_user.id, "4")
    add_spend(db_session, None, None, "128")  # a deleted key's history
    add_run(db_session, user.id, "8", mine.id)
    add_run(db_session, other_user.id, "16", theirs.id)
    add_claude_code(db_session, user.id, 32)
    add_claude_code(db_session, other_user.id, 64)


@pytest.mark.parametrize("client_fixture, expected", [("client", [255.0]), ("member_client", [43.0])])
def test_usage_access(request, db_session, user, other_user, client_fixture, expected):
    seed_mixed_ownership(db_session, user, other_user)
    api = request.getfixturevalue(client_fixture)

    assert costs(api.get("/usage", params=usage_params())) == expected


@pytest.mark.parametrize("client_fixture, expected", [("client", [86.0]), ("member_client", [2.0])])
def test_usage_user_filter_narrows_within_access(request, db_session, user, other_user, client_fixture, expected):
    seed_mixed_ownership(db_session, user, other_user)
    api = request.getfixturevalue(client_fixture)

    response = api.get("/usage", params=usage_params(user_id=other_user.id))

    assert costs(response) == expected


@pytest.mark.parametrize(
    "client_fixture, expected",
    [("client", {"mine": "mine", "theirs": "theirs"}), ("member_client", {"mine": "mine", "theirs": "shared key"})],
)
def test_usage_labels_only_your_own_keys(request, db_session, user, other_user, client_fixture, expected):
    seed_mixed_ownership(db_session, user, other_user)
    ids = {k.label: str(k.id) for k in db_session.query(LLMProviderKey)}
    api = request.getfixturevalue(client_fixture)

    response = api.get("/usage", params=usage_params(group_by="key"))

    assert response.json()["keys"] == {ids[label]: shown for label, shown in expected.items()}


def test_usage_includes_the_spend_bucket_that_from_falls_in(client, db_session, user):
    hour = NOW.replace(minute=0, second=0, microsecond=0)
    add_spend(db_session, None, user.id, "1", at=hour)

    response = client.get(
        "/usage",
        params={"from": (hour + timedelta(minutes=30)).isoformat(), "to": (hour + timedelta(hours=2)).isoformat()},
    )

    assert costs(response) == [1.0]


@pytest.mark.parametrize(
    "path, params, event, rows",
    [
        ("/usage/raw", {}, "token.usage", lambda body: body["events"]),
        ("/usage/metrics", {"metric": "token.usage", "group_by": []}, "token.usage", lambda body: body["data"]),
        ("/usage/sessions", {}, "token.usage", lambda body: body["sessions"]),
        ("/usage/tools", {}, "tool.token.usage", lambda body: body["tools"]),
        ("/usage", {}, "token.usage", lambda body: body["data"]),
    ],
)
def test_to_is_exclusive(client, db_session, user, path, params, event, rows):
    add_event(db_session, user.id, event, 5, minutes_ago=0)

    response = client.get(path, params={**params, "from": (NOW - timedelta(hours=1)).isoformat(), "to": NOW.isoformat()})

    assert response.status_code == 200, response.text
    assert rows(response.json()) == []


@pytest.mark.parametrize("client_fixture, expected", [("client", ["Other User", "Test User"]), ("member_client", ["Test User"])])
def test_users_lists_whoever_has_usage_you_can_see(request, db_session, user, other_user, client_fixture, expected):
    theirs = add_provider_key(db_session, other_user.id, "theirs")
    add_spend(db_session, theirs.id, user.id, "1")
    add_run(db_session, other_user.id, "1", theirs.id)
    db_session.add(HumanUser(name="Idle", email="idle@example.com", password_hash="x"))
    db_session.commit()
    api = request.getfixturevalue(client_fixture)

    response = api.get("/usage/users")

    assert response.status_code == 200, response.text
    assert sorted(u["name"] for u in response.json()) == expected
