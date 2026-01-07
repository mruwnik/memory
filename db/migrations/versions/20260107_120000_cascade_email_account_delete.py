"""Cascade delete mail messages when email account deleted.

Revision ID: 20260107_120000
Revises: 20260106_120000
Create Date: 2026-01-07

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260107_120000"
down_revision = "20260106_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the old foreign key constraint with SET NULL
    op.drop_constraint(
        "mail_message_email_account_id_fkey", "mail_message", type_="foreignkey"
    )

    # Recreate with CASCADE delete
    op.create_foreign_key(
        "mail_message_email_account_id_fkey",
        "mail_message",
        "email_accounts",
        ["email_account_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    # Drop the CASCADE constraint
    op.drop_constraint(
        "mail_message_email_account_id_fkey", "mail_message", type_="foreignkey"
    )

    # Recreate with SET NULL
    op.create_foreign_key(
        "mail_message_email_account_id_fkey",
        "mail_message",
        "email_accounts",
        ["email_account_id"],
        ["id"],
        ondelete="SET NULL",
    )
