"""DM messages another owner's bot pushed stop being that bot's."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def test_dm_messages_keep_only_bots_of_the_dms_owner(db_session):
    path = Path(__file__).parents[4] / "db/migrations/versions/20261218_disown_dm_messages.py"
    spec = importlib.util.spec_from_file_location("disown_dm_messages_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    db = db_session.connection()
    db.execute(text("CREATE SCHEMA disown_dm_messages_test"))
    db.execute(text("SET LOCAL search_path TO disown_dm_messages_test"))
    # Only the columns the migration reads: older instances' tables carry more, under other index names.
    db.execute(text("CREATE TABLE discord_bots (id bigint PRIMARY KEY, created_by_user_id integer)"))
    db.execute(text("CREATE TABLE discord_channels (id bigint PRIMARY KEY, server_id bigint, channel_type varchar, bot_id bigint)"))
    db.execute(text("CREATE TABLE discord_message (id bigint PRIMARY KEY, channel_id bigint, bot_id bigint)"))
    db.execute(text("INSERT INTO discord_bots VALUES (11, 1), (12, 1), (13, NULL), (21, 2)"))
    db.execute(text(
        "INSERT INTO discord_channels VALUES (100, NULL, 'dm', 11), (101, NULL, 'group_dm', 13), (102, NULL, 'dm', NULL), (200, 9, 'text', 11)"
    ))
    db.execute(text(
        "INSERT INTO discord_message VALUES (1, 100, 11), (2, 100, 12), (3, 100, 21), (4, 100, 13), (5, 100, NULL),"
        " (6, 101, 13), (7, 101, 11), (8, 200, 21), (9, 102, 21)"
    ))

    with Operations.context(MigrationContext.configure(db)):
        module.upgrade()

    assert db.execute(text("SELECT id, bot_id FROM discord_message ORDER BY id")).all() == [
        (1, 11), (2, 12), (3, None), (4, None), (5, None), (6, 13), (7, None), (8, 21), (9, 21),
    ]
    db_session.rollback()
