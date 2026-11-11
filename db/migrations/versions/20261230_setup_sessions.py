"""Mark the sessions first-run setup signs in with.

Adds user_sessions.from_setup. A session with no OAuth client and no API key
counts as a web-UI login (which may create API keys) only when this is set;
agent runs mint sessions of the same shape. Sessions made by setup before this
release are not marked: creating an API key from one asks to sign in again.

Revision ID: 20261230_setup_sessions
Revises: 20261226_recurrence_cost
Create Date: 2026-11-11
"""

import sqlalchemy as sa
from alembic import op

revision = "20261230_setup_sessions"
down_revision = "20261226_recurrence_cost"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_sessions",
        sa.Column("from_setup", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("user_sessions", "from_setup")
