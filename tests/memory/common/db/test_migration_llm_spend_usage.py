"""Upgrading keeps existing spend with no user or model; downgrading merges buckets back
to one per key, agent and hour and drops the ones whose key is gone."""

import importlib.util
from decimal import Decimal
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def load_migration():
    path = Path(__file__).parents[4] / "db/migrations/versions/20261121_llm_spend_usage.py"
    spec = importlib.util.spec_from_file_location("llm_spend_usage_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_keeps_spend_and_downgrade_merges_it_back(db_session):
    module = load_migration()
    db = db_session.connection()
    db.execute(text("CREATE SCHEMA llm_spend_usage_migration_test"))
    db.execute(text("SET LOCAL search_path TO llm_spend_usage_migration_test"))
    db.execute(text("CREATE TABLE users (id int PRIMARY KEY)"))
    db.execute(text("CREATE TABLE llm_provider_keys (id int PRIMARY KEY)"))
    db.execute(
        text(
            "CREATE TABLE llm_spend (id bigserial PRIMARY KEY, "
            "provider_key_id int NOT NULL REFERENCES llm_provider_keys(id) ON DELETE CASCADE, "
            "agent text NOT NULL, hour timestamptz NOT NULL, "
            "cost_usd numeric(12, 6) NOT NULL DEFAULT 0, calls int NOT NULL DEFAULT 0, "
            "CONSTRAINT llm_spend_bucket UNIQUE (provider_key_id, agent, hour))"
        )
    )
    db.execute(text("INSERT INTO users VALUES (1), (2)"))
    db.execute(text("INSERT INTO llm_provider_keys VALUES (1), (2)"))
    db.execute(
        text(
            "INSERT INTO llm_spend (provider_key_id, agent, hour, cost_usd, calls) VALUES "
            "(1, 'system:hyde', '2026-10-01T10:00Z', 1, 2)"
        )
    )
    columns = "provider_key_id, user_id, model, cost_usd, calls, input_tokens"

    with Operations.context(MigrationContext.configure(db)):
        module.upgrade()
    upgraded = db.execute(text(f"SELECT {columns} FROM llm_spend")).all()
    db.execute(
        text(
            "INSERT INTO llm_spend (provider_key_id, user_id, agent, model, hour, cost_usd, calls) "
            "VALUES (1, 1, 'system:hyde', 'anthropic/m', '2026-10-01T10:00Z', 2, 1), "
            "(1, 2, 'system:hyde', 'anthropic/m', '2026-10-01T10:00Z', 3, 1), "
            "(2, 1, 'system:hyde', 'anthropic/m', '2026-10-01T10:00Z', 4, 1)"
        )
    )
    db.execute(text("DELETE FROM llm_provider_keys WHERE id = 2"))
    with Operations.context(MigrationContext.configure(db)):
        module.downgrade()
    downgraded = db.execute(text("SELECT provider_key_id, cost_usd, calls FROM llm_spend")).all()
    db_session.rollback()

    assert upgraded == [(1, None, None, Decimal("1"), 2, 0)]
    assert downgraded == [(1, Decimal("6"), 4)]
