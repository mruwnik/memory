"""Add email deletion tracking columns.

Revision ID: 20260103_120000
Revises: 20260102_150000
Create Date: 2026-01-03

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260103_120000"
down_revision = "20260102_150000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add columns to mail_message table for deletion tracking
    op.add_column(
        "mail_message",
        sa.Column(
            "email_account_id",
            sa.BigInteger(),
            sa.ForeignKey("email_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "mail_message",
        sa.Column("imap_uid", sa.Text(), nullable=True),
    )

    # Add indexes
    op.create_index("mail_account_idx", "mail_message", ["email_account_id"])
    op.create_index(
        "mail_imap_uid_idx",
        "mail_message",
        ["email_account_id", "folder", "imap_uid"],
    )


def downgrade() -> None:
    # Remove indexes
    op.drop_index("mail_imap_uid_idx", table_name="mail_message")
    op.drop_index("mail_account_idx", table_name="mail_message")

    # Remove columns
    op.drop_column("mail_message", "imap_uid")
    op.drop_column("mail_message", "email_account_id")
