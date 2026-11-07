"""Upgrading turns matches into deliveries: recent per-item ones pending again (an
edit when the trigger matched the item before), the rest done."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def load_migration():
    path = Path(__file__).parents[4] / "db/migrations/versions/20261212_trigger_deliveries.py"
    spec = importlib.util.spec_from_file_location("trigger_deliveries_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migrate(db, step):
    with Operations.context(MigrationContext.configure(db)):
        step()


def test_upgrade_repends_recent_per_item_matches_and_downgrade_reverts(db_session):
    module = load_migration()
    db = db_session.connection()
    db.execute(text("CREATE SCHEMA trigger_deliveries_migration_test"))
    db.execute(text("SET LOCAL search_path TO trigger_deliveries_migration_test"))
    db.execute(text("CREATE TABLE agent_runs (id varchar PRIMARY KEY)"))
    db.execute(text("CREATE TABLE agent_triggers (id int PRIMARY KEY, config jsonb NOT NULL)"))
    db.execute(
        text(
            "CREATE TABLE agent_trigger_matches (trigger_id int, item_id bigint, "
            "content_hash bytea, created_at timestamptz NOT NULL, "
            "PRIMARY KEY (trigger_id, item_id, content_hash))"
        )
    )
    db.execute(
        text(
            "INSERT INTO agent_triggers VALUES "
            """(1, '{"delivery": {"mode": "per_item"}}'), (2, '{}'), """
            """(3, '{"delivery": {"mode": "batch"}}')"""
        )
    )
    db.execute(
        text(
            "INSERT INTO agent_trigger_matches VALUES "
            "(1, 10, 'a', now() - interval '5 minutes'), "
            "(1, 10, 'b', now() - interval '3 hours'), "
            "(1, 11, 'a', now() - interval '2 hours'), "
            "(2, 10, 'a', now()), "
            "(3, 10, 'a', now())"
        )
    )
    matches = (
        "SELECT trigger_id, item_id, content_hash, status, edited, changes "
        "FROM agent_trigger_matches ORDER BY 1, 2, 3"
    )

    migrate(db, module.upgrade)
    upgraded = db.execute(text(matches)).all()
    migrate(db, module.downgrade)
    columns = db.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'trigger_deliveries_migration_test' "
            "AND table_name = 'agent_trigger_matches' ORDER BY ordinal_position"
        )
    ).scalars().all()
    db_session.rollback()

    assert [tuple(bytes(v) if isinstance(v, memoryview) else v for v in row) for row in upgraded] == [
        (1, 10, b"a", "pending", True, ["edited"]),
        (1, 10, b"b", "done", False, []),
        (1, 11, b"a", "done", False, []),
        (2, 10, b"a", "pending", False, ["new"]),
        (3, 10, b"a", "done", False, []),
    ]
    assert columns == ["trigger_id", "item_id", "content_hash", "created_at"]
