"""Transcript accounts: turn off action item tasks, or limit them to chosen people

Revision ID: 20261223_transcript_task_options
Revises: 20261222_item_inserted_idx
Create Date: 2026-11-10
"""

import sqlalchemy as sa
from alembic import op

revision = "20261223_transcript_task_options"
down_revision = "20261222_item_inserted_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transcript_accounts",
        sa.Column("create_tasks", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_table(
        "transcript_account_task_people",
        sa.Column(
            "transcript_account_id",
            sa.BigInteger(),
            sa.ForeignKey("transcript_accounts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "person_id",
            sa.BigInteger(),
            sa.ForeignKey("people.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index(
        "transcript_account_task_people_person_idx",
        "transcript_account_task_people",
        ["person_id"],
    )


def downgrade() -> None:
    op.drop_index("transcript_account_task_people_person_idx", table_name="transcript_account_task_people")
    op.drop_table("transcript_account_task_people")
    op.drop_column("transcript_accounts", "create_tasks")
