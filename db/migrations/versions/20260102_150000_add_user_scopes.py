"""Add scopes column to users table for MCP tool access control

Revision ID: i5d6e7f8g9h0
Revises: h4c5d6e7f8g9
Create Date: 2026-01-02 15:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY


# revision identifiers, used by Alembic.
revision: str = "i5d6e7f8g9h0"
down_revision: Union[str, None] = "h4c5d6e7f8g9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add scopes column with default ["*"] for existing users (full access)
    op.add_column(
        "users",
        sa.Column(
            "scopes",
            ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
    )
    # Set existing users to have full access
    op.execute("UPDATE users SET scopes = ARRAY['*']")


def downgrade() -> None:
    op.drop_column("users", "scopes")
