"""Store chat reactions per user, as {emoji: [user ids]}.

Slack messages stored Slack's list [{"name", "users", "count"}]; each name
keeps its users. Discord messages stored [{"emoji", "count"}] without users,
so those counts can't become per-user entries and are dropped; an entry that
did carry "users" keeps them. Reactions added from now on are recorded per user.
The downgrade restores the list shapes, with counts from the user lists.

Revision ID: 20261126_per_user_reactions
Revises: 20261125_chat_webhooks
Create Date: 2026-11-05
"""

from alembic import op

revision = "20261126_per_user_reactions"
down_revision = "20261125_chat_webhooks"
branch_labels = None
depends_on = None


def to_map(table: str, key: str, users: str) -> str:
    return f"""
        UPDATE {table} SET reactions = (
            SELECT COALESCE(jsonb_object_agg(r->>'{key}', {users}), '{{}}'::jsonb)
            FROM jsonb_array_elements(reactions) r
            WHERE r->>'{key}' IS NOT NULL AND jsonb_typeof(r->'users') = 'array'
              AND jsonb_array_length(r->'users') > 0
        )
        WHERE jsonb_typeof(reactions) = 'array'
    """


def to_list(table: str, key: str) -> str:
    return f"""
        UPDATE {table} SET reactions = (
            SELECT COALESCE(
                jsonb_agg(jsonb_build_object(
                    '{key}', e.key, 'users', e.value, 'count', jsonb_array_length(e.value)
                )),
                '[]'::jsonb
            )
            FROM jsonb_each(reactions) e
        )
        WHERE jsonb_typeof(reactions) = 'object'
    """


STRING_USERS = "(SELECT jsonb_agg(u #>> '{}') FROM jsonb_array_elements(r->'users') u)"


def upgrade() -> None:
    op.execute(to_map("slack_message", "name", f"COALESCE({STRING_USERS}, '[]'::jsonb)"))
    op.execute(to_map("discord_message", "emoji", f"COALESCE({STRING_USERS}, '[]'::jsonb)"))


def downgrade() -> None:
    op.execute(to_list("slack_message", "name"))
    op.execute(to_list("discord_message", "emoji"))
