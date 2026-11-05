"""20261125_chat_webhooks on populated tables."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

SNOWFLAKE = 123456789012345678


def load_migration():
    path = Path(__file__).parents[4] / "db/migrations/versions/20261125_chat_webhooks.py"
    spec = importlib.util.spec_from_file_location("chat_webhooks_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(db, step):
    with Operations.context(MigrationContext.configure(db)):
        step()


@pytest.fixture
def old_schema(db_session, request):
    """The tables the migration touches, as before it, in a scratch schema."""
    bot_id_type = request.param
    db = db_session.connection()
    db.execute(text("CREATE SCHEMA chat_webhooks_migration_test"))
    db.execute(text("SET LOCAL search_path TO chat_webhooks_migration_test"))
    for statement in [
        "CREATE TABLE users (id integer PRIMARY KEY)",
        "CREATE TABLE slack_apps (id bigserial PRIMARY KEY, client_id text NOT NULL, "
        "created_by_user_id integer REFERENCES users(id), "
        "CONSTRAINT legacy_client_id_key UNIQUE (client_id))",
        "CREATE TABLE slack_user_credentials (id serial PRIMARY KEY, scopes text[], "
        "slack_app_id bigint REFERENCES slack_apps(id), workspace_id text)",
        "CREATE TABLE slack_message (id serial PRIMARY KEY, workspace_id text)",
        f"CREATE TABLE discord_bots (id {bot_id_type} PRIMARY KEY, created_by_user_id integer REFERENCES users(id))",
        "CREATE TABLE discord_servers (id bigint PRIMARY KEY, bot_id bigint REFERENCES discord_bots(id))",
        "CREATE TABLE discord_channels (id bigint PRIMARY KEY, server_id bigint REFERENCES discord_servers(id))",
        "CREATE TABLE discord_message (id serial PRIMARY KEY, message_id bigint, "
        "channel_id bigint REFERENCES discord_channels(id), bot_id bigint REFERENCES discord_bots(id))",
        "INSERT INTO users VALUES (1), (2)",
        f"INSERT INTO discord_bots VALUES ({SNOWFLAKE}, 1), ({SNOWFLAKE + 1}, 2)",
        f"INSERT INTO discord_servers VALUES (5, {SNOWFLAKE}), (6, NULL)",
        "INSERT INTO discord_channels VALUES (10, 5), (11, NULL), (12, NULL)",
        f"INSERT INTO discord_message (message_id, channel_id, bot_id) VALUES "
        f"(100, 10, {SNOWFLAKE}), (102, 11, {SNOWFLAKE + 1}), (101, 11, {SNOWFLAKE})",
        "INSERT INTO slack_apps (client_id, created_by_user_id) VALUES ('c1', 1), ('c2', 2), ('c3', 2)",
        "INSERT INTO slack_user_credentials (scopes, slack_app_id, workspace_id) VALUES "
        "('{\"a:read,b:write\"}', 2, 'T1'), (NULL, 1, 'T1'), (NULL, 3, 'T2')",
        "INSERT INTO slack_message (workspace_id) VALUES ('T1'), ('T2'), ('T3')",
    ]:
        db.execute(text(statement))
    yield db
    db_session.rollback()


@pytest.mark.parametrize("old_schema", ["bigint", "bigserial"], indirect=True)
def test_upgrade_keeps_bot_ids_and_allows_shared_applications(old_schema):
    db = old_schema
    run(db, load_migration().upgrade)

    assert db.execute(text("SELECT id, application_id FROM discord_bots ORDER BY id")).all() == [
        (SNOWFLAKE, SNOWFLAKE), (SNOWFLAKE + 1, SNOWFLAKE + 1),
    ]
    assert db.execute(text("SELECT bot_id FROM discord_servers")).scalar() == SNOWFLAKE
    new_id = db.execute(
        text(f"INSERT INTO discord_bots (application_id, created_by_user_id) VALUES ({SNOWFLAKE}, 2) RETURNING id")
    ).scalar()
    assert new_id < 1 << 22
    db.execute(text("INSERT INTO slack_apps (client_id, created_by_user_id) VALUES ('c1', 2)"))
    assert db.execute(text("SELECT scopes FROM slack_user_credentials ORDER BY id")).scalar() == ["a:read", "b:write"]


@pytest.mark.parametrize("old_schema", ["bigint"], indirect=True)
def test_upgrade_backfills_ownership_from_existing_rows(old_schema):
    db = old_schema
    run(db, load_migration().upgrade)

    assert db.execute(text("SELECT id, verified FROM discord_servers ORDER BY id")).all() == [(5, True), (6, True)]
    assert db.execute(text("SELECT bot_id, server_id FROM discord_bot_servers")).all() == [(SNOWFLAKE, 5)]
    # A DM belongs to the bot of its earliest message, not of the first row stored.
    assert db.execute(text("SELECT id, bot_id FROM discord_channels ORDER BY id")).all() == [
        (10, None), (11, SNOWFLAKE), (12, None),
    ]
    # Slack history goes to the app of each workspace's earliest credential.
    assert db.execute(text("SELECT workspace_id, slack_app_id FROM slack_message ORDER BY id")).all() == [
        ("T1", 2), ("T2", 3), ("T3", None),
    ]


@pytest.mark.parametrize("old_schema", ["bigint"], indirect=True)
def test_downgrade_drops_the_ownership_columns(old_schema):
    db = old_schema
    module = load_migration()
    run(db, module.upgrade)

    run(db, module.downgrade)

    columns = db.execute(text(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'chat_webhooks_migration_test' AND column_name IN "
        "('verified', 'bot_id', 'slack_app_id', 'application_id', 'public_key')"
    )).all()
    assert sorted(columns) == [
        ("discord_message", "bot_id"),
        ("discord_servers", "bot_id"),
        ("slack_user_credentials", "slack_app_id"),
    ]
    assert db.execute(text("SELECT to_regclass('discord_bot_servers')")).scalar() is None


@pytest.mark.parametrize(
    "duplicate",
    [
        f"INSERT INTO discord_bots (application_id, created_by_user_id) VALUES ({SNOWFLAKE}, 1)",
        "INSERT INTO slack_apps (client_id, created_by_user_id) VALUES ('c1', 1)",
    ],
)
@pytest.mark.parametrize("old_schema", ["bigint"], indirect=True)
def test_upgrade_keeps_one_row_per_user(old_schema, duplicate):
    run(old_schema, load_migration().upgrade)

    with pytest.raises(IntegrityError):
        old_schema.execute(text(duplicate))


@pytest.mark.parametrize(
    "extra_row",
    [
        f"INSERT INTO discord_bots (application_id, created_by_user_id) VALUES ({SNOWFLAKE}, 2)",
        "INSERT INTO slack_apps (client_id, created_by_user_id) VALUES ('c1', 2)",
    ],
)
@pytest.mark.parametrize("old_schema", ["bigint"], indirect=True)
def test_downgrade_refuses_rows_the_old_schema_cannot_hold(old_schema, extra_row):
    module = load_migration()
    run(old_schema, module.upgrade)
    old_schema.execute(text(extra_row))

    with pytest.raises(RuntimeError, match="Cannot downgrade"):
        run(old_schema, module.downgrade)


@pytest.mark.parametrize("old_schema", ["bigint"], indirect=True)
def test_downgrade_restores_the_old_shape(old_schema):
    module = load_migration()
    run(old_schema, module.upgrade)

    run(old_schema, module.downgrade)

    with pytest.raises(IntegrityError):
        old_schema.execute(text("INSERT INTO slack_apps (client_id, created_by_user_id) VALUES ('c1', 2)"))


@pytest.mark.parametrize(
    "stored, split",
    [
        (["channels:history,users:read"], ["channels:history", "users:read"]),
        (["channels:history, users:read"], ["channels:history", "users:read"]),
        (["channels:history", "users:read"], ["channels:history", "users:read"]),
        ([], []),
        (None, None),
    ],
)
def test_comma_joined_scopes_are_split(db_session, stored, split):
    module = load_migration()
    db = db_session.connection()
    db.execute(text("CREATE SCHEMA scopes_migration_test"))
    db.execute(text("SET LOCAL search_path TO scopes_migration_test"))
    db.execute(text("CREATE TABLE slack_user_credentials (id integer PRIMARY KEY, scopes text[])"))
    db.execute(text("INSERT INTO slack_user_credentials VALUES (1, :scopes)"), {"scopes": stored})

    run(db, module.split_slack_scopes)

    assert db.execute(text("SELECT scopes FROM slack_user_credentials")).scalar() == split
    db_session.rollback()
