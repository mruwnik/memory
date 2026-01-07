"""Add meeting table and meeting_attendees association table.

Revision ID: 20260106_120000
Revises: 20260103_150000
Create Date: 2026-01-06

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260106_120000"
down_revision = "20260103_150000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create meeting table
    op.create_table(
        "meeting",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("meeting_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("source_tool", sa.Text(), nullable=True),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("calendar_event_id", sa.BigInteger(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "extraction_status", sa.Text(), server_default="pending", nullable=False
        ),
        sa.ForeignKeyConstraint(["id"], ["source_item.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["calendar_event_id"], ["calendar_event.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("meeting_date_idx", "meeting", ["meeting_date"], unique=False)
    op.create_index("meeting_source_tool_idx", "meeting", ["source_tool"], unique=False)
    op.create_index(
        "meeting_external_id_idx", "meeting", ["external_id"], unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    op.create_index(
        "meeting_extraction_status_idx", "meeting", ["extraction_status"], unique=False
    )
    op.create_index(
        "meeting_calendar_event_idx", "meeting", ["calendar_event_id"], unique=False
    )

    # Create meeting_attendees association table
    op.create_table(
        "meeting_attendees",
        sa.Column("meeting_id", sa.BigInteger(), nullable=False),
        sa.Column("person_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["meeting_id"], ["meeting.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("meeting_id", "person_id"),
    )
    op.create_index(
        "meeting_attendees_meeting_idx",
        "meeting_attendees",
        ["meeting_id"],
        unique=False,
    )
    op.create_index(
        "meeting_attendees_person_idx",
        "meeting_attendees",
        ["person_id"],
        unique=False,
    )


def downgrade() -> None:
    # Drop meeting_attendees
    op.drop_index("meeting_attendees_person_idx", table_name="meeting_attendees")
    op.drop_index("meeting_attendees_meeting_idx", table_name="meeting_attendees")
    op.drop_table("meeting_attendees")

    # Drop meeting
    op.drop_index("meeting_calendar_event_idx", table_name="meeting")
    op.drop_index("meeting_extraction_status_idx", table_name="meeting")
    op.drop_index("meeting_external_id_idx", table_name="meeting")
    op.drop_index("meeting_source_tool_idx", table_name="meeting")
    op.drop_index("meeting_date_idx", table_name="meeting")
    op.drop_table("meeting")
