"""REST-submitted meeting ids move into their submitter's namespace."""
import importlib.util
import json
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

MIGRATION = Path(__file__).parents[4] / "db/migrations/versions/20261223_meeting_api_ext_ids.py"


def load_migration():
    spec = importlib.util.spec_from_file_location("meeting_ids_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_schema(db, name: str) -> None:
    db.execute(text(f"CREATE SCHEMA {name}"))
    db.execute(text(f"SET LOCAL search_path TO {name}"))
    db.execute(text("CREATE TABLE source_item (id bigint PRIMARY KEY, creator_id integer)"))
    db.execute(text("CREATE TABLE meeting (id bigint PRIMARY KEY, external_id text, transcript_account_id bigint)"))
    db.execute(
        text("CREATE TABLE pending_jobs (id bigint PRIMARY KEY, job_type text, user_id bigint, params jsonb)")
    )


def run(db, *steps) -> None:
    for step in steps:
        with Operations.context(MigrationContext.configure(db)):
            step()


def job_ids(db) -> list[tuple[int, str | None]]:
    return [tuple(row) for row in db.execute(text("SELECT id, params->>'external_id' FROM pending_jobs ORDER BY id"))]


def test_meeting_api_external_ids_namespaces_rest_submissions_only(db_session):
    module = load_migration()
    db = db_session.connection()
    make_schema(db, "meeting_ids_migration_test")
    db.execute(text("INSERT INTO source_item VALUES (1, 7), (2, 7), (3, 7), (4, NULL), (5, 7), (6, 8)"))
    db.execute(
        text(
            "INSERT INTO meeting VALUES (1, 'rest-1', NULL), (2, 'ff-1', 3), (3, 'push:3:x', NULL), "
            "(4, 'legacy', NULL), (5, NULL, NULL), (6, 'rest-1', 9)"
        )
    )
    run(db, module.upgrade)
    assert db.execute(text("SELECT id, external_id FROM meeting ORDER BY id")).all() == [
        (1, "api:7:rest-1"), (2, "ff-1"), (3, "push:3:x"), (4, "legacy"), (5, None), (6, "rest-1"),
    ]
    db.execute(text("UPDATE meeting SET external_id = 'api:8:rest-2' WHERE id = 5"))
    run(db, module.downgrade)
    assert db.execute(text("SELECT id, external_id FROM meeting ORDER BY id")).all() == [
        (1, "api:7:rest-1"), (2, "ff-1"), (3, "push:3:x"), (4, "legacy"), (5, "rest-2"), (6, "rest-1"),
    ]
    db_session.rollback()


def test_meeting_api_external_ids_upgrade_after_downgrade_keeps_one_prefix(db_session):
    module = load_migration()
    db = db_session.connection()
    make_schema(db, "meeting_ids_redo_test")
    db.execute(text("INSERT INTO source_item VALUES (1, 7), (2, 8), (3, 9)"))
    db.execute(text("INSERT INTO meeting VALUES (1, 'shared', NULL), (2, 'shared', NULL), (3, 'own', NULL)"))
    run(db, module.upgrade, module.downgrade, module.upgrade)
    assert db.execute(text("SELECT id, external_id FROM meeting ORDER BY id")).all() == [
        (1, "api:7:shared"), (2, "api:8:shared"), (3, "api:9:own"),
    ]
    db_session.rollback()


JOBS = [
    (1, "meeting", 7, {"external_id": "rest-1", "creator_id": 7}),  # POST /meetings
    (2, "meeting", 7, {"external_id": "push:3:x", "transcript_account_id": 3}),  # transcript push
    (3, "meeting", 7, {"external_id": None, "creator_id": 7}),  # no id submitted
    (4, "meeting", 8, {"external_id": "rest-1", "creator_id": 7}),  # creator isn't the job's user
    (5, "document", 7, {"external_id": "rest-1", "creator_id": 7}),  # not a meeting job
    (6, "meeting", 9, {"external_id": "api:9:rest-2", "creator_id": 9}),  # already namespaced
]


def test_meeting_api_external_ids_renames_rest_job_params(db_session):
    module = load_migration()
    db = db_session.connection()
    make_schema(db, "meeting_ids_jobs_test")
    for job_id, job_type, user_id, params in JOBS:
        db.execute(
            text("INSERT INTO pending_jobs VALUES (:id, :type, :user, CAST(:params AS jsonb))"),
            {"id": job_id, "type": job_type, "user": user_id, "params": json.dumps(params)},
        )
    upgraded = [(1, "api:7:rest-1"), (2, "push:3:x"), (3, None), (4, "rest-1"), (5, "rest-1"), (6, "api:9:rest-2")]

    run(db, module.upgrade)
    assert job_ids(db) == upgraded
    run(db, module.downgrade)
    assert job_ids(db) == [(1, "rest-1"), (2, "push:3:x"), (3, None), (4, "rest-1"), (5, "rest-1"), (6, "rest-2")]
    run(db, module.upgrade)
    assert job_ids(db) == upgraded
    db_session.rollback()
