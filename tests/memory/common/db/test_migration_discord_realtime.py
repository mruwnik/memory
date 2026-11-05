"""Discord bots get a real-time mode from their public key; tokenless ones pause."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

# (id, public_key, has token) -> (realtime, is_active)
BOTS = {
    1: (None, True, ("gateway", True)),
    2: ("ab" * 32, True, ("webhook", True)),
    3: ("ab" * 32, False, ("webhook", False)),
}


def load_migration():
    path = Path(__file__).parents[4] / "db/migrations/versions/20261127_discord_realtime.py"
    spec = importlib.util.spec_from_file_location("discord_realtime_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bots_keep_their_delivery_and_tokenless_ones_pause(db_session):
    module = load_migration()
    db = db_session.connection()
    db.execute(text("CREATE SCHEMA discord_realtime_migration_test"))
    db.execute(text("SET LOCAL search_path TO discord_realtime_migration_test"))
    db.execute(text(
        "CREATE TABLE discord_bots (id integer PRIMARY KEY, public_key text, "
        "token_encrypted bytea, is_active boolean NOT NULL)"
    ))
    for bot_id, (public_key, has_token, _) in BOTS.items():
        db.execute(
            text("INSERT INTO discord_bots VALUES (:id, :key, :token, true)"),
            {"id": bot_id, "key": public_key, "token": b"t" if has_token else None},
        )

    with Operations.context(MigrationContext.configure(db)):
        module.upgrade()

    rows = db.execute(text("SELECT id, realtime, is_active FROM discord_bots ORDER BY id"))
    assert {row[0]: (row[1], row[2]) for row in rows} == {i: expected for i, (_, _, expected) in BOTS.items()}

    with Operations.context(MigrationContext.configure(db)):
        module.downgrade()

    columns = db.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'discord_realtime_migration_test' AND table_name = 'discord_bots'"
    ))
    assert "realtime" not in {row[0] for row in columns}
