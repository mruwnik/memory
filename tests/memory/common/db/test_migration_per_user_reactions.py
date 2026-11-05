"""Stored reaction lists become per-user maps."""
import importlib.util
import json
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

SLACK = [
    (1, [{"name": "fire", "count": 2, "users": ["U1", "U2"]}, {"name": "ok", "count": 1, "users": ["U3"]}]),
    (2, []),
    (3, None),
]
DISCORD = [
    (1, [{"emoji": "👍", "count": 5}, {"emoji": "🎉", "count": 1, "users": [123]}]),
    (2, [{"emoji": "👍", "count": 5}]),
    (3, None),
]


def load_migration():
    path = Path(__file__).parents[4] / "db/migrations/versions/20261126_per_user_reactions.py"
    spec = importlib.util.spec_from_file_location("per_user_reactions_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reactions(db, table: str) -> list:
    return [row[0] for row in db.execute(text(f"SELECT reactions FROM {table} ORDER BY id"))]


def test_reactions_become_per_user_maps(db_session):
    module = load_migration()
    db = db_session.connection()
    db.execute(text("CREATE SCHEMA reactions_migration_test"))
    db.execute(text("SET LOCAL search_path TO reactions_migration_test"))
    for table, rows in (("slack_message", SLACK), ("discord_message", DISCORD)):
        db.execute(text(f"CREATE TABLE {table} (id integer PRIMARY KEY, reactions jsonb)"))
        for row_id, value in rows:
            db.execute(
                text(f"INSERT INTO {table} VALUES (:id, CAST(:r AS jsonb))"),
                {"id": row_id, "r": None if value is None else json.dumps(value)},
            )

    with Operations.context(MigrationContext.configure(db)):
        module.upgrade()

    assert reactions(db, "slack_message") == [{"fire": ["U1", "U2"], "ok": ["U3"]}, {}, None]
    assert reactions(db, "discord_message") == [{"🎉": ["123"]}, {}, None]

    with Operations.context(MigrationContext.configure(db)):
        module.downgrade()

    first, *rest = reactions(db, "slack_message")
    assert sorted(first, key=lambda r: r["name"]) == [
        {"name": "fire", "users": ["U1", "U2"], "count": 2},
        {"name": "ok", "users": ["U3"], "count": 1},
    ]
    assert rest == [[], None]
    db_session.rollback()
