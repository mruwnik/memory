"""Add user_id to email, github, and google accounts.

Revision ID: 20260107_180000
Revises: 20260107_150000_add_github_projects
Create Date: 2026-01-07
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260107_180000"
down_revision = "20260107_150000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Get the first user ID to assign existing accounts to
    connection = op.get_bind()
    result = connection.execute(sa.text("SELECT id FROM users ORDER BY id LIMIT 1"))
    first_user = result.fetchone()
    first_user_id = first_user[0] if first_user else None

    # Add user_id column to email_accounts
    op.add_column(
        "email_accounts",
        sa.Column("user_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_email_accounts_user_id",
        "email_accounts",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("email_accounts_user_idx", "email_accounts", ["user_id"])

    # Add user_id column to github_accounts
    op.add_column(
        "github_accounts",
        sa.Column("user_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_github_accounts_user_id",
        "github_accounts",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("github_accounts_user_idx", "github_accounts", ["user_id"])

    # Add user_id column to google_accounts
    op.add_column(
        "google_accounts",
        sa.Column("user_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_google_accounts_user_id",
        "google_accounts",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("google_accounts_user_idx", "google_accounts", ["user_id"])

    # Assign existing accounts to the first user and make column NOT NULL
    if first_user_id is not None:
        connection.execute(
            sa.text("UPDATE email_accounts SET user_id = :user_id WHERE user_id IS NULL"),
            {"user_id": first_user_id},
        )
        connection.execute(
            sa.text("UPDATE github_accounts SET user_id = :user_id WHERE user_id IS NULL"),
            {"user_id": first_user_id},
        )
        connection.execute(
            sa.text("UPDATE google_accounts SET user_id = :user_id WHERE user_id IS NULL"),
            {"user_id": first_user_id},
        )

    # Make user_id NOT NULL now that all existing records have been assigned
    op.alter_column("email_accounts", "user_id", nullable=False)
    op.alter_column("github_accounts", "user_id", nullable=False)
    op.alter_column("google_accounts", "user_id", nullable=False)


def downgrade() -> None:
    # Remove from google_accounts
    op.drop_index("google_accounts_user_idx", table_name="google_accounts")
    op.drop_constraint("fk_google_accounts_user_id", "google_accounts", type_="foreignkey")
    op.drop_column("google_accounts", "user_id")

    # Remove from github_accounts
    op.drop_index("github_accounts_user_idx", table_name="github_accounts")
    op.drop_constraint("fk_github_accounts_user_id", "github_accounts", type_="foreignkey")
    op.drop_column("github_accounts", "user_id")

    # Remove from email_accounts
    op.drop_index("email_accounts_user_idx", table_name="email_accounts")
    op.drop_constraint("fk_email_accounts_user_id", "email_accounts", type_="foreignkey")
    op.drop_column("email_accounts", "user_id")
