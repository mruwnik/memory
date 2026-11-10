"""Remember Gmail messages a sync fetched but did not store.

Adds email_accounts.gmail_declined_ids so Gmail syncs stop re-downloading
duplicates and other mail they never store.

Revision ID: 20261223_gmail_declined_ids
Revises: 20261222_item_inserted_idx
Create Date: 2026-11-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261223_gmail_declined_ids"
down_revision = "20261222_item_inserted_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "email_accounts",
        sa.Column(
            "gmail_declined_ids",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("email_accounts", "gmail_declined_ids")
