"""Add email deletion tracking columns.

Revision ID: 20260103_120000
Revises: i5d6e7f8g9h0
Create Date: 2026-01-03

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260103_120000"
down_revision = "i5d6e7f8g9h0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add columns to mail_message table for deletion tracking
    op.add_column(
        "mail_message",
        sa.Column(
            "email_account_id",
            sa.BigInteger(),
            nullable=True,
        ),
    )
    op.add_column(
        "mail_message",
        sa.Column("imap_uid", sa.Text(), nullable=True),
    )

    # Add foreign key constraint
    op.create_foreign_key(
        "mail_message_email_account_id_fkey",
        "mail_message",
        "email_accounts",
        ["email_account_id"],
        ["id"],
        ondelete="SET NULL",
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

    # Remove foreign key
    op.drop_constraint(
        "mail_message_email_account_id_fkey", "mail_message", type_="foreignkey"
    )

    # Remove columns
    op.drop_column("mail_message", "imap_uid")
    op.drop_column("mail_message", "email_account_id")
