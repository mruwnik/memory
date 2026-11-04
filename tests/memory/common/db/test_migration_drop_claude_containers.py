import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

SCHEMA = "drop_claude_containers_test"
BASE_DDL = [
    "CREATE TABLE ssh_keys (id integer PRIMARY KEY)",
    "CREATE TABLE users (id integer PRIMARY KEY, scopes varchar[] NOT NULL)",
    "CREATE TABLE api_keys (id integer PRIMARY KEY, scopes varchar[])",
    "CREATE TABLE instance_settings (key varchar PRIMARY KEY, value json)",
    "CREATE TABLE scheduled_tasks (id varchar PRIMARY KEY, task_type varchar)",
]
RUNNER_TABLES = [
    "CREATE TABLE claude_config_snapshots (id integer PRIMARY KEY)",
    "CREATE TABLE claude_environments (id integer PRIMARY KEY, "
    "snapshot_id integer REFERENCES claude_config_snapshots(id))",
    "CREATE TABLE claude_github_tokens (id integer PRIMARY KEY)",
]
DEFAULT_KEY_COLUMN = [
    "ALTER TABLE users ADD COLUMN default_ssh_key_id integer "
    "CONSTRAINT users_default_ssh_key_fk REFERENCES ssh_keys(id) ON DELETE SET NULL"
]
ROWS = [
    "INSERT INTO users (id, scopes) VALUES "
    "(1, ARRAY['read', 'claude_sessions']), (2, ARRAY['claude_sessions']), (3, ARRAY['*'])",
    "INSERT INTO api_keys VALUES "
    "(1, ARRAY['claude_sessions', 'schedule:write']), (2, NULL), (3, ARRAY['read'])",
    "INSERT INTO instance_settings VALUES "
    "('module_claude_containers', 'true'), ('module_teams', 'true')",
    "INSERT INTO scheduled_tasks VALUES "
    "('s', 'claude_session'), ('n', 'notification'), ('a', 'agent_run')",
]


def load_migration():
    path = Path(__file__).parents[4] / "db/migrations/versions/20261124_drop_claude_containers.py"
    spec = importlib.util.spec_from_file_location("drop_claude_containers_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def schema_state(db) -> tuple[set[str], set[str]]:
    tables = db.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname = :s"), {"s": SCHEMA}
    ).scalars()
    user_columns = db.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :s AND table_name = 'users'"
        ),
        {"s": SCHEMA},
    ).scalars()
    return set(tables), set(user_columns)


# Instances that drifted from the schema may lack any of the runner's objects.
@pytest.mark.parametrize(
    "runner_ddl",
    [RUNNER_TABLES, [RUNNER_TABLES[0]], [RUNNER_TABLES[2]], []],
    ids=["all-tables", "snapshots-only", "tokens-only", "no-tables"],
)
@pytest.mark.parametrize("column_ddl", [DEFAULT_KEY_COLUMN, []], ids=["column", "no-column"])
def test_drop_claude_containers(db_session, runner_ddl, column_ddl):
    module = load_migration()
    db = db_session.connection()
    db.execute(text(f"CREATE SCHEMA {SCHEMA}"))
    db.execute(text(f"SET LOCAL search_path TO {SCHEMA}"))
    for sql in [*BASE_DDL, *column_ddl, *runner_ddl, *ROWS]:
        db.execute(text(sql))

    with Operations.context(MigrationContext.configure(db)):
        module.upgrade()

    expected = (
        {"ssh_keys", "users", "api_keys", "instance_settings", "scheduled_tasks"},
        {"id", "scopes"},
    )
    assert schema_state(db) == expected
    assert db.execute(text("SELECT id FROM scheduled_tasks ORDER BY id")).scalars().all() == ["a", "n"]
    assert db.execute(text("SELECT id, scopes FROM users ORDER BY id")).all() == [
        (1, ["read"]),
        (2, []),
        (3, ["*"]),
    ]
    assert db.execute(text("SELECT id, scopes FROM api_keys ORDER BY id")).all() == [
        (1, ["schedule:write"]),
        (2, None),
        (3, ["read"]),
    ]
    assert db.execute(text("SELECT key FROM instance_settings")).scalars().all() == ["module_teams"]

    with Operations.context(MigrationContext.configure(db)):
        module.downgrade()
    assert schema_state(db) == expected
    db_session.rollback()
