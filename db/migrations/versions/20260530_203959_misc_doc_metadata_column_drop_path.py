"""misc_doc metadata column, drop path

Revision ID: 549f6728ccfd
Revises: 20260529_st_enabled_derived
Create Date: 2026-05-30 20:39:59.444764

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '549f6728ccfd'
down_revision: Union[str, None] = '20260529_st_enabled_derived'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('misc_doc', sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.drop_column('misc_doc', 'path')


def downgrade() -> None:
    op.add_column('misc_doc', sa.Column('path', sa.TEXT(), autoincrement=False, nullable=True))
    op.drop_column('misc_doc', 'metadata') 
