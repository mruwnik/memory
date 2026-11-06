"""Meetings get their size and calendar event; ones left unstored are rerun.

Revision ID: 20261205_meeting_backfill
Revises: 20261204_meeting_cal_event_uid
Create Date: 2026-11-06
"""

from alembic import op

revision = "20261205_meeting_backfill"
down_revision = "20261204_meeting_cal_event_uid"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE source_item SET size = octet_length(source_item.content)
        FROM meeting
        WHERE meeting.id = source_item.id AND source_item.size IS NULL AND source_item.content IS NOT NULL
        """
    )
    # The owner's event with the meeting's calendar uid, nearest the meeting
    # when a recurring event's occurrences share the uid.
    op.execute(
        """
        UPDATE meeting SET calendar_event_id = linked.event_id
        FROM (
            SELECT DISTINCT ON (m.id) m.id AS meeting_id, ce.id AS event_id
            FROM meeting m
            JOIN source_item si ON si.id = m.id
            LEFT JOIN transcript_accounts ta ON ta.id = m.transcript_account_id
            JOIN calendar_event ce ON ce.external_id = m.calendar_event_uid
            JOIN calendar_accounts ca
                ON ca.id = ce.calendar_account_id AND ca.user_id = coalesce(si.creator_id, ta.user_id)
            WHERE m.calendar_event_id IS NULL
            ORDER BY m.id, abs(extract(epoch FROM ce.start_time - coalesce(m.meeting_date, ce.start_time)))
        ) linked
        WHERE meeting.id = linked.meeting_id
        """
    )
    # "extracted" was also where a worker lost mid-embed left a meeting; the
    # hourly cleanup_stuck_meetings reruns "processing" ones once.
    op.execute("UPDATE meeting SET extraction_status = 'processing' WHERE extraction_status = 'extracted'")


def downgrade() -> None:
    pass
