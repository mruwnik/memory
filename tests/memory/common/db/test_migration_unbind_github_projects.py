"""Projects bound to a repo whose tracker shares none of their teams are unbound,
and the repo's items filed into them lose that project."""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

MIGRATION = Path(__file__).parents[4] / "db/migrations/versions/20261227_unbind_github_projects.py"


def load_migration():
    spec = importlib.util.spec_from_file_location("unbind_github_projects_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_schema(db) -> None:
    db.execute(text("CREATE SCHEMA unbind_github_projects_test"))
    db.execute(text("SET LOCAL search_path TO unbind_github_projects_test"))
    db.execute(text("CREATE TABLE github_accounts (id bigint PRIMARY KEY, user_id integer)"))
    db.execute(text("CREATE TABLE github_repos (id bigint PRIMARY KEY, account_id bigint)"))
    db.execute(text("CREATE TABLE people (id bigint PRIMARY KEY, user_id integer)"))
    db.execute(text("CREATE TABLE team_members (team_id bigint, person_id bigint)"))
    db.execute(text("CREATE TABLE project_teams (project_id bigint, team_id bigint)"))
    db.execute(
        text("CREATE TABLE projects (id bigint PRIMARY KEY, repo_id bigint, github_id bigint, number integer)")
    )
    db.execute(
        text("CREATE TABLE source_item (id bigint PRIMARY KEY, project_id bigint, updated_at timestamptz)")
    )
    db.execute(text("CREATE TABLE github_item (id bigint PRIMARY KEY, repo_id bigint, milestone_id bigint)"))


# User 1 tracks repo 10 and is in team 100; user 2 tracks repo 20 and has no Person.
# Project 1: repo 10, team 100       -> kept (tracker in a team)
# Project 2: repo 10, team 200       -> unbound (tracker in none)
# Project 3: repo 10, milestone 4, teams 200 + 100 -> kept
# Project 4: repo 10, milestone 5, team 200 -> unbound
# Project 5: repo 10, no teams       -> kept (grants no one)
# Project 6: repo 20, team 100       -> unbound (tracker has no Person)
# Project 7: standalone, team 200    -> untouched
SEED = [
    "INSERT INTO github_accounts VALUES (1, 1), (2, 2)",
    "INSERT INTO github_repos VALUES (10, 1), (20, 2)",
    "INSERT INTO people VALUES (50, 1)",
    "INSERT INTO team_members VALUES (100, 50)",
    "INSERT INTO projects VALUES (1, 10, NULL, NULL), (2, 10, NULL, NULL), (3, 10, 33, 4), "
    "(4, 10, 44, 5), (5, 10, NULL, NULL), (6, 20, NULL, NULL), (7, NULL, NULL, NULL)",
    "INSERT INTO project_teams VALUES (1, 100), (2, 200), (3, 200), (3, 100), (4, 200), (6, 100), (7, 200)",
    # Items 1-6: one per bound project; item 7 is a note in project 2; item 8 is repo 20's in project 2.
    "INSERT INTO source_item VALUES (1, 1, NULL), (2, 2, NULL), (3, 3, NULL), (4, 4, NULL), "
    "(5, 5, NULL), (6, 6, NULL), (7, 2, NULL), (8, 2, NULL)",
    "INSERT INTO github_item VALUES (1, 10, NULL), (2, 10, NULL), (3, 10, 3), (4, 10, 4), "
    "(5, 10, NULL), (6, 20, NULL), (8, 20, NULL)",
]


def test_unbinds_projects_the_tracker_shares_no_team_of(db_session):
    module = load_migration()
    db = db_session.connection()
    make_schema(db)
    for statement in SEED:
        db.execute(text(statement))

    with Operations.context(MigrationContext.configure(db)):
        module.upgrade()

    projects = db.execute(text("SELECT id, repo_id, github_id, number FROM projects ORDER BY id")).all()
    items = db.execute(text("SELECT id, project_id, updated_at IS NOT NULL FROM source_item ORDER BY id")).all()
    milestones = db.execute(text("SELECT id, milestone_id FROM github_item ORDER BY id")).all()
    db_session.rollback()
    assert projects == [
        (1, 10, None, None),
        (2, None, None, None),
        (3, 10, 33, 4),
        (4, None, None, None),
        (5, 10, None, None),
        (6, None, None, None),
        (7, None, None, None),
    ]
    assert items == [
        (1, 1, False),
        (2, None, True),
        (3, 3, False),
        (4, None, True),
        (5, 5, False),
        (6, None, True),
        (7, 2, False),
        (8, 2, False),
    ]
    assert milestones == [(1, None), (2, None), (3, 3), (4, None), (5, None), (6, None), (8, None)]
