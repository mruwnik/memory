"""Add people tracking

Revision ID: c9d0e1f2a3b4
Revises: b7c8d9e0f1a2
Create Date: 2025-12-24 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "people",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("identifier", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column(
            "aliases",
            postgresql.ARRAY(sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "contact_info",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["id"], ["source_item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("identifier"),
    )
    op.create_index("person_identifier_idx", "people", ["identifier"], unique=False)
    op.create_index("person_display_name_idx", "people", ["display_name"], unique=False)
    op.create_index(
        "person_aliases_idx", "people", ["aliases"], unique=False, postgresql_using="gin"
    )


def downgrade() -> None:
    op.drop_index("person_aliases_idx", table_name="people")
    op.drop_index("person_display_name_idx", table_name="people")
    op.drop_index("person_identifier_idx", table_name="people")
    op.drop_table("people")
