"""Add note

Revision ID: ba301527a2eb
Revises: 6554eb260176
Create Date: 2025-06-02 10:38:20.112303

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "ba301527a2eb"
down_revision: Union[str, None] = "6554eb260176"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notes",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("note_type", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(precision=3, scale=2), nullable=False),
        sa.ForeignKeyConstraint(["id"], ["source_item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("note_confidence_idx", "notes", ["confidence"], unique=False)
    op.create_index("note_subject_idx", "notes", ["subject"], unique=False)
    op.create_index("note_type_idx", "notes", ["note_type"], unique=False)


def downgrade() -> None:
    op.drop_index("note_type_idx", table_name="notes")
    op.drop_index("note_subject_idx", table_name="notes")
    op.drop_index("note_confidence_idx", table_name="notes")
    op.drop_table("notes")
