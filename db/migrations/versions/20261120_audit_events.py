"""Move audit records out of telemetry_events into audit_events.

Audit rows lived beside OTLP data, where a user's telemetry upload could forge
them and deleting the user deleted them. audit_events keeps the actor's email
so a row outlives its user. Only membership-snapshot rows (no longer written)
had a `source` worth keeping; it moves into their attributes. Audit-named rows
that look client-written (an API key, session or body set) are dropped.
Downgrade drops audit rows whose actor has been deleted.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261120_audit_events"
down_revision = "20261119_email_sender_tags"
branch_labels = None
depends_on = None

SNAPSHOT = "team.external_membership_snapshot"
# OTLP uploads could forge audit names; the server's writes never set these.
SERVER_WRITTEN = "t.api_key_id IS NULL AND t.session_id IS NULL AND t.body IS NULL"
AUDIT_NAMES = f"('mcp.call', 'settings.update', 'settings.restart', '{SNAPSHOT}')"


def upgrade():
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("actor_email", sa.String(), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target", sa.String(200), nullable=True),
        sa.Column("attributes", JSONB(), server_default="{}", nullable=False),
    )
    op.create_index("idx_audit_events_timestamp", "audit_events", ["timestamp"])
    op.create_index("idx_audit_events_action_ts", "audit_events", ["action", "timestamp"])
    op.create_index("idx_audit_events_actor_ts", "audit_events", ["actor_id", "timestamp"])
    op.execute(
        f"""
        INSERT INTO audit_events (timestamp, actor_id, actor_email, action, target, attributes)
        SELECT t.timestamp, u.id, u.email, t.name, t.tool_name,
            CASE WHEN t.name = '{SNAPSHOT}'
                 THEN t.attributes || jsonb_strip_nulls(jsonb_build_object('source', t.source))
                 ELSE t.attributes END
        FROM telemetry_events t LEFT JOIN users u ON u.id = t.user_id
        WHERE t.name IN {AUDIT_NAMES} AND {SERVER_WRITTEN}
        ORDER BY t.id
        """
    )
    op.execute(f"DELETE FROM telemetry_events WHERE name IN {AUDIT_NAMES}")


def downgrade():
    op.execute(
        f"""
        INSERT INTO telemetry_events (timestamp, user_id, event_type, name, source, tool_name, attributes)
        SELECT timestamp, actor_id, 'log', action,
            CASE WHEN action LIKE 'settings.%' THEN 'settings'
                 WHEN action = '{SNAPSHOT}' THEN attributes->>'source' END,
            left(target, 100),
            CASE WHEN action = '{SNAPSHOT}' THEN attributes - 'source' ELSE attributes END
        FROM audit_events WHERE actor_id IS NOT NULL
        ORDER BY id
        """
    )
    op.drop_table("audit_events")
