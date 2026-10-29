"""The migration's trigger lock_timeout must not outlive the trigger DDL: the
concurrent index build that follows waits on older transactions and would fail."""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

SCHEMA = "chunk_search_vector_migration_test"


def load_migration():
    path = Path(__file__).parents[4] / "db/migrations/versions/20261123_chunk_search_vector.py"
    spec = importlib.util.spec_from_file_location("chunk_search_vector_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_does_not_leave_lock_timeout_set(db_engine):
    module = load_migration()
    with db_engine.connect() as conn:
        conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))
        conn.execute(text(f"SET search_path TO {SCHEMA}"))
        conn.execute(text("CREATE TABLE chunk (id int PRIMARY KEY, content text, search_vector tsvector)"))
        conn.commit()
        context = MigrationContext.configure(conn)
        with context.begin_transaction(), Operations.context(context):
            module.upgrade()
        lock_timeout = conn.execute(text("SHOW lock_timeout")).scalar()
        conn.execute(text(f"DROP SCHEMA {SCHEMA} CASCADE"))
        conn.commit()

    assert lock_timeout == "0"
