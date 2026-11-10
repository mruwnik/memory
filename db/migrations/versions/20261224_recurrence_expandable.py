"""Remember whether a calendar event's recurrence rule yields any occurrence.

Adds calendar_event.recurrence_expandable, filled by the calendar sync, so
queries skip rules no date satisfies instead of expanding them to year 9999.
Existing rows stay NULL and expand as before until a sync checks them.

Revision ID: 20261224_recurrence_expandable
Revises: 20261223_gmail_declined_ids
Create Date: 2026-11-10
"""

import sqlalchemy as sa
from alembic import op

revision = "20261224_recurrence_expandable"
down_revision = "20261223_gmail_declined_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "calendar_event",
        sa.Column("recurrence_expandable", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("calendar_event", "recurrence_expandable")
