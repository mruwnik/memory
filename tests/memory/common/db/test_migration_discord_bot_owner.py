"""Bot ownership survives removal of the user-sharing table."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def test_drop_bot_sharing_preserves_owners_and_backfills_legacy_bots(db_session):
    path = Path(__file__).parents[4] / "db/migrations/versions/20261110_drop_discord_bot_users.py"
    spec = importlib.util.spec_from_file_location("discord_owner_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    db = db_session.connection()
    db.execute(text("CREATE SCHEMA discord_owner_migration_test"))
    db.execute(text("SET LOCAL search_path TO discord_owner_migration_test"))
    db.execute(text("CREATE TABLE users (id integer PRIMARY KEY)"))
    db.execute(text("CREATE TABLE discord_bots (id bigint PRIMARY KEY, created_by_user_id integer)"))
    db.execute(text("CREATE TABLE discord_bot_users (bot_id bigint, user_id integer)"))
    db.execute(text("INSERT INTO users VALUES (1), (2)"))
    db.execute(text("INSERT INTO discord_bots VALUES (11, 2), (12, NULL), (13, NULL)"))
    db.execute(text("INSERT INTO discord_bot_users VALUES (11, 1), (11, 2), (12, 1)"))
    with Operations.context(MigrationContext.configure(db)):
        module.upgrade()
    assert db.execute(text("SELECT id, created_by_user_id FROM discord_bots ORDER BY id")).all() == [(11, 2), (12, 1), (13, None)]
    assert db.execute(text("SELECT to_regclass('discord_owner_migration_test.discord_bot_users')")).scalar() is None
    with Operations.context(MigrationContext.configure(db)):
        module.downgrade()
    assert db.execute(text("SELECT bot_id, user_id FROM discord_bot_users ORDER BY bot_id")).all() == [(11, 2), (12, 1)]
    db_session.rollback()
