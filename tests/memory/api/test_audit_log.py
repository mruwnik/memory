from datetime import datetime, timedelta, timezone

import pytest

from memory.common.db.models import AuditEvent

NOW = datetime.now(timezone.utc)


@pytest.fixture
def audit_rows(db_session, user):
    db_session.add_all(
        [
            AuditEvent(
                timestamp=NOW - timedelta(minutes=2),
                actor_id=user.id,
                actor_email=user.email,
                action="mcp.call",
                target="upsert",
                attributes={"name": "Acme"},
            ),
            AuditEvent(
                timestamp=NOW - timedelta(minutes=1),
                actor_id=None,
                actor_email="gone@example.com",
                action="settings.restart",
            ),
        ]
    )
    db_session.commit()


def test_audit_log_is_admin_only(regular_client, audit_rows):
    assert regular_client.get("/audit").status_code == 403


@pytest.mark.parametrize(
    "params, total, expected",
    [
        ({}, 2, [("settings.restart", None, "gone@example.com"), ("mcp.call", "upsert", "test@example.com")]),
        ({"action": "mcp.call"}, 1, [("mcp.call", "upsert", "test@example.com")]),
        ({"actor_id": 1}, 1, [("mcp.call", "upsert", "test@example.com")]),
        ({"limit": 1, "offset": 1}, 2, [("mcp.call", "upsert", "test@example.com")]),
        ({"from": (NOW - timedelta(seconds=90)).isoformat()}, 1, [("settings.restart", None, "gone@example.com")]),
    ],
)
def test_audit_log_lists_newest_first(client, audit_rows, params, total, expected):
    response = client.get("/audit", params=params)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == total
    assert [(e["action"], e["target"], e["actor_email"]) for e in body["events"]] == expected
