"""Add user_id ownership column to calendar_accounts.

Closes the IDOR vulnerability where any authenticated user could
read/update/delete any user's calendar account: ownership for Gmail-backed
records was only inferable via google_account.user_id, and CalDAV records
had no ownership tracking at all.

Backfills user_id from google_accounts.user_id for Gmail-linked accounts.
CalDAV accounts pre-dating this migration are left with NULL user_id; they
become admin-only (via get_user_account) until an admin reassigns them, which
is the secure default.

Revision ID: 20260507_calendar_account_user_id
Revises: 20260506_transcript_accounts
Create Date: 2026-05-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260507_calendar_account_user_id"
down_revision: Union[str, None] = "20260506_transcript_accounts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add nullable user_id column with FK to users.
    # Kept nullable so legacy CalDAV rows (no inferable owner) survive the
    # migration; the application sets user_id on every new row going forward.
    op.add_column(
        "calendar_accounts",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index(
        "calendar_accounts_user_idx", "calendar_accounts", ["user_id"]
    )

    # Backfill: Gmail-linked rows inherit ownership from google_accounts.user_id.
    op.execute(
        """
        UPDATE calendar_accounts ca
           SET user_id = ga.user_id
          FROM google_accounts ga
         WHERE ca.google_account_id = ga.id
           AND ca.user_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("calendar_accounts_user_idx", table_name="calendar_accounts")
    op.drop_column("calendar_accounts", "user_id")
