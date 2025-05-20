"""Update field for chunks

Revision ID: d292d48ec74e
Revises: 4684845ca51e
Create Date: 2025-05-04 12:56:53.231393

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "d292d48ec74e"
down_revision: Union[str, None] = "4684845ca51e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chunk",
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )
    op.drop_column("misc_doc", "mime_type")


def downgrade() -> None:
    op.add_column(
        "misc_doc",
        sa.Column("mime_type", sa.TEXT(), autoincrement=False, nullable=True),
    )
    op.drop_column("chunk", "checked_at")
