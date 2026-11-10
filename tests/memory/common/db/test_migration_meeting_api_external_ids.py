"""REST-submitted meeting ids move into their submitter's namespace."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

MIGRATION = Path(__file__).parents[4] / "db/migrations/versions/20261223_meeting_api_ext_ids.py"


def test_meeting_api_external_ids_namespaces_rest_submissions_only(db_session):
    spec = importlib.util.spec_from_file_location("meeting_ids_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    db = db_session.connection()
    db.execute(text("CREATE SCHEMA meeting_ids_migration_test"))
    db.execute(text("SET LOCAL search_path TO meeting_ids_migration_test"))
    db.execute(text("CREATE TABLE source_item (id bigint PRIMARY KEY, creator_id integer)"))
    db.execute(text("CREATE TABLE meeting (id bigint PRIMARY KEY, external_id text, transcript_account_id bigint)"))
    db.execute(text("INSERT INTO source_item VALUES (1, 7), (2, 7), (3, 7), (4, NULL), (5, 7), (6, 8)"))
    db.execute(
        text(
            "INSERT INTO meeting VALUES (1, 'rest-1', NULL), (2, 'ff-1', 3), (3, 'push:3:x', NULL), "
            "(4, 'legacy', NULL), (5, NULL, NULL), (6, 'rest-1', 9)"
        )
    )
    with Operations.context(MigrationContext.configure(db)):
        module.upgrade()
    assert db.execute(text("SELECT id, external_id FROM meeting ORDER BY id")).all() == [
        (1, "api:7:rest-1"), (2, "ff-1"), (3, "push:3:x"), (4, "legacy"), (5, None), (6, "rest-1"),
    ]
    db.execute(text("UPDATE meeting SET external_id = 'api:8:rest-2' WHERE id = 5"))
    with Operations.context(MigrationContext.configure(db)):
        module.downgrade()
    assert db.execute(text("SELECT id, external_id FROM meeting ORDER BY id")).all() == [
        (1, "api:7:rest-1"), (2, "ff-1"), (3, "push:3:x"), (4, "legacy"), (5, "rest-2"), (6, "rest-1"),
    ]
    db_session.rollback()
