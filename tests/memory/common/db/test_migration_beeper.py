"""20261130_beeper goes down and back up."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

TABLES = {"beeper_accounts", "beeper_networks", "beeper_chats", "beeper_participants", "beeper_message"}


def load_migration():
    path = Path(__file__).parents[4] / "db/migrations/versions/20261130_beeper.py"
    spec = importlib.util.spec_from_file_location("beeper_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def beeper_tables(db) -> set[str]:
    return set(db.scalars(text("SELECT tablename FROM pg_tables WHERE tablename LIKE 'beeper%'")))


def test_downgrade_drops_the_tables_and_upgrade_restores_them(db_session):
    module = load_migration()
    db = db_session.connection()

    with Operations.context(MigrationContext.configure(db)):
        module.downgrade()
    assert beeper_tables(db) == set()

    with Operations.context(MigrationContext.configure(db)):
        module.upgrade()
    assert beeper_tables(db) == TABLES
