"""Meetings record the calendar event occurrence they recorded.

Revision ID: 20261204_meeting_cal_event_uid
Revises: 20261203_agent_run_workers
Create Date: 2026-11-06
"""

import sqlalchemy as sa
from alembic import op

revision = "20261204_meeting_cal_event_uid"
down_revision = "20261203_agent_run_workers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("meeting", sa.Column("calendar_event_uid", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("meeting", "calendar_event_uid")
