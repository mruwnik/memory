"""Time is an event: cron subscriptions (agent_triggers of kind 'cron', with a
next_at) write tick events, timers are events with a fire time and a handler, and
a notify handler sends a message.

Scheduled tasks convert, leaving their tables as they are: recurring ones become
cron subscriptions named "schedule-<task id>", pending one-offs become timers.
Notification tasks get a notify handler; agent_run tasks keep their definition
(those whose definition is gone are left out, as they could never run). A task
set up through an API key that no longer exists converts disabled and tripped.
Running it again converts only what it hasn't yet.

Revision ID: 20261213_time_events
Revises: 20261213_trigger_loop_filters
Create Date: 2026-11-07
"""

from alembic import op

revision = "20261213_time_events"
down_revision = "20261213_trigger_loop_filters"
branch_labels = None
depends_on = None

HANDLER_CHECK = (
    "CASE WHEN definition_id IS NOT NULL "
    "THEN handler = jsonb_build_object('agent', definition_id) "
    "ELSE ((jsonb_typeof(handler->'agent') = 'string' AND handler->>'agent' <> '' "
    "AND handler - 'agent' = '{}'::jsonb) "
    "OR (jsonb_typeof(handler->'queue') = 'string' AND handler->>'queue' <> '' "
    "AND handler - 'queue' = '{}'::jsonb)NOTIFY) IS TRUE END"
)
NOTIFY_CHECK = (
    " OR (jsonb_typeof(handler->'notify') = 'object' AND handler - 'notify' = '{}'::jsonb)"
)

# One row per scheduled task: its handler (or NULL: it can't convert), definition
# and key binding.
TASKS = """
SELECT st.id, st.user_id, st.cron_expression, st.next_scheduled_time,
    st.data::jsonb AS data, d.id AS definition_id,
    CASE
        WHEN st.task_type = 'agent_run' AND d.id IS NOT NULL
            THEN jsonb_build_object('agent', d.id)
        WHEN st.task_type = 'notification' AND st.notification_channel IS NOT NULL
            AND st.notification_target IS NOT NULL AND st.message IS NOT NULL
            THEN jsonb_build_object('notify', jsonb_strip_nulls(jsonb_build_object(
                'channel', st.notification_channel,
                'target', st.notification_target,
                'message', st.message,
                'subject', st.topic,
                'discord_bot_id', st.data::jsonb->'discord_bot_id',
                'from_address', st.data::jsonb->'from_address')))
    END AS handler,
    k.id AS api_key_id,
    st.data::jsonb->>'api_key_id' IS NOT NULL AS via_api_key
FROM scheduled_tasks st
LEFT JOIN agent_definitions d
    ON st.task_type = 'agent_run' AND d.user_id = st.user_id
    AND d.id::text = st.data::jsonb->>'definition_id'
LEFT JOIN api_keys k ON k.id::text = st.data::jsonb->>'api_key_id'
"""


