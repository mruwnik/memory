"""Scheduled Claude sessions lose their per-schedule tokens when secrets go."""
import importlib.util
import json
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def test_drop_secrets_moves_schedules_onto_saved_tokens(db_session):
    path = Path(__file__).parents[4] / "db/migrations/versions/20261114_drop_secrets.py"
    spec = importlib.util.spec_from_file_location("drop_secrets_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    db = db_session.connection()
    db.execute(text("CREATE SCHEMA drop_secrets_migration_test"))
    db.execute(text("SET LOCAL search_path TO drop_secrets_migration_test"))
    db.execute(text("CREATE TABLE users (id integer PRIMARY KEY)"))
    db.execute(text("CREATE TABLE secrets (id integer PRIMARY KEY, name varchar)"))
    db.execute(text("CREATE INDEX ix_secrets_name ON secrets (name)"))
    db.execute(text("CREATE TABLE scheduled_tasks (id integer PRIMARY KEY, task_type varchar, data json)"))
    rows = [
        (1, "claude_session", {"spawn_config": {"repo_url": "r", "github_token": "clone-ref", "github_token_write": "write-ref"}}),
        (2, "claude_session", {"spawn_config": {"github_token": "clone-ref"}}),
        (3, "claude_session", {"spawn_config": {"repo_url": "r"}}),
        (4, "agent_run", {"spawn_config": {"github_token": "kept"}}),
        (5, "notification", None),
        (6, "claude_session", {"spawn_config": {"github_token_write": None}}),
        (7, "claude_session", {"spawn_config": {"github_token_write": ""}}),
    ]
    for task_id, task_type, data in rows:
        db.execute(
            text("INSERT INTO scheduled_tasks VALUES (:id, :t, :d)"),
            {"id": task_id, "t": task_type, "d": json.dumps(data) if data else None},
        )

    with Operations.context(MigrationContext.configure(db)):
        module.upgrade()

    assert db.execute(text("SELECT id, data::jsonb FROM scheduled_tasks ORDER BY id")).all() == [
        (1, {"spawn_config": {"repo_url": "r", "use_saved_write_token": True}}),
        (2, {"spawn_config": {}}),
        (3, {"spawn_config": {"repo_url": "r"}}),
        (4, {"spawn_config": {"github_token": "kept"}}),
        (5, None),
        (6, {"spawn_config": {}}),
        (7, {"spawn_config": {}}),
    ]
    assert db.execute(text("SELECT to_regclass('drop_secrets_migration_test.secrets')")).scalar() is None
    with Operations.context(MigrationContext.configure(db)):
        module.downgrade()
    assert db.execute(text("SELECT count(*) FROM secrets")).scalar() == 0
    db_session.rollback()
