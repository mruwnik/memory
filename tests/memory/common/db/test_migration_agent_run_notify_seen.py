"""Upgrading adds the notify and seen columns and counts runs their owner started that already ended as seen."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def load_migration():
    path = Path(__file__).parents[4] / "db/migrations/versions/20261221_agent_run_notify_seen.py"
    spec = importlib.util.spec_from_file_location("agent_run_notify_seen_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def columns(db) -> set[str]:
    rows = db.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'notify_seen_migration_test' AND table_name = 'agent_runs'"
        )
    )
    return {r[0] for r in rows}


def test_upgrade_counts_ended_owner_started_runs_as_seen_and_downgrade_drops_the_columns(db_session):
    module = load_migration()
    db = db_session.connection()
    db.execute(text("CREATE SCHEMA notify_seen_migration_test"))
    db.execute(text("SET LOCAL search_path TO notify_seen_migration_test"))
    db.execute(
        text(
            "CREATE TABLE agent_runs (id text PRIMARY KEY, user_id int, parent_run_id text, "
            "trigger_kind text NOT NULL, finished_at timestamptz)"
        )
    )
    db.execute(
        text(
            "INSERT INTO agent_runs (id, parent_run_id, trigger_kind, finished_at) VALUES "
            "('ended', NULL, 'manual', '2026-11-01T00:00:00Z'), "
            "('ended-mcp', NULL, 'mcp', '2026-11-01T00:00:00Z'), "
            "('live', NULL, 'manual', NULL), "
            "('scheduled', NULL, 'schedule', '2026-11-01T00:00:00Z'), "
            "('subrun', 'ended', 'manual', '2026-11-01T00:00:00Z')"
        )
    )

    with Operations.context(MigrationContext.configure(db)):
        module.upgrade()
    upgraded = db.execute(
        text(
            "SELECT id, notify_on_finish, notified_at, seen_at = finished_at, seen_at IS NULL "
            "FROM agent_runs ORDER BY id"
        )
    ).all()
    with Operations.context(MigrationContext.configure(db)):
        module.downgrade()
    downgraded = columns(db)
    db_session.rollback()

    # agent_list_runs(unseen=true) never reads seen_at of scheduled runs or sub-runs.
    assert upgraded == [
        ("ended", False, None, True, False),
        ("ended-mcp", False, None, True, False),
        ("live", False, None, None, True),
        ("scheduled", False, None, None, True),
        ("subrun", False, None, None, True),
    ]
    assert downgraded == {"id", "user_id", "parent_run_id", "trigger_kind", "finished_at"}
