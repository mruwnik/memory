"""Add journal_entries table.

Revision ID: 20260204_journal_entries
Revises: 20260203_project_owner
Create Date: 2026-02-04

Supports journal entries on:
- source_item: Any SourceItem (notes, books, emails, etc.)
- project: Project entities
- team: Team entities
- poll: AvailabilityPoll entities
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260204_journal_entries"
down_revision: Union[str, None] = "20260203_project_owner"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "journal_entries",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        # Polymorphic target: target_type + target_id identify the entity
        sa.Column("target_type", sa.String(50), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("creator_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        # project_id for access control (inherited from target or set directly)
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("private", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    # Composite index for looking up entries by target
    op.create_index("journal_entries_target_idx", "journal_entries", ["target_type", "target_id"])
    op.create_index("journal_entries_creator_idx", "journal_entries", ["creator_id"])
    op.create_index("journal_entries_project_idx", "journal_entries", ["project_id"])
    op.create_index("journal_entries_created_idx", "journal_entries", ["created_at"])
    # Check constraint to ensure target_type is valid
    op.create_check_constraint(
        "journal_entries_target_type_check",
        "journal_entries",
        "target_type IN ('source_item', 'project', 'team', 'poll')"
    )


def downgrade() -> None:
    op.drop_constraint("journal_entries_target_type_check", "journal_entries", type_="check")
    op.drop_index("journal_entries_created_idx", table_name="journal_entries")
    op.drop_index("journal_entries_project_idx", table_name="journal_entries")
    op.drop_index("journal_entries_creator_idx", table_name="journal_entries")
    op.drop_index("journal_entries_target_idx", table_name="journal_entries")
    op.drop_table("journal_entries")
