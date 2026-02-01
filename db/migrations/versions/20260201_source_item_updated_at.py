"""Add updated_at to source_item.

This migration adds an updated_at column to source_item that automatically
updates on row modification. Useful for detecting stuck processing jobs
and tracking when items were last modified.

Revision ID: 20260201_source_item_updated_at
Revises: 20260201_encrypt_google_credentials
Create Date: 2026-02-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260201_source_item_updated_at"
down_revision: Union[str, None] = "20260201_encrypt_google_credentials"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "source_item",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
    )
    # Backfill existing rows with inserted_at value
    op.execute("UPDATE source_item SET updated_at = COALESCE(inserted_at, NOW())")


def downgrade() -> None:
    op.drop_column("source_item", "updated_at")
