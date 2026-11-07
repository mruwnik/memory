"""Upgrading records the finished check rollup of PRs whose stored checks have all finished."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def load_migration():
    path = Path(__file__).parents[4] / "db/migrations/versions/20261212_github_pr_draft.py"
    spec = importlib.util.spec_from_file_location("github_pr_draft_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migrate(db, step):
    with Operations.context(MigrationContext.configure(db)):
        step()


@pytest.mark.parametrize(
    "head_sha, checks, key",
    [
        ("a", """'{"tests": "success", "lint": "skipped"}'""", "a:SUCCESS"),
        ("a", """'{"tests": "success", "lint": "failure"}'""", "a:FAILURE"),
        ("a", """'{"ci/legacy": "error"}'""", "a:ERROR"),
        ("a", """'{"tests": "success", "lint": "in_progress"}'""", None),
        ("a", "'{}'", None),
        ("a", "NULL", None),
        ("a", "'null'", None),
        (None, """'{"tests": "success"}'""", None),
    ],
)
def test_upgrade_backfills_finished_check_rollups(db_session, head_sha, checks, key):
    module = load_migration()
    db = db_session.connection()
    db.execute(text("CREATE SCHEMA github_pr_draft_migration_test"))
    db.execute(text("SET LOCAL search_path TO github_pr_draft_migration_test"))
    db.execute(text("CREATE TABLE github_item (id int PRIMARY KEY, head_sha text, checks jsonb)"))
    db.execute(text(f"INSERT INTO github_item VALUES (1, :sha, {checks}::jsonb)"), {"sha": head_sha})

    migrate(db, module.upgrade)
    upgraded = db.execute(text("SELECT draft, checks_complete_key FROM github_item")).one()
    migrate(db, module.downgrade)
    columns = db.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'github_pr_draft_migration_test' ORDER BY column_name"
        )
    ).scalars().all()
    db_session.rollback()

    assert tuple(upgraded) == (None, key)
    assert columns == ["checks", "head_sha", "id"]
