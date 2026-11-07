"""Upgrading gives GitHub PRs and issues their own source types, and widens triggers that named github_item."""
import importlib.util
import json
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def load_migration():
    path = Path(__file__).parents[4] / "db/migrations/versions/20261207_github_pr_issue_types.py"
    spec = importlib.util.spec_from_file_location("github_pr_issue_types_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(db, step):
    with Operations.context(MigrationContext.configure(db)):
        step()
    types = db.execute(text("SELECT id, type FROM source_item ORDER BY id")).all()
    filters = db.execute(
        text("SELECT id, config->'filter'->'source_types' FROM agent_triggers ORDER BY id")
    ).all()
    return types, filters


def test_upgrade_retypes_prs_and_issues_and_downgrade_reverts(db_session):
    module = load_migration()
    db = db_session.connection()
    db.execute(text("CREATE SCHEMA github_pr_issue_types_migration_test"))
    db.execute(text("SET LOCAL search_path TO github_pr_issue_types_migration_test"))
    db.execute(text("CREATE TABLE source_item (id bigint PRIMARY KEY, type text)"))
    db.execute(text("CREATE TABLE github_item (id bigint PRIMARY KEY, kind text NOT NULL)"))
    db.execute(text("CREATE TABLE agent_triggers (id int PRIMARY KEY, config jsonb NOT NULL)"))
    db.execute(
        text(
            "INSERT INTO source_item VALUES "
            "(1, 'github_item'), (2, 'github_item'), (3, 'github_item'), (4, 'mail_message')"
        )
    )
    db.execute(text("INSERT INTO github_item VALUES (1, 'pr'), (2, 'issue'), (3, 'comment')"))
    configs = [
        {"filter": {"source_types": ["github_item", "mail_message"]}},
        {"filter": {"source_types": ["slack_message"]}},
        {"filter": {"repos": ["o/r"]}},
    ]
    for i, config in enumerate(configs, 1):
        db.execute(
            text("INSERT INTO agent_triggers VALUES (:id, CAST(:config AS jsonb))"),
            {"id": i, "config": json.dumps(config)},
        )

    upgraded = run(db, module.upgrade)
    downgraded = run(db, module.downgrade)
    db_session.rollback()

    assert upgraded == (
        [(1, "github_pr"), (2, "github_issue"), (3, "github_item"), (4, "mail_message")],
        [
            (1, ["github_issue", "github_item", "github_pr", "mail_message"]),
            (2, ["slack_message"]),
            (3, None),
        ],
    )
    assert downgraded == (
        [(1, "github_item"), (2, "github_item"), (3, "github_item"), (4, "mail_message")],
        [(1, ["github_item", "mail_message"]), (2, ["slack_message"]), (3, None)],
    )