def convert() -> None:
    op.execute(
        "INSERT INTO agent_triggers (user_id, name, definition_id, handler, kind, config, "
        "enabled, state, api_key_id, via_api_key, next_at) "
        "SELECT user_id, 'schedule-' || id, definition_id, handler, 'cron', "
        "jsonb_strip_nulls(jsonb_build_object('cron', cron_expression, "
        "'input', data->>'input')), "
        "next_scheduled_time IS NOT NULL AND (api_key_id IS NOT NULL OR NOT via_api_key), "
        "CASE WHEN via_api_key AND api_key_id IS NULL THEN jsonb_build_object('tripped', "
        "jsonb_build_object('reason', 'the API key that set this up was deleted', "
        "'at', now())) ELSE '{}'::jsonb END, "
        "api_key_id, via_api_key, next_scheduled_time AT TIME ZONE 'UTC' "
        f"FROM ({TASKS}) t "
        "WHERE cron_expression IS NOT NULL AND handler IS NOT NULL "
        "ON CONFLICT ON CONSTRAINT agent_triggers_user_name_key DO NOTHING"
    )
    op.execute(
        "INSERT INTO events (kind, owner_id, fire_at, handler, data) "
        "SELECT 'timer', user_id, next_scheduled_time AT TIME ZONE 'UTC', handler, "
        "jsonb_strip_nulls(jsonb_build_object('input', data->>'input', "
        "'api_key_id', api_key_id, 'via_api_key', via_api_key, 'scheduled_task_id', id)) "
        f"FROM ({TASKS}) t "
        "WHERE cron_expression IS NULL AND next_scheduled_time IS NOT NULL "
        "AND handler IS NOT NULL AND NOT EXISTS (SELECT 1 FROM events e "
        "WHERE e.kind = 'timer' AND e.data->>'scheduled_task_id' = t.id)"
    )


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agent_triggers "
        "ADD COLUMN next_at TIMESTAMPTZ, "
        "DROP CONSTRAINT agent_triggers_kind_check, "
        "ADD CONSTRAINT agent_triggers_kind_check CHECK (kind IN ('ingest', 'cron')), "
        "DROP CONSTRAINT agent_triggers_handler_check, "
        "ADD CONSTRAINT agent_triggers_handler_check CHECK ("
        + HANDLER_CHECK.replace("NOTIFY", NOTIFY_CHECK)
        + ")"
    )
    op.execute(
        "CREATE INDEX agent_triggers_cron_due_idx ON agent_triggers (next_at) "
        "WHERE kind = 'cron' AND enabled"
    )
    op.execute(
        "ALTER TABLE events "
        "ADD COLUMN trigger_id INTEGER REFERENCES agent_triggers(id) ON DELETE CASCADE, "
        "ADD COLUMN fire_at TIMESTAMPTZ, "
        "ADD COLUMN handler JSONB, "
        "ADD COLUMN data JSONB, "
        "ADD COLUMN result JSONB, "
        "ADD COLUMN dispatched_at TIMESTAMPTZ, "
        "ADD CONSTRAINT events_tick_check CHECK (kind <> 'tick' OR (trigger_id IS NOT NULL "
        "AND owner_id IS NOT NULL AND fire_at IS NOT NULL)), "
        "ADD CONSTRAINT events_timer_check CHECK (kind <> 'timer' OR (owner_id IS NOT NULL "
        "AND fire_at IS NOT NULL AND handler IS NOT NULL))"
    )
    op.execute(
        "CREATE UNIQUE INDEX events_tick_key ON events (trigger_id, fire_at) "
        "WHERE kind = 'tick'"
    )
    op.execute(
        "CREATE INDEX events_time_due_idx ON events (fire_at) "
        "WHERE routed_at IS NULL AND kind IN ('tick', 'timer')"
    )
    op.execute(
        "CREATE INDEX events_owner_timers_idx ON events (owner_id, fire_at) "
        "WHERE kind = 'timer'"
    )
    convert()


def downgrade() -> None:
    op.execute("DELETE FROM events WHERE kind IN ('tick', 'timer')")
    op.execute("DROP INDEX events_owner_timers_idx")
    op.execute("DROP INDEX events_time_due_idx")
    op.execute("DROP INDEX events_tick_key")
    op.execute(
        "ALTER TABLE events "
        "DROP CONSTRAINT events_timer_check, DROP CONSTRAINT events_tick_check, "
        "DROP COLUMN dispatched_at, DROP COLUMN result, DROP COLUMN data, "
        "DROP COLUMN handler, DROP COLUMN fire_at, "
        "DROP COLUMN trigger_id"
    )
    op.execute("DELETE FROM agent_triggers WHERE kind = 'cron' OR handler ? 'notify'")
    op.execute("DROP INDEX agent_triggers_cron_due_idx")
    op.execute(
        "ALTER TABLE agent_triggers "
        "DROP CONSTRAINT agent_triggers_handler_check, "
        "ADD CONSTRAINT agent_triggers_handler_check CHECK ("
        + HANDLER_CHECK.replace("NOTIFY", "")
        + "), "
        "DROP CONSTRAINT agent_triggers_kind_check, "
        "ADD CONSTRAINT agent_triggers_kind_check CHECK (kind IN ('ingest')), "
        "DROP COLUMN next_at"
    )
