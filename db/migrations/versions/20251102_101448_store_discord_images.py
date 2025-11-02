"""store discord images

Revision ID: 1954477b25f4
Revises: 2024235e37e7
Create Date: 2025-11-02 10:14:48.334934

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "1954477b25f4"
down_revision: Union[str, None] = "2024235e37e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "discord_message", sa.Column("images", sa.ARRAY(sa.Text()), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("discord_message", "images")
