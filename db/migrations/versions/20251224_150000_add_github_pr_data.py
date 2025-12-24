"""Add github_pr_data table for PR-specific data

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2025-12-24 15:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "github_pr_data",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("github_item_id", sa.BigInteger(), nullable=False),
        # Diff stored compressed with zlib
        sa.Column("diff_compressed", sa.LargeBinary(), nullable=True),
        # File changes as structured data
        # [{filename, status, additions, deletions, patch?}]
        sa.Column("files", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        # Stats
        sa.Column("additions", sa.Integer(), nullable=True),
        sa.Column("deletions", sa.Integer(), nullable=True),
        sa.Column("changed_files_count", sa.Integer(), nullable=True),
        # Reviews - [{user, state, body, submitted_at}]
        sa.Column("reviews", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        # Review comments (line-by-line code comments)
        # [{user, body, path, line, diff_hunk, created_at}]
        sa.Column(
            "review_comments", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["github_item_id"], ["github_item.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("github_item_id"),
    )
    op.create_index(
        "github_pr_data_item_idx", "github_pr_data", ["github_item_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("github_pr_data_item_idx", table_name="github_pr_data")
    op.drop_table("github_pr_data")
