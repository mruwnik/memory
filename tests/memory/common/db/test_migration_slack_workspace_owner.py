"""Each Slack workspace's owner is stored as the user of its oldest credential."""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

MIGRATION = Path(__file__).parents[4] / "db/migrations/versions/20261226_slack_workspace_owner.py"


def test_slack_workspace_owner_backfills_the_oldest_credential(db_session):
    spec = importlib.util.spec_from_file_location("slack_workspace_owner_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    db = db_session.connection()
    db.execute(text("CREATE SCHEMA slack_workspace_owner_test"))
    db.execute(text("SET LOCAL search_path TO slack_workspace_owner_test"))
    db.execute(text("CREATE TABLE users (id integer PRIMARY KEY)"))
    db.execute(text("CREATE TABLE slack_workspaces (id text PRIMARY KEY)"))
    db.execute(text("CREATE TABLE slack_user_credentials (id integer PRIMARY KEY, workspace_id text, user_id integer)"))
    db.execute(text("INSERT INTO users VALUES (1), (2)"))
    db.execute(text("INSERT INTO slack_workspaces VALUES ('T1'), ('T2')"))
    db.execute(text("INSERT INTO slack_user_credentials VALUES (5, 'T1', 2), (7, 'T1', 1)"))

    with Operations.context(MigrationContext.configure(db)):
        module.upgrade()
    owners = db.execute(text("SELECT id, owner_id FROM slack_workspaces ORDER BY id")).all()
    db.execute(text("DELETE FROM users WHERE id = 2"))
    after_delete = db.execute(text("SELECT owner_id FROM slack_workspaces WHERE id = 'T1'")).scalar()
    with Operations.context(MigrationContext.configure(db)):
        module.downgrade()
    columns = db.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'slack_workspace_owner_test' AND table_name = 'slack_workspaces'"
        )
    ).scalars().all()
    db_session.rollback()

    assert owners == [("T1", 2), ("T2", None)]
    assert after_delete is None
    assert columns == ["id"]
