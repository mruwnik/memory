"""Upgrading gives triggers an owner, a name and a handler, and moves run dedupe onto the trigger."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def load_migration():
    path = Path(__file__).parents[4] / "db/migrations/versions/20261210_trigger_subscriptions.py"
    spec = importlib.util.spec_from_file_location("trigger_subscriptions_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migrate(db, step):
    with Operations.context(MigrationContext.configure(db)):
        step()


def test_upgrade_backfills_triggers_and_links_runs_and_downgrade_reverts(db_session):
    module = load_migration()
    db = db_session.connection()
    db.execute(text("CREATE SCHEMA trigger_subscriptions_migration_test"))
    db.execute(text("SET LOCAL search_path TO trigger_subscriptions_migration_test"))
    db.execute(text("CREATE TABLE users (id int PRIMARY KEY)"))
    db.execute(
        text("CREATE TABLE agent_definitions (id int PRIMARY KEY, user_id int NOT NULL)")
    )
    db.execute(
        text(
            "CREATE TABLE agent_triggers (id serial PRIMARY KEY, "
            "definition_id int NOT NULL REFERENCES agent_definitions(id) ON DELETE CASCADE)"
        )
    )
    db.execute(
        text(
            "CREATE TABLE agent_runs (id text PRIMARY KEY, definition_id int, "
            "trigger_kind text NOT NULL, trigger_ref jsonb NOT NULL, dedupe_key text, "
            "created_at timestamptz NOT NULL)"
        )
    )
    db.execute(
        text(
            "CREATE UNIQUE INDEX agent_runs_definition_dedupe_key ON agent_runs "
            "(definition_id, dedupe_key) WHERE dedupe_key IS NOT NULL"
        )
    )
    db.execute(text("INSERT INTO users VALUES (1), (2)"))
    db.execute(text("INSERT INTO agent_definitions VALUES (10, 1), (11, 2), (12, 2)"))
    db.execute(text("INSERT INTO agent_triggers (id, definition_id) VALUES (5, 10), (6, 12)"))
    # Trigger 6 moved from definition 11 to 12, then redelivered: only its first run keeps the key.
    db.execute(
        text(
            "INSERT INTO agent_runs VALUES "
            """('a', 10, 'ingest', '{"trigger_id": 5}', '5:x', now() - interval '2 hours'), """
            """('b', 11, 'ingest', '{"trigger_id": 6}', '6:y', now() - interval '1 hour'), """
            """('c', 12, 'ingest', '{"trigger_id": 6}', '6:y', now()), """
            """('d', 10, 'ingest', '{"trigger_id": 99}', '99:z', now()), """
            """('e', 10, 'cron', '{"task_id": "t"}', 'exec-1', now())"""
        )
    )
    triggers = "SELECT id, user_id, name, handler, definition_id FROM agent_triggers ORDER BY id"
    runs = "SELECT id, trigger_id FROM agent_runs ORDER BY id"

    migrate(db, module.upgrade)
    upgraded = db.execute(text(triggers)).all(), db.execute(text(runs)).all()
    db.execute(
        text(
            "INSERT INTO agent_triggers (user_id, name, handler) "
            """VALUES (1, 'q', '{"queue": "inbox"}')"""
        )
    )
    migrate(db, module.downgrade)
    downgraded = db.execute(text("SELECT id, definition_id FROM agent_triggers ORDER BY id")).all()
    db_session.rollback()

    assert upgraded == (
        [
            (5, 1, "trigger-5", {"agent": 10}, 10),
            (6, 2, "trigger-6", {"agent": 12}, 12),
        ],
        [("a", 5), ("b", 6), ("c", None), ("d", None), ("e", None)],
    )
    assert downgraded == [(5, 10), (6, 12)]
