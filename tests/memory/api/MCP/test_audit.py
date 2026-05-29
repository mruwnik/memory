"""Tests for the audit_call decorator (MCP mutating-call logging)."""

import inspect

import pytest

from memory.api.MCP.audit import MAX_DEPTH, audit_call, sanitize_value
from memory.common.db.models import TelemetryEvent
from tests.conftest import mcp_auth_context


@pytest.mark.asyncio
async def test_audit_call_logs_call(db_session, admin_session):
    @audit_call()
    async def upsert(name: str, password: str | None = None):
        return {"ok": name}

    with mcp_auth_context(admin_session.id):
        result = await upsert(name="Acme", password="hunter2")
    assert result == {"ok": "Acme"}

    row = db_session.query(TelemetryEvent).filter(TelemetryEvent.name == "mcp.call").one()
    assert row.tool_name == "upsert"
    assert row.attributes["name"] == "Acme"


@pytest.mark.asyncio
async def test_audit_call_redacts_and_truncates(db_session, admin_session):
    @audit_call(redact=("password",), max_value_len=5)
    async def upsert(name: str, password: str | None = None):
        return None

    with mcp_auth_context(admin_session.id):
        await upsert(name="ABCDEFGHIJ", password="hunter2")

    row = db_session.query(TelemetryEvent).filter(TelemetryEvent.name == "mcp.call").one()
    assert row.attributes["password"] == "***"
    assert row.attributes["name"].startswith("ABCDE")
    assert row.attributes["name"] != "ABCDEFGHIJ"


@pytest.mark.asyncio
async def test_audit_call_preserves_signature():
    @audit_call()
    async def upsert(name: str, slug: str | None = None):
        return None

    params = list(inspect.signature(upsert).parameters)
    assert params == ["name", "slug"]


@pytest.mark.asyncio
async def test_audit_call_default_redact_backstop(db_session, admin_session):
    """A secret-named arg is masked even when the call site forgets redact=."""
    @audit_call()  # no explicit redact
    async def upsert(name: str, token: str | None = None):
        return None

    with mcp_auth_context(admin_session.id):
        await upsert(name="x", token="sekret")

    row = db_session.query(TelemetryEvent).filter(TelemetryEvent.name == "mcp.call").one()
    assert row.attributes["token"] == "***"
    assert row.attributes["name"] == "x"


def test_sanitize_value_caps_recursion_depth():
    value = "deep"
    for _ in range(MAX_DEPTH + 5):
        value = {"k": value}

    out = sanitize_value(value, frozenset(), 2000)
    cur = out
    for _ in range(MAX_DEPTH):
        assert isinstance(cur, dict)
        cur = cur["k"]
    assert cur == "…(max depth)"


def test_sanitize_value_redacts_nested_keys():
    value = {"outer": {"password": "p", "ok": "v"}, "items": [{"token": "t"}]}
    out = sanitize_value(value, frozenset({"password", "token"}), 2000)
    assert out["outer"]["password"] == "***"
    assert out["outer"]["ok"] == "v"
    assert out["items"][0]["token"] == "***"


@pytest.mark.asyncio
async def test_audit_call_failure_does_not_break_tool(db_session, admin_session, monkeypatch):
    monkeypatch.setattr(
        "memory.api.MCP.audit.record_event",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    @audit_call()
    async def upsert(name: str):
        return {"ok": True}

    with mcp_auth_context(admin_session.id):
        assert await upsert(name="x") == {"ok": True}
