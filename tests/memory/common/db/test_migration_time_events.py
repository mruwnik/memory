"""Upgrading converts scheduled tasks: recurring ones into cron subscriptions,
pending one-offs into timers, once however often it runs."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

SCHEMA = "time_events_migration_test"
TABLES = [
    "CREATE TABLE users (id int PRIMARY KEY)",
    "CREATE TABLE api_keys (id int PRIMARY KEY)",
    "CREATE TABLE agent_definitions (id int PRIMARY KEY, user_id int)",
    "CREATE TABLE agent_triggers (id serial PRIMARY KEY, user_id int NOT NULL, "
    "name text NOT NULL, definition_id int, handler jsonb NOT NULL, kind varchar(20) NOT NULL, "
    "config jsonb NOT NULL, enabled boolean NOT NULL DEFAULT true, "
    "state jsonb NOT NULL DEFAULT '{}', api_key_id int, via_api_key boolean NOT NULL DEFAULT false, "
    "CONSTRAINT agent_triggers_kind_check CHECK (kind IN ('ingest')), "
    # As 20261210_trigger_subscriptions made it, so the upgrade's replacement is exercised.
    "CONSTRAINT agent_triggers_handler_check CHECK ("
    "CASE WHEN definition_id IS NOT NULL "
    "THEN handler = jsonb_build_object('agent', definition_id) "
    "ELSE ((jsonb_typeof(handler->'agent') = 'string' AND handler->>'agent' <> '' "
    "AND handler - 'agent' = '{}'::jsonb) "
    "OR (jsonb_typeof(handler->'queue') = 'string' AND handler->>'queue' <> '' "
    "AND handler - 'queue' = '{}'::jsonb)) IS TRUE END), "
    "CONSTRAINT agent_triggers_user_name_key UNIQUE (user_id, name))",
    "CREATE TABLE events (id bigserial PRIMARY KEY, kind text NOT NULL DEFAULT 'item', "
    "owner_id int, created_at timestamptz NOT NULL DEFAULT now(), routed_at timestamptz)",
    "CREATE TABLE scheduled_tasks (id varchar PRIMARY KEY, user_id int NOT NULL, "
    "task_type varchar(20) NOT NULL, topic text, message text, notification_channel varchar(20), "
    "notification_target varchar(255), data json, cron_expression varchar(100), "
    "next_scheduled_time timestamp)",
    "INSERT INTO users VALUES (1)",
    "INSERT INTO api_keys VALUES (7)",
    "INSERT INTO agent_definitions VALUES (3, 1), (4, 2)",
]
TASKS = (
    "INSERT INTO scheduled_tasks VALUES "
    # recurring message, set up through key 7
    """('daily', 1, 'notification', 'Daily', 'stand up', 'email', 'a@b.c', '{"api_key_id": 7}', """
    "'0 9 * * *', '2999-01-01 09:00'), "
    # paused recurring agent run
    """('digest', 1, 'agent_run', 'agent: d', NULL, NULL, NULL, '{"definition_id": 3, "input": "go"}', """
    "'0 8 * * 1', NULL), "
    # recurring message whose key is gone
    """('orphan', 1, 'notification', NULL, 'hi', 'slack', 'U1', '{"api_key_id": 99}', """
    "'0 7 * * *', '2999-01-01 07:00'), "
    # pending one-off reminder
    """('remind', 1, 'notification', 'Call', '**Call**', 'discord', '42', '{"discord_bot_id": 5}', """
    "NULL, '2999-01-02 10:00'), "
    # a fired one-off, and an agent run of someone else's definition: neither converts
    "('fired', 1, 'notification', NULL, 'x', 'email', 'a@b.c', NULL, NULL, NULL), "
    """('foreign', 1, 'agent_run', NULL, NULL, NULL, NULL, '{"definition_id": 4, "input": "x"}', """
    "'0 9 * * *', '2999-01-01 09:00')"
)


def load_migration():
    path = Path(__file__).parents[4] / "db/migrations/versions/20261213_time_events.py"
    spec = importlib.util.spec_from_file_location("time_events_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migrate(db, step):
    with Operations.context(MigrationContext.configure(db)):
        step()


def test_upgrade_converts_scheduled_tasks_once(db_session):
    module = load_migration()
    db = db_session.connection()
    db.execute(text(f"CREATE SCHEMA {SCHEMA}"))
    db.execute(text(f"SET LOCAL search_path TO {SCHEMA}"))
    for statement in TABLES + [TASKS]:
        db.execute(text(statement))

    migrate(db, module.upgrade)
    migrate(db, module.convert)
    triggers = db.execute(
        text(
            "SELECT name, kind, definition_id, handler, config, enabled, state ? 'tripped', "
            "api_key_id, via_api_key, next_at AT TIME ZONE 'UTC' FROM agent_triggers ORDER BY name"
        )
    ).all()
    timers = db.execute(
        text("SELECT kind, owner_id, fire_at AT TIME ZONE 'UTC', handler, data FROM events")
    ).all()
    db_session.rollback()

    assert [t[:3] for t in triggers] == [
        ("schedule-daily", "cron", None),
        ("schedule-digest", "cron", 3),
        ("schedule-orphan", "cron", None),
    ]
    daily, digest, orphan = triggers
    assert daily[3:9] == (
        {"notify": {"channel": "email", "target": "a@b.c", "message": "stand up", "subject": "Daily"}},
        {"cron": "0 9 * * *"},
        True, False, 7, True,
    )
    assert str(daily[9]) == "2999-01-01 09:00:00"
    assert digest[3:9] == ({"agent": 3}, {"cron": "0 8 * * 1", "input": "go"}, False, False, None, False)
    assert orphan[5:9] == (False, True, None, True)
    assert [(k, o, str(f), h, d) for k, o, f, h, d in timers] == [
        (
            "timer", 1, "2999-01-02 10:00:00",
            {"notify": {"channel": "discord", "target": "42", "message": "**Call**", "subject": "Call",
                        "discord_bot_id": 5}},
            {"via_api_key": False, "scheduled_task_id": "remind"},
        )
    ]
