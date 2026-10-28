"""Upgrading moves a run's prebuilt/system agent slug out of trigger_ref into its own column."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def load_migration():
    path = Path(__file__).parents[4] / "db/migrations/versions/20261118_run_ledger.py"
    spec = importlib.util.spec_from_file_location("run_ledger_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_moves_the_agent_slug_and_downgrade_restores_it(db_session):
    module = load_migration()
    db = db_session.connection()
    db.execute(text("CREATE SCHEMA run_ledger_migration_test"))
    db.execute(text("SET LOCAL search_path TO run_ledger_migration_test"))
    db.execute(
        text(
            "CREATE TABLE agent_runs (id text PRIMARY KEY, parent_run_id text, "
            "trigger_ref jsonb NOT NULL, finished_at timestamptz, steps_purged_at timestamptz)"
        )
    )
    db.execute(
        text(
            "INSERT INTO agent_runs (id, trigger_ref) VALUES "
            """('a', '{"agent": "fact-check", "x": 1}'), ('b', '{"definition_id": 3}')"""
        )
    )
    rows = "SELECT id, agent, trigger_ref, executor FROM agent_runs ORDER BY id"

    with Operations.context(MigrationContext.configure(db)):
        module.upgrade()
    upgraded = db.execute(text(rows)).all()
    with Operations.context(MigrationContext.configure(db)):
        module.downgrade()
    downgraded = db.execute(text("SELECT id, trigger_ref FROM agent_runs ORDER BY id")).all()
    db_session.rollback()

    assert upgraded == [
        ("a", "fact-check", {"x": 1}, "server"),
        ("b", None, {"definition_id": 3}, "server"),
    ]
    assert downgraded == [("a", {"agent": "fact-check", "x": 1}), ("b", {"definition_id": 3})]
