"""Configurable background sweep interval for event-enabled Slack workspaces."""
from alembic import op
import sqlalchemy as sa

revision = "20261109_slack_reconcile"
down_revision = "20261108_ssh_keys"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("slack_workspaces", sa.Column(
        "event_reconcile_interval_seconds", sa.Integer(),
        nullable=False, server_default="86400",
    ))


def downgrade():
    op.drop_column("slack_workspaces", "event_reconcile_interval_seconds")
