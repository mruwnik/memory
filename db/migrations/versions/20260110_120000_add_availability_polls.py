"""Add availability polls tables for meeting scheduling.

Creates tables for LettuceMeet-style availability polling:
- availability_polls: Main poll configuration
- poll_responses: Respondent submissions
- poll_availabilities: Individual time slot selections

Revision ID: 20260110_120000
Revises: 20260109_150000
Create Date: 2026-01-10

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260110_120000"
down_revision = "20260109_150000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create availability_polls table
    op.create_table(
        "availability_polls",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("slug", sa.String(20), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        # Poll time window (stored in UTC)
        sa.Column("datetime_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("datetime_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "slot_duration_minutes", sa.Integer(), nullable=False, server_default="30"
        ),
        # Creator
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("closes_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_time", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("slug"),
    )

    # Note: slug already has a unique index from UniqueConstraint
    op.create_index("idx_polls_user_id", "availability_polls", ["user_id"])
    op.create_index("idx_polls_status", "availability_polls", ["status"])
    op.create_index("idx_polls_created_at", "availability_polls", ["created_at"])

    # Create poll_responses table
    op.create_table(
        "poll_responses",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("poll_id", sa.BigInteger(), nullable=False),
        sa.Column("respondent_name", sa.String(255), nullable=True),
        sa.Column("respondent_email", sa.String(255), nullable=True),
        sa.Column("person_id", sa.BigInteger(), nullable=True),
        sa.Column("edit_token", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["poll_id"], ["availability_polls.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="SET NULL"),
    )

    op.create_index("idx_poll_responses_poll_id", "poll_responses", ["poll_id"])
    op.create_index("idx_poll_responses_edit_token", "poll_responses", ["edit_token"])
    op.create_index("idx_poll_responses_person_id", "poll_responses", ["person_id"])

    # Create poll_availabilities table
    op.create_table(
        "poll_availabilities",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("response_id", sa.BigInteger(), nullable=False),
        sa.Column("slot_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("slot_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "availability_level", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["response_id"], ["poll_responses.id"], ondelete="CASCADE"
        ),
    )

    op.create_index(
        "idx_poll_availability_response_id", "poll_availabilities", ["response_id"]
    )
    op.create_index(
        "idx_poll_availability_slots",
        "poll_availabilities",
        ["slot_start", "slot_end"],
    )


def downgrade() -> None:
    op.drop_table("poll_availabilities")
    op.drop_table("poll_responses")
    op.drop_table("availability_polls")
