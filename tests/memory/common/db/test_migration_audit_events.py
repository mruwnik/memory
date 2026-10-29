"""The audit_events migration moves audit rows out of telemetry_events and back."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def load_migration():
    path = Path(__file__).parents[4] / "db/migrations/versions/20261120_audit_events.py"
    spec = importlib.util.spec_from_file_location("audit_events_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TELEMETRY = "SELECT user_id, name, source, tool_name, attributes FROM telemetry_events ORDER BY id"


def test_audit_rows_move_to_audit_events_and_back(db_session):
    module = load_migration()
    db = db_session.connection()
    db.execute(text("CREATE SCHEMA audit_events_migration_test"))
    db.execute(text("SET LOCAL search_path TO audit_events_migration_test"))
    db.execute(text("CREATE TABLE users (id integer PRIMARY KEY, email text NOT NULL)"))
    db.execute(
        text(
            "CREATE TABLE telemetry_events (id bigserial PRIMARY KEY,"
            " timestamp timestamptz NOT NULL DEFAULT now(), user_id bigint NOT NULL,"
            " event_type varchar(50) NOT NULL, name varchar(100) NOT NULL, value float,"
            " session_id varchar(100), source varchar(100), tool_name varchar(100),"
            " attributes jsonb NOT NULL DEFAULT '{}', body text, api_key_id integer)"
        )
    )
    db.execute(text("INSERT INTO users VALUES (1, 'a@x.com')"))
    db.execute(
        text(
            "INSERT INTO telemetry_events (user_id, event_type, name, value, source, tool_name, attributes) VALUES"
            " (1, 'metric', 'token.usage', 5, 'opus', NULL, '{}'),"
            """ (1, 'log', 'mcp.call', NULL, NULL, 'upsert', '{"name": "Acme"}'),"""
            """ (1, 'log', 'settings.update', NULL, 'settings', NULL, '{"changes": {}}'),"""
            " (1, 'log', 'settings.restart', NULL, 'settings', NULL, '{}'),"
            """ (1, 'log', 'team.external_membership_snapshot', NULL, 'github', 'upsert', '{"team_id": 3}'),"""
            """ (1, 'log', 'team.external_membership_snapshot', NULL, NULL, 'upsert', '{"team_id": 4}')"""
        )
    )
    # Shaped like OTLP uploads, which could have forged these names.
    db.execute(
        text(
            "INSERT INTO telemetry_events (user_id, event_type, name, api_key_id, session_id, body) VALUES"
            " (1, 'log', 'mcp.call', 7, NULL, NULL),"
            " (1, 'log', 'settings.update', NULL, 's1', NULL),"
            " (1, 'log', 'settings.restart', NULL, NULL, 'forged')"
        )
    )

    with Operations.context(MigrationContext.configure(db)):
        module.upgrade()
        moved = db.execute(
            text("SELECT actor_id, actor_email, action, target, attributes FROM audit_events ORDER BY id")
        ).all()
        kept = db.execute(text(TELEMETRY)).all()
        module.downgrade()
        restored = db.execute(text(TELEMETRY)).all()
    db_session.rollback()

    assert moved == [
        (1, "a@x.com", "mcp.call", "upsert", {"name": "Acme"}),
        (1, "a@x.com", "settings.update", None, {"changes": {}}),
        (1, "a@x.com", "settings.restart", None, {}),
        (1, "a@x.com", "team.external_membership_snapshot", "upsert", {"team_id": 3, "source": "github"}),
        (1, "a@x.com", "team.external_membership_snapshot", "upsert", {"team_id": 4}),
    ]
    assert kept == [(1, "token.usage", "opus", None, {})]
    assert restored == [
        (1, "token.usage", "opus", None, {}),
        (1, "mcp.call", None, "upsert", {"name": "Acme"}),
        (1, "settings.update", "settings", None, {"changes": {}}),
        (1, "settings.restart", "settings", None, {}),
        (1, "team.external_membership_snapshot", "github", "upsert", {"team_id": 3}),
        (1, "team.external_membership_snapshot", None, "upsert", {"team_id": 4}),
    ]
