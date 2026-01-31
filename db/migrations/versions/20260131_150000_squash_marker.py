"""Squash marker - no-op migration for existing databases.

This migration does nothing. It exists so that existing databases (which already
have this revision applied) continue to work after the migration squash.

Fresh databases get the complete schema from 20260131_000000_complete_schema.py,
then this stub runs (doing nothing).

Revision ID: 20260131_150000
Revises: 20260131_000000
Create Date: 2026-01-31

"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "20260131_150000"
down_revision: Union[str, None] = "20260131_000000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op - schema already exists from complete_schema migration
    pass


def downgrade() -> None:
    # No-op - can't undo the squash
    pass
