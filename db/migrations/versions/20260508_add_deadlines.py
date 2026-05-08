"""Add deadlines table + deadline_attachments junction.

Deadlines aggregate SourceItems under a single prepare-by date — distinct
from Task (which answers *what to do*) and CalendarEvent (imported events).

Revision ID: 20260508_add_deadlines
Revises: 20260508_normalize_filename
Create Date: 2026-05-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# alembic_version.version_num is varchar(32); keep this id ≤32 chars.
revision: str = "20260508_add_deadlines"
down_revision: Union[str, None] = "20260508_normalize_filename"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "deadlines",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("priority", sa.Text(), nullable=True),
        sa.Column(
            "owner_id",
            sa.BigInteger(),
            sa.ForeignKey("people.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # AccessControlMixin columns
        sa.Column(
            "project_id",
            sa.BigInteger(),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "sensitivity",
            sa.String(20),
            nullable=False,
            server_default="basic",
        ),
        sa.Column(
            "creator_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "sensitivity IN ('public', 'basic', 'internal', 'confidential')",
            name="deadline_valid_sensitivity_level",
        ),
        sa.CheckConstraint(
            "priority IS NULL OR priority IN ('low', 'medium', 'high', 'urgent')",
            name="deadline_priority_check",
        ),
    )
    op.create_index("deadline_date_idx", "deadlines", ["date"])
    op.create_index("deadline_priority_idx", "deadlines", ["priority"])
    op.create_index("deadline_owner_idx", "deadlines", ["owner_id"])
    op.create_index("deadline_project_idx", "deadlines", ["project_id"])
    op.create_index("deadline_sensitivity_idx", "deadlines", ["sensitivity"])
    op.create_index("deadline_creator_idx", "deadlines", ["creator_id"])
    op.create_index(
        "deadline_tags_idx", "deadlines", ["tags"], postgresql_using="gin"
    )

    op.create_table(
        "deadline_attachments",
        sa.Column(
            "deadline_id",
            sa.BigInteger(),
            sa.ForeignKey("deadlines.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "source_item_id",
            sa.BigInteger(),
            sa.ForeignKey("source_item.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "attached_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "deadline_attachments_deadline_idx",
        "deadline_attachments",
        ["deadline_id"],
    )
    op.create_index(
        "deadline_attachments_source_idx",
        "deadline_attachments",
        ["source_item_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "deadline_attachments_source_idx", table_name="deadline_attachments"
    )
    op.drop_index(
        "deadline_attachments_deadline_idx", table_name="deadline_attachments"
    )
    op.drop_table("deadline_attachments")

    op.drop_index("deadline_tags_idx", table_name="deadlines")
    op.drop_index("deadline_creator_idx", table_name="deadlines")
    op.drop_index("deadline_sensitivity_idx", table_name="deadlines")
    op.drop_index("deadline_project_idx", table_name="deadlines")
    op.drop_index("deadline_owner_idx", table_name="deadlines")
    op.drop_index("deadline_priority_idx", table_name="deadlines")
    op.drop_index("deadline_date_idx", table_name="deadlines")
    op.drop_table("deadlines")
