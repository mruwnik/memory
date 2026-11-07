"""Upgrading lets subscriptions take raw sources and lets deliveries outlive their
items, whatever an older instance named the foreign key; downgrading undoes both."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def load_migration():
    path = Path(__file__).parents[4] / "db/migrations/versions/20261216_raw_subscriptions.py"
    spec = importlib.util.spec_from_file_location("raw_subscriptions_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migrate(db, step):
    with Operations.context(MigrationContext.configure(db)):
        step()


@pytest.mark.parametrize("fkey", ["agent_trigger_matches_item_id_fkey", "fk_matches_item"])
def test_upgrade_takes_raw_triggers_and_keeps_deliveries_of_removed_items(db_session, fkey):
    module = load_migration()
    db = db_session.connection()
    db.execute(text("CREATE SCHEMA raw_subscriptions_migration_test"))
    db.execute(text("SET LOCAL search_path TO raw_subscriptions_migration_test"))
    db.execute(text("CREATE TABLE source_item (id bigint PRIMARY KEY)"))
    db.execute(
        text(
            "CREATE TABLE agent_triggers (id int PRIMARY KEY, kind text, "
            "CONSTRAINT agent_triggers_kind_check CHECK (kind IN ('ingest', 'cron')))"
        )
    )
    db.execute(
        text(
            "CREATE TABLE agent_trigger_matches (trigger_id int, item_id bigint, "
            f"CONSTRAINT {fkey} FOREIGN KEY (item_id) REFERENCES source_item(id) ON DELETE CASCADE)"
        )
    )
    db.execute(text("INSERT INTO source_item VALUES (1), (2)"))
    db.execute(text("INSERT INTO agent_trigger_matches VALUES (1, 1), (1, 2)"))

    # The concurrent index can't be built in a test's transaction; head has it.
    migrate(db, module.take_raw_triggers)
    migrate(db, module.take_raw_triggers)
    db.execute(text("INSERT INTO agent_triggers VALUES (1, 'raw'), (2, 'ingest')"))
    db.execute(text("DELETE FROM source_item WHERE id = 1"))
    upgraded = db.execute(text("SELECT item_id FROM agent_trigger_matches ORDER BY item_id")).scalars().all()
    migrate(db, module.refuse_raw_triggers)
    downgraded = db.execute(text("SELECT item_id FROM agent_trigger_matches")).scalars().all()
    kinds = db.execute(text("SELECT kind FROM agent_triggers")).scalars().all()
    db.execute(text("DELETE FROM source_item WHERE id = 2"))
    cascaded = db.execute(text("SELECT count(*) FROM agent_trigger_matches")).scalar_one()
    db_session.rollback()

    assert (upgraded, downgraded, kinds, cascaded) == ([1, 2], [2], ["ingest"], 0)
