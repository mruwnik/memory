"""Upgrading renames the hidden `scheduler` MCP server to `subscription`."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def load_migration():
    path = Path(__file__).parents[4] / "db/migrations/versions/20261214_subscription_server.py"
    spec = importlib.util.spec_from_file_location("subscription_server_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migrate(db, step):
    with Operations.context(MigrationContext.configure(db)):
        step()


@pytest.mark.parametrize(
    "stored, upgraded",
    [
        ('["scheduler", "books"]', [["books", "subscription"]]),
        ('["scheduler", "subscription"]', [["subscription"]]),
        ('["books"]', [["books"]]),
        ("[]", [[]]),
        (None, []),
    ],
)
def test_upgrade_renames_the_hidden_scheduler_server(db_session, stored, upgraded):
    module = load_migration()
    db = db_session.connection()
    db.execute(text("CREATE SCHEMA subscription_server_migration_test"))
    db.execute(text("SET LOCAL search_path TO subscription_server_migration_test"))
    db.execute(text("CREATE TABLE instance_settings (key text PRIMARY KEY, value jsonb)"))
    db.execute(text("INSERT INTO instance_settings VALUES ('other', '[\"scheduler\"]')"))
    db.execute(
        text("INSERT INTO instance_settings SELECT 'disabled_mcp_servers', CAST(:v AS jsonb) WHERE :v IS NOT NULL"),
        {"v": stored},
    )

    migrate(db, module.upgrade)
    migrate(db, module.upgrade)
    rows = db.execute(
        text("SELECT value FROM instance_settings WHERE key = 'disabled_mcp_servers'")
    ).scalars().all()
    other = db.execute(text("SELECT value FROM instance_settings WHERE key = 'other'")).scalar_one()
    db_session.rollback()

    assert ([sorted(r) for r in rows], other) == (upgraded, ["scheduler"])
