"""Add allowed_connect_urls column to reports table for CSP connect-src control.

Revision ID: 20260216_report_connect_urls
Revises: 20260209_project_doc_url
Create Date: 2026-02-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260216_report_connect_urls"
down_revision: Union[str, None] = "20260209_project_doc_url"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "reports",
        sa.Column(
            "allowed_connect_urls",
            postgresql.ARRAY(sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("reports", "allowed_connect_urls")
