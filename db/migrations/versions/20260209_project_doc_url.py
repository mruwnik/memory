"""Add doc_url column to projects table.

Revision ID: 20260209_project_doc_url
Revises: 20260208_report_allow_scripts
Create Date: 2026-02-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260209_project_doc_url"
down_revision: Union[str, None] = "20260208_report_allow_scripts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("doc_url", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "doc_url")
