"""The scheduler MCP server is now the subscription server: a stored list of hidden
servers naming `scheduler` names `subscription` instead, or the list no longer
decodes and every hidden server shows.

Revision ID: 20261214_subscription_server
Revises: 20261213_time_events
Create Date: 2026-11-07
"""

from alembic import op

revision = "20261214_subscription_server"
down_revision = "20261213_time_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE instance_settings SET value = (
            SELECT COALESCE(jsonb_agg(DISTINCT name), '[]'::jsonb)
            FROM (
                SELECT CASE WHEN v = 'scheduler' THEN 'subscription' ELSE v END AS name
                FROM jsonb_array_elements_text(value) AS v
            ) names
        )
        WHERE key = 'disabled_mcp_servers'
          AND jsonb_typeof(value) = 'array'
          AND value ? 'scheduler'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE instance_settings SET value = (
            SELECT COALESCE(jsonb_agg(DISTINCT name), '[]'::jsonb)
            FROM (
                SELECT CASE WHEN v = 'subscription' THEN 'scheduler' ELSE v END AS name
                FROM jsonb_array_elements_text(value) AS v
            ) names
        )
        WHERE key = 'disabled_mcp_servers'
          AND jsonb_typeof(value) = 'array'
          AND value ? 'subscription'
        """
    )
