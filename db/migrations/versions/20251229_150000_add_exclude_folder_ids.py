"""Add exclude_folder_ids to google_folders

Revision ID: add_exclude_folder_ids
Revises: 20251229_120000_add_google_drive
Create Date: 2025-12-29 15:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "add_exclude_folder_ids"
down_revision: Union[str, None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "google_folders",
        sa.Column(
            "exclude_folder_ids",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("google_folders", "exclude_folder_ids")
