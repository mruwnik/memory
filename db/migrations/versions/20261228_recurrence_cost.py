"""Remember what expanding a calendar event's recurrence rule costs.

Adds calendar_event.recurrence_cost_ms, filled by the calendar sync, so queries
expand each account's cheapest rules first. The sync now also stores a rule
whose expansion is over the per-event budget as not expandable. Rows checked
before this release are checked again on their next sync; until then they keep
their stored verdict and expand after the checked rows of their account.

Revision ID: 20261228_recurrence_cost
Revises: 20261227_unbind_github_projects
Create Date: 2026-11-11
"""

import sqlalchemy as sa
from alembic import op

revision = "20261228_recurrence_cost"
down_revision = "20261227_unbind_github_projects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "calendar_event",
        sa.Column("recurrence_cost_ms", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("calendar_event", "recurrence_cost_ms")
