"""Upgrading renames triggers to subscriptions and matches to deliveries, with the
names of their indexes, constraints and sequence whatever an older instance
called them, and limits a delivery's status to the recorded reasons; downgrading
restores the old names."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

SCHEMA = "subscriptions_contract_migration_test"


def load_migration():
    path = Path(__file__).parents[4] / "db/migrations/versions/20261217_subscriptions_contract.py"
    spec = importlib.util.spec_from_file_location("subscriptions_contract_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migrate(db, step):
    with Operations.context(MigrationContext.configure(db)):
        step()


def names(db) -> set[str]:
    """Every table, column, index, constraint and sequence name in the test schema."""
    query = (
        "SELECT relname FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace "
        "WHERE nspname = :schema "
        "UNION SELECT conname FROM pg_constraint c JOIN pg_namespace s ON s.oid = c.connamespace "
        "WHERE nspname = :schema "
        "UNION SELECT table_name || '.' || column_name FROM information_schema.columns "
        "WHERE table_schema = :schema"
    )
    return set(db.execute(text(query), {"schema": SCHEMA}).scalars())


def build(db, run_fkey: str):
    db.execute(text(f"CREATE SCHEMA {SCHEMA}"))
    db.execute(text(f"SET LOCAL search_path TO {SCHEMA}"))
    db.execute(
        text(
            "CREATE TABLE agent_triggers (id serial PRIMARY KEY, user_id int NOT NULL, name text NOT NULL, "
            "CONSTRAINT agent_triggers_user_name_key UNIQUE (user_id, name))"
        )
    )
    db.execute(
        text(
            "CREATE TABLE agent_runs (id text PRIMARY KEY, dedupe_key text, trigger_id int, "
            f"CONSTRAINT {run_fkey} FOREIGN KEY (trigger_id) REFERENCES agent_triggers(id) ON DELETE SET NULL)"
        )
    )
    db.execute(
        text(
            "CREATE INDEX agent_runs_trigger_kind_idx ON agent_runs (dedupe_key)"
        )
    )
    db.execute(
        text(
            "CREATE UNIQUE INDEX agent_runs_trigger_dedupe_key ON agent_runs (trigger_id, dedupe_key) "
            "WHERE dedupe_key IS NOT NULL AND trigger_id IS NOT NULL"
        )
    )
    db.execute(
        text(
            "CREATE TABLE agent_trigger_matches (trigger_id int REFERENCES agent_triggers(id) ON DELETE CASCADE, "
            "item_id bigint, status text NOT NULL, PRIMARY KEY (trigger_id, item_id), "
            "CONSTRAINT agent_trigger_matches_status_check CHECK "
            "(status IN ('pending', 'done', 'failed', 'expired') OR status LIKE 'skipped:%'))"
        )
    )
    db.execute(
        text(
            "CREATE INDEX agent_trigger_matches_trigger_created_idx ON agent_trigger_matches (trigger_id)"
        )
    )
    db.execute(
        text("CREATE TABLE events (id bigserial PRIMARY KEY, trigger_id int REFERENCES agent_triggers(id))")
    )
    db.execute(
        text(
            "INSERT INTO agent_triggers (id, user_id, name) VALUES "
            "(1, 1, 'trigger-1'), (2, 1, 'schedule-7'), (3, 1, 'inbox'), (4, 2, 'trigger-40')"
        )
    )
    db.execute(text("SELECT setval('agent_triggers_id_seq', 4)"))
    db.execute(text("INSERT INTO agent_runs VALUES ('r', 'k', 1)"))
    db.execute(text("INSERT INTO agent_trigger_matches VALUES (1, 10, 'skipped:shed:rate')"))
    db.execute(text("INSERT INTO events (trigger_id) VALUES (2)"))


@pytest.mark.parametrize(
    "run_fkey, renamed_fkey",
    [
        ("agent_runs_trigger_id_fkey", "agent_runs_subscription_id_fkey"),
        ("fk_runs_trigger", "fk_runs_trigger"),
    ],
)
def test_upgrade_renames_by_lookup_and_downgrade_restores(db_session, run_fkey, renamed_fkey):
    module = load_migration()
    db = db_session.connection()
    build(db, run_fkey)
    before = names(db)

    migrate(db, module.rename)
    migrate(db, module.rename)
    migrate(db, module.validate)
    upgraded = names(db)
    subscriptions = db.execute(text("SELECT id, name FROM subscriptions ORDER BY id")).all()
    next_id = db.execute(text("INSERT INTO subscriptions (user_id, name) VALUES (3, 'x') RETURNING id")).scalar()
    joined = db.execute(
        text(
            "SELECT r.id, d.item_id, e.subscription_id FROM agent_runs r "
            "JOIN deliveries d ON d.subscription_id = r.subscription_id "
            "JOIN events e ON e.subscription_id = 2"
        )
    ).all()
    migrate(db, module.unrename)
    downgraded = names(db)
    db_session.rollback()

    assert {
        "subscriptions",
        "subscriptions_pkey",
        "subscriptions_id_seq",
        "subscriptions_user_name_key",
        "agent_runs.subscription_id",
        "agent_runs_subscription_dedupe_key",
        renamed_fkey,
        "deliveries",
        "deliveries.subscription_id",
        "deliveries_pkey",
        "deliveries_subscription_id_fkey",
        "deliveries_subscription_created_idx",
        "deliveries_status_check",
        "events.subscription_id",
        "events_subscription_id_fkey",
    } <= upgraded
    assert not {n for n in upgraded if "trigger" in n} - {renamed_fkey, "agent_runs_trigger_kind_idx"}
    assert "agent_runs_trigger_kind_idx" in upgraded
    assert subscriptions == [(1, "subscription-1"), (2, "subscription-2"), (3, "inbox"), (4, "subscription-4")]
    assert next_id == 5
    assert joined == [("r", 10, 2)]
    assert downgraded == before


def upgraded(db_session):
    module = load_migration()
    db = db_session.connection()
    build(db, "agent_runs_trigger_id_fkey")
    migrate(db, module.rename)
    migrate(db, module.validate)
    return db


@pytest.mark.parametrize("status", ["pending", "done", "skipped:shed:backlog", "skipped:coalesced"])
def test_a_delivery_takes_a_recorded_status(db_session, status):
    db = upgraded(db_session)
    db.execute(text("INSERT INTO deliveries VALUES (1, 11, :status)"), {"status": status})
    count = db.execute(text("SELECT count(*) FROM deliveries")).scalar()
    db_session.rollback()

    assert count == 2


@pytest.mark.parametrize("status", ["skipped:late", "skipped:", "running"])
def test_a_delivery_refuses_any_other_status(db_session, status):
    db = upgraded(db_session)

    with pytest.raises(IntegrityError):
        db.execute(text("INSERT INTO deliveries VALUES (1, 11, :status)"), {"status": status})
    db_session.rollback()
