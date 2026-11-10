"""Upgrading keeps every transcript account making tasks for all action items."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def load_migration():
    path = Path(__file__).parents[4] / "db/migrations/versions/20261223_transcript_task_options.py"
    spec = importlib.util.spec_from_file_location("transcript_task_options_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_defaults_existing_accounts_to_tasks_for_everyone_and_downgrades(db_session):
    module = load_migration()
    db = db_session.connection()
    db.execute(text("CREATE SCHEMA transcript_task_options_migration_test"))
    db.execute(text("SET LOCAL search_path TO transcript_task_options_migration_test"))
    db.execute(text("CREATE TABLE transcript_accounts (id bigint PRIMARY KEY)"))
    db.execute(text("CREATE TABLE people (id bigint PRIMARY KEY)"))
    db.execute(text("INSERT INTO transcript_accounts VALUES (1)"))
    db.execute(text("INSERT INTO people VALUES (7)"))

    with Operations.context(MigrationContext.configure(db)):
        module.upgrade()
        db.execute(text("INSERT INTO transcript_account_task_people VALUES (1, 7)"))
        db.execute(text("DELETE FROM people"))
        upgraded = db.execute(text("SELECT id, create_tasks FROM transcript_accounts")).all()
        links = db.execute(text("SELECT count(*) FROM transcript_account_task_people")).scalar()
        module.downgrade()
    columns = db.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'transcript_task_options_migration_test' "
            "AND table_name = 'transcript_accounts'"
        )
    ).scalars().all()
    db_session.rollback()

    assert (upgraded, links, columns) == ([(1, True)], 0, ["id"])
